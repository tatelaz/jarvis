"""Il custode della memoria: a conversazione finita rilegge gli scambi e aggiorna i ricordi sull'utente.

Prima di lui la memoria si riempiva solo se il cervello si ricordava di usare `ricorda` (al 7/10: 0 fatti in
187 frasi). Il custode è un cervello a parte, usato una volta e poi chiuso: riceve le frasi nuove e i ricordi
già presenti che c'entrano (con il loro numero) e risponde con operazioni in JSON (aggiungi / aggiorna /
elimina). Il codice le controlla prima di applicarle:
- aggiorna ed elimina valgono solo per i ricordi che gli sono stati mostrati;
- mai password, PIN, codici, parole d'ordine, numeri di carte o conti;
- al massimo MAX_OPS operazioni per volta, testi brevi, importanza 1-3.
Il punto fin dove ha letto si salva solo dopo un lavoro riuscito: se il cervello non risponde si riprova dopo.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .store import Memory

log = logging.getLogger("jarvis.memory.keeper")

CHECKPOINT = "custode_ultimo_turno"
MAX_OPS = 12
MAX_TURNS = 80  # frasi per giro: un arretrato lungo si smaltisce in più giri
CATEGORIES = {"preferenze", "persone", "progetti", "pc", "abitudini", "salute", "lavoro", "generale"}
SECRET = re.compile(r"password|passw|parola d.ordine|\bpin\b|\biban\b|\bcvv\b|codice (segreto|di sicurezza|fiscale)|"
                    r"numero (della |di )?carta|credenziali|\b\d{12,}\b", re.I)

SYSTEM = """Sei il custode della memoria di Jarvis, l'assistente personale di {user}. Ricevi una conversazione appena
finita tra {user} e Jarvis e aggiorni i ricordi a lungo termine su {user}.

Salva solo fatti stabili e utili in futuro: preferenze, persone e relazioni, progetti, abitudini, decisioni prese,
dati pratici (per esempio i suoi dispositivi), date importanti.
Non salvare mai:
- richieste del momento ("apri Spotify", "che ore sono", "alza il volume"), domande di cultura generale, meteo, notizie;
- frasi che sembrano dette da altri (televisione, video, altre persone) o senza senso (errori di trascrizione);
- password, PIN, codici, parole d'ordine, numeri di carte o di conti, nemmeno in parte;
- quello che {user} chiede di non ricordare;
- niente su {user} ricavato dalle frasi di un "Ospite": non sono sue (al massimo chi è l'ospite, se {user} lo presenta).
Scrivi ogni fatto come una frase completa in terza persona e in italiano ("{user} ...").
Confronta con i ricordi già presenti (tra parentesi quadre il loro numero): se un fatto nuovo ne corregge o
aggiorna uno, usa "aggiorna" con quel numero; se un ricordo è diventato falso, usa "elimina"; se due ricordi dicono
la stessa cosa, aggiornane uno ed elimina l'altro. Se non c'è niente da cambiare, nessuna operazione.

Rispondi SOLO con JSON valido, senza altro testo:
{{"operazioni": [
  {{"tipo": "aggiungi", "fatto": "...", "categoria": "preferenze|persone|progetti|pc|abitudini|salute|lavoro|generale", "importanza": 1}},
  {{"tipo": "aggiorna", "id": 12, "fatto": "..."}},
  {{"tipo": "elimina", "id": 7, "motivo": "..."}}
]}}
importanza: 1 normale, 2 importante, 3 fondamentale."""


@dataclass
class KeeperRun:
    status: str  # "fatto", "niente", "attendo", "errore"
    applied: list[dict] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    detail: str = ""


def _importance(value) -> int:
    try:
        return max(1, min(3, int(value)))
    except (TypeError, ValueError):  # "alta", None...
        return 1


def parse_operations(text: str) -> list[dict]:
    """Il JSON della risposta, anche se il modello ci mette intorno del testo o un blocco ```json."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("nessun JSON nella risposta")
    data = json.loads(text[start:end + 1])
    ops = data.get("operazioni") if isinstance(data, dict) else None
    if not isinstance(ops, list):
        raise ValueError("manca l'elenco 'operazioni'")
    return [op for op in ops if isinstance(op, dict)]


class MemoryKeeper:
    def __init__(self, memory: Memory, ask: Callable[[str, str], Awaitable[str]], user: str = "Utente",
                 on_change: Callable[[dict], None] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.memory = memory
        self.ask = ask  # (istruzioni, messaggio) -> testo della risposta
        self.user = user
        self.on_change = on_change
        self.clock = clock
        self._bad_answers = 0  # risposte illeggibili di fila sullo stesso blocco

    def start_from_now(self) -> None:
        """Al primo avvio non rilegge l'arretrato (al 7/10 erano quasi tutte frasi di prova)."""
        if self.memory.get_meta(CHECKPOINT) is None:
            self.memory.set_meta(CHECKPOINT, str(self.memory.last_turn_id()))

    async def run_once(self, idle_s: float) -> KeeperRun:
        last = int(self.memory.get_meta(CHECKPOINT, "0") or 0)
        if last > self.memory.last_turn_id():  # archivio svuotato: le frasi nuove ripartono da 1
            last = 0
        turns = self.memory.turns_after(last, MAX_TURNS)
        if not turns:
            return KeeperRun("niente", detail="nessuna frase nuova")
        if len(turns) < MAX_TURNS and self.clock() - turns[-1][1] < idle_s:
            return KeeperRun("attendo", detail="la conversazione non è ancora finita")
        if not any(role == "user" and len(text.split()) > 2 for _, _, role, text in turns):
            self.memory.set_meta(CHECKPOINT, str(turns[-1][0]))
            return KeeperRun("niente", detail="solo frasi brevi")
        user_texts = [text for _, _, role, text in turns if role == "user"]
        known = self.memory.related(user_texts[-12:], k=20)
        message = self._message(turns, known)
        try:
            answer = await self.ask(SYSTEM.format(user=self.user), message)
        except Exception as e:  # rete, limiti d'uso: si riprova al giro dopo, senza perdere niente
            return KeeperRun("errore", detail=f"cervello non disponibile: {e}")
        try:
            ops = parse_operations(answer)
        except (ValueError, json.JSONDecodeError) as e:
            self._bad_answers += 1
            if self._bad_answers < 3:
                return KeeperRun("errore", detail=f"risposta illeggibile: {e}")
            log.warning("tre risposte illeggibili di fila: salto queste %d frasi", len(turns))
            ops = []
        self._bad_answers = 0
        run = self._apply(ops, {f.id for f in known})
        self.memory.set_meta(CHECKPOINT, str(turns[-1][0]))
        run.status = "fatto"
        return run

    def _message(self, turns, known) -> str:
        lines = ["Ricordi già presenti:"]
        lines += [f"[{f.id}] {f.text}" for f in known] or ["(nessuno che c'entri)"]
        lines += ["", "Conversazione (dalla più vecchia):"]
        when = time.strftime("%d/%m/%Y", time.localtime(turns[0][1]))
        lines.append(f"(del {when})")
        for _, _, role, text in turns:
            who = {"user": self.user, "jarvis": "Jarvis"}.get(role, "Ospite")
            lines.append(f"{who}: {text[:600]}")
        return "\n".join(lines)

    def _apply(self, ops: list[dict], shown: set[int]) -> KeeperRun:
        run = KeeperRun("fatto")
        for op in ops[:MAX_OPS]:
            kind = str(op.get("tipo", "")).strip().lower()
            text = re.sub(r"\s+", " ", str(op.get("fatto") or "")).strip()[:300]
            if text and SECRET.search(text):
                run.rejected.append(f"dato riservato: {text[:40]}…")
                continue
            try:
                fid = int(op["id"]) if "id" in op else None
            except (TypeError, ValueError):
                fid = None
            if kind == "aggiungi" and text:
                cat = str(op.get("categoria") or "generale").lower()
                new_id, new = self.memory.remember(text, cat if cat in CATEGORIES else "generale",
                                                   _importance(op.get("importanza")), source="custode")
                self._done(run, {"op": "ricordato" if new else "aggiornato", "id": new_id, "text": text})
            elif kind == "aggiorna" and text and fid in shown:
                if self.memory.update(fid, text):
                    self._done(run, {"op": "aggiornato", "id": fid, "text": text})
            elif kind == "elimina" and fid in shown:
                old = self.memory.get(fid)
                if old and self.memory.delete(fid):
                    self._done(run, {"op": "dimenticato", "id": fid, "text": old.text,
                                     "reason": str(op.get("motivo") or "")[:200]})
            else:
                run.rejected.append(f"operazione non valida: {json.dumps(op, ensure_ascii=False)[:80]}")
        if len(ops) > MAX_OPS:
            run.rejected.append(f"{len(ops) - MAX_OPS} operazioni oltre il limite di {MAX_OPS}")
        return run

    def _done(self, run: KeeperRun, change: dict) -> None:
        run.applied.append(change)
        log.info("memoria: %s %s", change["op"], change["text"][:120])
        if self.on_change is not None:
            self.on_change(change)

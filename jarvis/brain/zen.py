"""I modelli gratis di OpenCode Zen: quali ci sono, cosa fanno dei dati, quale usare.

I modelli gratis cambiano spesso (alcuni durano poche settimane). Jarvis legge l'elenco da OpenCode (`GET
/provider`: costo zero, strumenti, non deprecati), ogni tanto li mette alla prova con un esame breve e usa il
vincitore; se un modello sparisce o non risponde si passa al successivo della classifica.

L'esame (due domande per modello, poche richieste: anche l'uso gratuito ha un limite giornaliero):
1. italiano corretto e tempo alla prima parola (è un assistente VOCALE: conta più di tutto);
2. uso di uno strumento finto ("meteo") con l'argomento giusto e la risposta che ne riporta il dato.
Promossi = entrambe le prove; tra i promossi vince chi parla prima.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .base import BrainUnavailable, Done, TextDelta, ToolResult
from .tools import ToolRegistry, schema

log = logging.getLogger("jarvis.brain.zen")

# Cosa fanno dei dati i modelli gratis, dalla documentazione di Zen (https://opencode.ai/docs/zen/, letta il
# 28/10/2026). True = possono usarli per l'addestramento o il miglioramento del modello. Un modello che qui non c'è
# è "sconosciuto" e, con la privacy "senza addestramento", resta escluso per prudenza.
PRIVACY: dict[str, tuple[bool, str]] = {
    "big-pickle": (True, "nel periodo gratuito i dati raccolti possono servire a migliorare il modello"),
    "glm-5.3-flash-free": (False, "zero retention: il fornitore non conserva i dati e non li usa per addestrare"),
    "deepseek-v4.1-flash-free": (False, "zero retention: il fornitore non conserva i dati e non li usa per "
                                        "addestrare"),
    "beam-free": (False, "non usati per addestrare, ma conservati 30 giorni per sicurezza"),
    "flint-40b-free": (True, "possono servire a migliorare il modello; conservati 30 giorni"),
    "mimo-v2.6-flash-free": (True, "nel periodo gratuito possono servire a migliorare il modello"),
    "nemotron-3-ultra-free": (True, "endpoint di prova di NVIDIA: uso registrato e usato per migliorare i suoi "
                                    "prodotti (non legato all'identità); niente dati personali"),
    "nemotron-3.5-lightning-free": (True, "endpoint di prova di NVIDIA: uso registrato e usato per migliorare i "
                                          "suoi prodotti (non legato all'identità); niente dati personali"),
    "nemotron-3.5-super-vl-free": (True, "endpoint di prova di NVIDIA: uso registrato e usato per migliorare i "
                                         "suoi prodotti (non legato all'identità); niente dati personali"),
}
UNKNOWN_PRIVACY = "politica sui dati sconosciuta (trattato come se potesse addestrare)"
PRIVACY_CHOICES = ("tutti", "senza addestramento")

EXAM_LIMIT = 5  # al più tanti modelli per esame: ogni esame consuma un po' dell'uso gratuito del giorno
GONE_PAUSE_S = 3600.0  # un modello sparito si riprova dopo un'ora (magari era un errore momentaneo)
BUSY_PAUSE_S = 300.0  # uno sovraccarico dopo cinque minuti
BLOCKED_PAUSE_S = 86400.0  # uno non disponibile nel paese dopo un giorno (poi lo ricontrolla l'esame)


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context: int = 0
    variants: list[str] = field(default_factory=list)
    released: str = ""


def trains(model: str) -> bool | None:
    return PRIVACY[model][0] if model in PRIVACY else None


def privacy_note(model: str) -> str:
    return PRIVACY[model][1] if model in PRIVACY else UNKNOWN_PRIVACY


def free_models(providers: dict) -> list[ModelInfo]:
    """Dall'elenco di OpenCode: i modelli del fornitore "opencode" a costo zero, che usano strumenti e non sono
    deprecati, dal più recente."""
    from .opencode import PROVIDER

    connected = providers.get("connected")
    if connected is not None and PROVIDER not in connected:
        return []
    prov = next((p for p in providers.get("all") or [] if p.get("id") == PROVIDER), None)
    out = []
    for mid, m in ((prov or {}).get("models") or {}).items():
        cost = m.get("cost") or {}
        caps = m.get("capabilities") or {}
        tools = caps.get("toolcall", m.get("tool_call"))
        if cost.get("input", 1) != 0 or cost.get("output", 1) != 0 or m.get("status") == "deprecated" or not tools:
            continue
        if (caps.get("output") or {}).get("text") is False:
            continue
        out.append(ModelInfo(mid, str(m.get("name") or mid), int((m.get("limit") or {}).get("context") or 0),
                             sorted((m.get("variants") or {}).keys()), str(m.get("release_date") or "")))
    return sorted(out, key=lambda m: (m.released, m.id), reverse=True)


def voice_variant(variants: list[str]) -> str | None:
    """Il ragionamento più corto che il modello offre (a voce conta il tempo alla prima parola)."""
    return next((v for v in ("none", "minimal", "low") if v in variants), None)


@dataclass
class ExamResult:
    model: str
    italian: bool
    tools: bool
    first_word_s: float | None = None
    error: str | None = None
    at: float = 0.0
    unavailable: bool = False  # non ha risposto (lento, sovraccarico): un intoppo, non una bocciatura nel merito
    blocked: bool = False  # non disponibile nel paese dell'utente: bocciato e mai scelto, finché un esame non cambia

    @property
    def passed(self) -> bool:
        return self.italian and self.tools


class ModelPicker:
    """Catalogo, esami e problemi recenti -> la classifica dei modelli da usare (il primo è quello di oggi)."""

    def __init__(self, path: Path, fixed: Callable[[], str] = lambda: "auto",
                 privacy: Callable[[], str] = lambda: "tutti", clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.fixed = fixed  # "auto" oppure un modello scelto dall'utente
        self.privacy = privacy  # "tutti" oppure "senza addestramento"
        self.clock = clock
        self.catalog: list[ModelInfo] = []
        self.results: dict[str, ExamResult] = {}
        self.exam_at = 0.0
        self.paused: dict[str, float] = {}  # modello -> fino a quando non usarlo (non salvato: vale per oggi)
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.catalog = [ModelInfo(**m) for m in data.get("catalogo", [])]
            self.results = {k: ExamResult(**v) for k, v in data.get("risultati", {}).items()}
            self.exam_at = float(data.get("esame", 0.0))
        except (OSError, ValueError, TypeError):
            pass  # niente esame ancora (o file rovinato: si rifà)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"catalogo": [asdict(m) for m in self.catalog], "esame": self.exam_at,
                "risultati": {k: asdict(v) for k, v in self.results.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def set_catalog(self, providers: dict) -> None:
        found = free_models(providers)
        if not found and self.catalog:
            log.warning("OpenCode non elenca modelli gratis: tengo il catalogo di prima")
            return
        gone = {m.id for m in self.catalog} - {m.id for m in found}
        if gone:
            log.info("modelli gratis spariti dall'elenco: %s", ", ".join(sorted(gone)))
        self.catalog = found
        self.save()

    def allowed(self) -> list[ModelInfo]:
        """Il catalogo dopo la scelta sulla privacy."""
        if self.privacy() == "senza addestramento":
            return [m for m in self.catalog if trains(m.id) is False]
        return list(self.catalog)

    def ranking(self) -> list[str]:
        now = self.clock()
        models = [m.id for m in self.allowed() if self.paused.get(m.id, 0.0) <= now
                  and not getattr(self.results.get(m.id), "blocked", False)]
        order = {m: i for i, m in enumerate(models)}

        def rank(m: str) -> tuple:
            r = self.results.get(m)
            if r is not None and r.passed:
                return 0, r.first_word_s if r.first_word_s is not None else 99.0, order[m]
            return (1 if r is None else 2), 0.0, order[m]  # mai esaminato prima di un bocciato

        ranked = sorted(models, key=rank)
        fixed = (self.fixed() or "auto").strip()
        if fixed != "auto":
            if fixed in ranked:
                ranked.remove(fixed)
                ranked.insert(0, fixed)
            elif fixed not in {m.id for m in self.catalog}:
                log.warning("il modello scelto (%s) non è fra i gratis di oggi: uso la classifica", fixed)
        return ranked

    def best(self) -> str | None:
        r = self.ranking()
        return r[0] if r else None

    def variant(self, model: str) -> str | None:
        info = next((m for m in self.catalog if m.id == model), None)
        return voice_variant(info.variants) if info else None

    def mark_problem(self, model: str, problem: Any) -> None:
        pause = (BLOCKED_PAUSE_S if getattr(problem, "blocked", False) else
                 GONE_PAUSE_S if getattr(problem, "gone", False) else BUSY_PAUSE_S)
        self.paused[model] = self.clock() + pause
        if getattr(problem, "blocked", False):  # vale anche dopo un riavvio, finché un esame non lo trova di nuovo
            self.results[model] = ExamResult(model, False, False, None, "non disponibile in questo paese",
                                             self.clock(), blocked=True)
            self.save()

    def record(self, results: list[ExamResult]) -> None:
        for r in results:
            old = self.results.get(r.model)
            if r.unavailable and old is not None and old.passed:
                continue  # il 28/10 un modello promosso una volta non ha risposto la seconda: resta promosso
            self.results[r.model] = r
        self.exam_at = self.clock()
        self.save()

    def exam_due(self, hours: float) -> bool:
        """Esame da rifare: mai fatto (anche senza elenco: arriva accendendo OpenCode), troppo vecchio, oppure fra
        i primi c'è un modello nuovo mai esaminato."""
        if hours <= 0:
            return False
        if not self.catalog or not self.results or self.clock() - self.exam_at > hours * 3600:
            return True
        return any(m.id not in self.results for m in self.allowed()[:EXAM_LIMIT])

    def describe(self) -> str:
        """Per Jarvis (strumento modelli_gratis): la classifica con esito dell'esame e uso dei dati."""
        if not self.catalog:
            return "Non ho ancora l'elenco dei modelli gratis di OpenCode (si legge al primo uso)."
        lines = []
        ranked = self.ranking()
        for m in sorted(self.allowed(), key=lambda m: ranked.index(m.id) if m.id in ranked else 999):
            r = self.results.get(m.id)
            if r is None:
                exam = "mai esaminato"
            elif r.passed:
                exam = "promosso" + (f", prima parola in {r.first_word_s:.1f} s".replace(".", ",")
                                     if r.first_word_s is not None else "")
            else:
                exam = "bocciato (" + (r.error or ("italiano" if not r.italian else "strumenti")) + ")"
            pause = " [in pausa per un problema recente]" if self.paused.get(m.id, 0.0) > self.clock() else ""
            lines.append(f"- {m.id}: {exam}{pause}; dati: {privacy_note(m.id)}")
        hidden = len(self.catalog) - len(self.allowed())
        head = f"In uso: {ranked[0]}." if ranked else "Nessun modello gratis utilizzabile adesso."
        if hidden:
            head += f" {hidden} esclusi perché possono usare i dati per addestrarsi (privacy: senza addestramento)."
        when = time.strftime("%d/%m alle %H:%M", time.localtime(self.exam_at)) if self.exam_at else "mai"
        return f"{head} Ultimo esame: {when}.\n" + "\n".join(lines)


# ---------------------------------------------------------------------- l'esame

EXAM_SYSTEM = ("Sei Jarvis, l'assistente vocale di {user}. Rispondi sempre in italiano, in una o due frasi brevi, "
               "senza elenchi né markdown: le tue parole vengono lette ad alta voce. Per sapere che tempo fa usa "
               "lo strumento meteo.")
QUESTIONS = ("Presentati in una frase: chi sei e cosa puoi fare per me?", "Che tempo fa adesso a Roma?")
WEATHER = "Roma: 18 gradi, cielo sereno, vento debole da nord."

_FOREIGN = re.compile(r"[Ѐ-ӿ؀-ۿ぀-ヿ㐀-鿿가-힯]")
_IT = {"il", "lo", "la", "i", "gli", "le", "un", "una", "di", "da", "che", "e", "è", "per", "non", "sono", "in",
       "con", "mi", "ti", "si", "del", "della", "al", "alla", "posso", "può", "come", "sua", "suo", "lei", "oggi",
       "ho", "io", "ma", "anche", "cosa", "sia", "nel", "nella", "ci", "vi"}
_EN = {"the", "and", "is", "are", "you", "i'm", "to", "of", "it", "can", "with", "for", "this", "that", "your",
       "my", "what", "how", "today", "today's"}


def italian_ok(text: str) -> tuple[bool, str]:
    """Una risposta detta in italiano (a voce non deve scappare né inglese né un carattere cinese)."""
    words = re.findall(r"[a-zàèéìòóù']+", text.lower())
    if len(words) < 3:
        return False, "risposta vuota o troppo corta"
    if _FOREIGN.search(text):
        return False, "caratteri di un'altra lingua"
    it = sum(w in _IT for w in words)
    en = sum(w in _EN for w in words)
    if it < 2 or en * 3 > it:
        return False, "non è italiano"
    return True, ""


def exam_tools(calls: list[dict]) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.register("meteo", "Il tempo che fa adesso in una città.",
                  schema({"citta": {"type": "string", "description": "nome della città"}}, ["citta"]), read_only=True)
    def meteo(args):
        calls.append(dict(args))
        return WEATHER if "roma" in str(args.get("citta", "")).lower() else "Errore: città non trovata"

    return reg


class Examiner:
    """Fa l'esame ai modelli gratis, uno alla volta, mai due esami insieme."""

    def __init__(self, hub: Any, picker: ModelPicker, user: str = "Utente") -> None:
        self.hub = hub
        self.picker = picker
        self.user = user
        self.calls: list[dict] = []
        self.registry = exam_tools(self.calls)
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def examine(self, model: str) -> ExamResult:
        from .opencode import EXAM_AGENT, OpenCodeBrain

        problems: list[Any] = []
        brain = OpenCodeBrain(EXAM_SYSTEM.format(user=self.user), self.hub, models=lambda: [model],
                              name=f"esame {model}", agent=EXAM_AGENT, prefix="esame_", max_models=1, ephemeral=True,
                              on_model_problem=lambda _m, p: problems.append(p))
        try:
            t0 = time.perf_counter()
            first, text = None, ""
            async for ev in brain.respond(QUESTIONS[0]):
                if isinstance(ev, TextDelta) and first is None:
                    first = round(time.perf_counter() - t0, 2)
                elif isinstance(ev, Done):
                    text = ev.text
            italian, why = italian_ok(text)
            self.calls.clear()
            failed_tool, answer = False, ""
            async for ev in brain.respond(QUESTIONS[1]):
                if isinstance(ev, ToolResult) and not ev.ok:
                    failed_tool = True
                elif isinstance(ev, Done):
                    answer = ev.text
            used = any("roma" in str(c.get("citta", "")).lower() for c in self.calls)
            tools = used and not failed_tool and "18" in answer
            error = why or (None if tools else "non ha usato bene lo strumento" if not used or failed_tool
                            else "non ha riportato il dato dello strumento")
            return ExamResult(model, italian, tools, first, error, self.picker.clock())
        except BrainUnavailable as e:
            blocked = next((p for p in problems if getattr(p, "blocked", False)), None)
            if blocked is not None:  # vietato nel paese: bocciato (anche se era promosso), e si va avanti con gli altri
                self.picker.mark_problem(model, blocked)
                return ExamResult(model, False, False, None, "non disponibile in questo paese", self.picker.clock(),
                                  blocked=True)
            if e.scope == "provider":  # limite gratuito o chiave rifiutata: inutile esaminare gli altri
                raise
            return ExamResult(model, False, False, None, str(e)[:160], self.picker.clock(), unavailable=True)
        finally:
            await brain.stop()

    def candidates(self, limit: int = EXAM_LIMIT) -> list[str]:
        """Chi esaminare: il modello in uso (magari è diventato lento), poi quelli mai esaminati, poi gli altri.
        Così, esame dopo esame, passano tutti anche se ogni volta se ne esaminano pochi."""
        allowed = [m.id for m in self.picker.allowed()]
        best = self.picker.best()
        order = ([best] if best else []) + [m for m in allowed if m not in self.picker.results] + allowed
        return list(dict.fromkeys(order))[:limit]

    async def run(self, limit: int = EXAM_LIMIT) -> list[ExamResult]:
        if self._lock.locked():
            return []
        async with self._lock:
            async with self.hub.using():
                pass  # accende OpenCode e rilegge l'elenco dei modelli
            results: list[ExamResult] = []
            for model in self.candidates(limit):
                try:
                    r = await self.examine(model)
                except BrainUnavailable as e:
                    log.warning("esame dei modelli gratis interrotto: %s", e)
                    break
                log.info("esame di %s: italiano %s, strumenti %s, prima parola %s s%s", model, r.italian, r.tools,
                         r.first_word_s, f" ({r.error})" if r.error else "")
                results.append(r)
            if results:
                self.picker.record(results)
            return results

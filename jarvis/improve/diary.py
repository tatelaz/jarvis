"""Auto-miglioramento spontaneo: diario silenzioso degli errori e proposte rare (regole decise con l'utente il 7/10).

- Si annota in silenzio: cadute, strumenti falliti, compiti falliti, errori del cervello o dell'udito, e le correzioni
  dell'utente ("no, intendevo…") subito dopo una risposta. Errori uguali hanno la stessa firma.
- Diventa una proposta solo una firma ripetuta almeno 3 volte (negli ultimi 30 giorni) e mai rifiutata.
- Al massimo un riepilogo per periodo (settimana di base; a voce: mai / settimana / giorno) con 3 proposte al
  massimo, in un momento tranquillo: lo decide l'app (l'utente presente, niente compiti, non a riposo).
- Un no vale per sempre: la firma rifiutata non torna più. "Hai proposte?" le elenca quando vuole lui.

Tutto resta sul PC (data/diario.db): sono frasi dell'utente e risposte di Jarvis.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MIN_REPEATS = 3
MAX_PROPOSALS = 3
WINDOW_DAYS = 30
PERIOD_S = {"giorno": 86400.0, "settimana": 7 * 86400.0}  # "mai" = nessun riepilogo spontaneo
CORRECTION_WINDOW_S = 60.0  # una correzione conta solo se arriva subito dopo una risposta
# fuori dal controllo di Jarvis: limiti d'uso, accesso, rete. Non sono difetti da correggere nel suo codice
EXTERNAL = re.compile(r"rate.?limit|limite|session limit|oauth|login|unauthori[sz]ed|401|403|429|timeout|timed out|"
                      r"connection|connessione|network|rete|overloaded|503|502", re.I)
CORRECTION = re.compile(r"^\s*(no[,.!]|no\s+(no|aspetta|intendevo|volevo|ho detto|non)|non\s+(è|e)\s+(quello|questo|così)|"
                        r"intendevo|volevo dire|ho detto|non ho detto|hai capito male|non hai capito|sbagliato|"
                        r"ma no\b|ti ho chiesto)", re.I)


@dataclass
class Entry:
    kind: str  # "strumento" | "compito" | "cervello" | "udito" | "correzione" | "caduta"
    key: str  # firma: errori uguali hanno la stessa
    title: str  # come lo si direbbe all'utente
    detail: str  # un esempio concreto


@dataclass
class Proposal:
    key: str
    title: str
    count: int
    examples: list[str] = field(default_factory=list)


def norm(text: str, most: int = 60) -> str:
    """Firma di un messaggio d'errore: senza numeri, percorsi, virgolette e spazi in più."""
    t = re.sub(r"[A-Za-z]:\\[^\s'\"]+|/[\w./-]+", "<percorso>", str(text).lower())
    t = re.sub(r"\d+", "", t)  # "dopo 30 s" e "dopo 5 s" sono lo stesso errore
    t = re.sub(r"[\"'«»`]", "", t)
    return " ".join(t.split())[:most]


def plain(preview: str) -> str:
    """Il testo di un risultato di strumento come arriva da Claude ("[{'type': 'text', 'text': '…'}]")."""
    m = re.match(r"\[\{'type': 'text', 'text': ['\"](.*)", preview, re.S)
    return m.group(1).rstrip("'\"}]") if m else preview


def clip(text: str, most: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[: most - 1] + "…"


class Diary:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, key TEXT, title TEXT,
                                                   detail TEXT);
                CREATE INDEX IF NOT EXISTS errors_key ON errors(key);
                CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, status TEXT, ts REAL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """)
        self.listed: list[str] = []  # firme dell'ultimo elenco letto all'utente: "la numero 2" si riferisce a queste

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------ annotare

    def note(self, e: Entry, ts: float | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO errors (ts, kind, key, title, detail) VALUES (?, ?, ?, ?, ?)",
                             (ts or time.time(), e.kind, e.key, e.title, clip(e.detail, 300)))

    def count(self, key: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM errors WHERE key = ?", (key,)).fetchone()[0]

    # ------------------------------------------------------------ proporre

    def candidates(self, now: float | None = None, limit: int = MAX_PROPOSALS) -> list[Proposal]:
        """Errori ripetuti almeno MIN_REPEATS volte di recente, mai decisi; i più frequenti prima."""
        since = (now or time.time()) - WINDOW_DAYS * 86400
        with self._lock:
            rows = self._db.execute("""
                SELECT key, COUNT(*) AS n, MAX(ts) AS last FROM errors
                WHERE ts >= ? AND key NOT IN (SELECT key FROM decisions)
                GROUP BY key HAVING n >= ? ORDER BY n DESC, last DESC LIMIT ?""", (since, MIN_REPEATS, limit)).fetchall()
            out = []
            for key, n, _last in rows:
                # anche per id: su Windows l'orologio scatta ogni ~15 ms e più errori hanno la stessa ora
                ex = self._db.execute("SELECT title, detail FROM errors WHERE key = ? ORDER BY ts DESC, id DESC "
                                      "LIMIT 3", (key,)).fetchall()
                out.append(Proposal(key, ex[0][0], n, [d for _, d in ex]))
        return out

    def decide(self, key: str, status: str) -> None:
        """"rifiutata" (non torna più) o "accettata" (passata a migliora_jarvis)."""
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO decisions (key, status, ts) VALUES (?, ?, ?)",
                             (key, status, time.time()))

    def decision(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT status FROM decisions WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------ quando

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def due(self, frequency: str, now: float | None = None) -> bool:
        """È ora di un riepilogo spontaneo? (al primo avvio si comincia a contare da adesso)"""
        if frequency not in PERIOD_S:
            return False
        now = now or time.time()
        last = self.get_meta("ultimo_riepilogo")
        if last is None:
            self.set_meta("ultimo_riepilogo", str(now))
            return False
        return now - float(last) >= PERIOD_S[frequency]

    def summary_done(self, now: float | None = None) -> None:
        self.set_meta("ultimo_riepilogo", str(now or time.time()))


def listing(proposals: list[Proposal]) -> str:
    """Le proposte numerate, come le legge Jarvis (i numeri servono a rispondi_proposta)."""
    lines = []
    for i, p in enumerate(proposals, 1):
        lines.append(f"{i}) {p.title}: {p.count} volte. Esempi: " + " | ".join(clip(e, 120) for e in p.examples))
    return "\n".join(lines)


def summary_event(proposals: list[Proposal], user: str) -> str:
    return (f"Riepilogo delle cose da migliorare (dal tuo diario degli errori: solo quelle ripetute almeno "
            f"{MIN_REPEATS} volte). Proponile a {user} in poche parole, una per una, e chiedi se vuole che te ne "
            f"occupi; poi rispondi_proposta col numero (accetta sì o no: un no non la farà tornare mai più). "
            f"Se ha da fare o non risponde, lascia stare.\n" + listing(proposals))


def improve_request(p: Proposal) -> str:
    """Il testo per migliora_jarvis: cosa si ripete e gli esempi veri dal diario."""
    examples = "\n".join(f"- {e}" for e in p.examples)
    return (f"Difetto ripetuto {p.count} volte negli ultimi {WINDOW_DAYS} giorni: {p.title}.\nEsempi dal diario "
            f"degli errori:\n{examples}\nTrova la causa nel codice, correggila e aggiungi un test che la riproduce.")


# ------------------------------------------------------------ dagli eventi alle firme

class Recorder:
    """Ascolta gli eventi del bus e annota nel diario quelli che sono errori di Jarvis."""

    def __init__(self, diary: Diary, user: str = "Utente") -> None:
        self.diary, self.user = diary, user
        self._failed_tasks: set[int] = set()
        self._turn_tools: list[str] = []  # strumenti dell'ultima risposta (per capire cosa è stato corretto)
        self._last_reply: tuple[float, str, list[str]] | None = None
        self.last_owner_at = 0.0  # ultima frase dell'utente: per capire se è presente
        self.last_activity_at = 0.0  # ultima frase o risposta: per capire se la conversazione è finita

    def handle(self, ev: dict, now: float | None = None) -> Entry | None:
        now = now or time.time()
        e = self.classify(ev, now)
        if e is not None:
            self.diary.note(e, now)
        return e

    def classify(self, ev: dict, now: float) -> Entry | None:
        kind = ev.get("type")
        if kind == "tool":
            self._turn_tools.append(str(ev.get("name")))
        elif kind == "jarvis_done":
            self._last_reply = (now, str(ev.get("text") or ""), self._turn_tools)
            self._turn_tools = []
            self.last_activity_at = now
        elif kind == "user":
            self.last_activity_at = now
            if ev.get("owner", True):  # le correzioni contano solo se vengono dall'utente
                self.last_owner_at = now
                return self._correction(str(ev.get("text") or ""), now)
        elif kind == "tool_result" and not ev.get("ok", True):
            name, why = str(ev.get("name")), plain(str(ev.get("preview") or ""))
            if EXTERNAL.search(why):
                return None
            return Entry("strumento", f"strumento:{name}:{norm(why)}", f"lo strumento «{name}» non riesce",
                         f"{name}: {why}")
        elif kind == "task" and ev.get("status") == "fallito" and ev.get("id") not in self._failed_tasks:
            self._failed_tasks.add(ev.get("id"))
            why = str(ev.get("error") or "")
            if EXTERNAL.search(why):
                return None
            return Entry("compito", f"compito:{norm(why)}", "un compito fallisce sempre allo stesso modo",
                         f"«{ev.get('title')}»: {why}")
        elif kind == "error":
            where, msg = str(ev.get("where")), str(ev.get("message") or "")
            if EXTERNAL.search(msg):
                return None
            what = {"brain": ("cervello", "il cervello si inceppa"), "stt": ("udito", "la trascrizione fallisce")}
            k, title = what.get(where, (where, f"errore ({where})"))
            return Entry(k, f"{k}:{norm(msg)}", title, msg)
        return None

    def _correction(self, text: str, now: float) -> Entry | None:
        if not self._last_reply or now - self._last_reply[0] > CORRECTION_WINDOW_S or not CORRECTION.search(text):
            return None
        _, said, tools = self._last_reply
        target = next((t for t in reversed(tools) if t != "nessuna_risposta"), None)
        what = f"lo strumento «{target}»" if target else "la risposta"
        return Entry("correzione", f"correzione:{target or 'risposta'}",
                     f"{self.user} mi corregge spesso dopo {what}",
                     f"Jarvis: «{clip(said, 100)}» → {self.user}: «{clip(text, 100)}»")


def crash_entries(reports: list[Path]) -> list[Entry]:
    """Le cadute dai rapporti del guardiano (data/logs/cadute): firma = l'ultima riga d'errore."""
    out = []
    for p in reports:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        err = next((ln.strip() for ln in reversed(lines) if re.match(r"\s*\w+(Error|Exception)\b", ln)), None)
        head = [ln.strip() for ln in lines[:4] if ln.lower().startswith(("motivo", "codice"))]  # testa del rapporto
        sign = err or ", ".join(head) or "caduta senza messaggio"
        out.append(Entry("caduta", f"caduta:{norm(sign)}", "Jarvis cade", f"{p.name}: {sign}"))
    return out

"""Segnalazioni agli sviluppatori: un rapporto tecnico ripulito dai dati personali, che parte solo se l'utente vuole.

- Cosa parte: versione, Windows, scheda video, cervello, le righe del registro che servono (avvisi, errori con il loro
  traceback, le righe dei registri tecnici), le firme degli errori ripetuti nel diario e la descrizione dell'utente,
  se l'ha data.
- Cosa non parte mai: le frasi dette a Jarvis e le sue risposte, il nome dell'utente e della sua cartella, email,
  numeri di telefono, contatti di WhatsApp, segreti. Dai registri che possono portare testo della conversazione
  (cervello, voce, memoria, app...) arrivano solo il livello e il punto del codice, mai il messaggio; ogni riga passa
  comunque da scrub().
- Quando: a voce (app "segnalazioni": sempre dopo un sì, con detto cosa parte), dalla finestra (si legge il testo
  prima di mandarlo) e dopo una caduta come dice system.crash_reports (chiedi, sempre, mai).
- Dove: il piccolo server di servizi/segnalazioni (un Cloudflare Worker) le mette come issue in un repository
  privato. Senza rete restano in data/segnalazioni e ripartono al prossimo avvio.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .log import redact

log = logging.getLogger("jarvis.feedback")

MAX_TEXT = 60_000  # una issue di GitHub ne tiene 65 536: si tagliano le righe più vecchie del registro
LOG_LINES = 5000  # quante righe del registro si guardano: il registro è dettagliato, ne restano molte meno
# registri tecnici: le loro righe normali (INFO) dicono cosa faceva Jarvis senza parole di nessuno
TECH_LOGGERS = ("jarvis.supervisor", "jarvis.health", "jarvis.engine", "jarvis.speech", "jarvis.updates",
                "jarvis.localrepo", "jarvis.setup", "jarvis.certs", "jarvis.audio.devices", "jarvis.winjob",
                "jarvis.single", "jarvis.feedback", "jarvis.self_improve")
# registri che possono portare frasi, nomi e messaggi: si tiene solo che c'è stato un avviso o un errore, e dove
PERSONAL_LOGGERS = ("jarvis.conversation", "jarvis.brain", "jarvis.stt", "jarvis.tts", "jarvis.voice",
                    "jarvis.memory", "jarvis.whatsapp", "jarvis.context", "jarvis.bus", "jarvis.worker",
                    "jarvis.tasks", "jarvis.speaker", "jarvis.identity", "jarvis.media", "jarvis.scheduler",
                    "jarvis.opencode", "jarvis.tools", "jarvis.apps", "jarvis.gmail", "jarvis.ui", "jarvis.app")
REMOVED = "[messaggio tolto]"

_HEAD = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (\w+)\s+([\w.]+): (.*)$")
_FRAME = re.compile(r'^\s+File "([^"]+)", line (\d+), in (\S+)\s*$')
# la riga dell'eccezione in un traceback: a inizio riga, un nome (anche con i moduli davanti) e il suo messaggio
_EXC = re.compile(r"^([A-Za-z_][\w.]*)(?::\s?(.*))?$")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_CHAT_ID = re.compile(r"[\w.\-]+@(?:c\.us|g\.us|lid|s\.whatsapp\.net|broadcast|newsletter)\b")
# numeri di telefono (anche con prefisso e spazi) e lunghe file di cifre (i numeri dentro gli id di WhatsApp); le date e
# le ore del registro no: hanno trattini e due punti in mezzo
_PHONE = re.compile(r"(?<![\w.,:/\\-])(?:(?:\+\d{1,3} ?)?\d{3} ?\d{3} ?\d{3,4}|\d{9,15})(?![\w.,:/\\-])")
_HOME = re.compile(r"(?i)([A-Z]:[\\/]+(?:Users|Utenti)[\\/]+)([^\\/\"'\s]+)")
# testo fra virgolette con almeno uno spazio e senza barre (le barre dicono "percorso"): può essere una frase detta
_QUOTED = re.compile(r"(['\"«“‘])([^'\"«»“”‘’\\/\n]*\s[^'\"«»“”‘’\\/\n]*)(['\"»”’])")


def short_path(path: str, root: Path | None = None) -> str:
    """Un percorso di file come lo si può mandare: dentro Jarvis relativo, nelle librerie da site-packages in poi, il
    resto solo il nome del file. Le cartelle dicono come l'utente ha organizzato il suo PC (e a volte il suo nome)."""
    p = path.replace("/", "\\")
    base = str(root or paths.ROOT).replace("/", "\\").rstrip("\\") + "\\"
    if p.lower().startswith(base.lower()):
        return p[len(base):].replace("\\", "/")
    if m := re.search(r"(?i)\\site-packages\\(.+)$", p):
        return "site-packages/" + m[1].replace("\\", "/")
    if m := re.search(r"(?i)\\Lib\\(.+)$", p):
        return "python/Lib/" + m[1].replace("\\", "/")
    return "…/" + p.rsplit("\\", 1)[-1]


def scrub(text: str, names: Iterable[str] = (), root: Path | None = None) -> str:
    """Toglie da un testo ciò che può dire chi è l'utente o cosa ha detto. Mai l'ultima difesa: le righe dei registri
    personali non arrivano proprio (vedi log_lines)."""
    text = redact(text)
    base = str(root or paths.ROOT).rstrip("\\/")
    for form in {base, base.replace("\\", "/"), base.replace("/", "\\")}:  # la cartella di Jarvis: anche lei dice dove
        text = re.sub(re.escape(form), "<jarvis>", text, flags=re.I)
    text = _HOME.sub(r"\1[UTENTE]", text)
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True):
        text = re.sub(rf"(?i)(?<!\w){re.escape(name)}(?!\w)", "[NOME]", text)
    text = _CHAT_ID.sub("[CONTATTO]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[NUMERO]", text)
    return _QUOTED.sub(lambda m: f"{m[1]}[TESTO]{m[3]}", text)


def _personal(logger: str) -> bool:
    return logger.startswith(PERSONAL_LOGGERS) or not logger.startswith("jarvis")


def _technical(logger: str) -> bool:
    return logger.startswith(TECH_LOGGERS)


def log_lines(lines: Iterable[str], names: Iterable[str] = ()) -> list[str]:
    """Le righe del registro che servono a capire un problema, ripulite.

    Avvisi ed errori di tutti, con il loro traceback; le righe normali solo dei registri tecnici. Dai registri che
    possono portare testo della conversazione (o di librerie esterne) restano livello, nome e punti del codice: il
    messaggio e il testo dell'eccezione no."""
    names = list(names)
    out: list[str] = []
    keep = personal = False
    for raw in lines:
        line = raw.rstrip("\r\n")
        head = _HEAD.match(line)
        if head:
            stamp, level, logger, msg = head.groups()
            n = _LEVELS.get(level, 20)
            keep = n >= 30 or (n == 20 and _technical(logger))
            personal = _personal(logger) and not _technical(logger)
            if keep:
                out.append(f"{stamp} {level} {logger}: {REMOVED if personal else scrub(msg, names)}")
            continue
        if not keep or not line.strip():
            continue
        if line.startswith("Traceback") or line.startswith(("During handling", "The above exception")):
            out.append(line)
        elif m := _FRAME.match(line):
            out.append(scrub(f'  File "{short_path(m[1])}", line {m[2]}, in {m[3]}', names))
        elif not line[0].isspace() and (m := _EXC.match(line)):
            detail = "" if m[2] is None else (f": {REMOVED}" if personal else f": {scrub(m[2], names)}")
            out.append(f"{m[1]}{detail}")
        elif not personal and line.startswith("    "):  # la riga di codice del traceback: è il codice di Jarvis
            out.append(scrub(line, names))
    return out


def tail(path: Path, n: int = LOG_LINES) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def latest_crash(logs: Path | None = None) -> Path | None:
    reports = sorted(((logs or paths.logs_dir()) / "cadute").glob("*.txt"))
    return reports[-1] if reports else None


def where_it_broke(lines: list[str]) -> str:
    """Per il titolo: l'ultima eccezione e l'ultimo punto del codice di Jarvis prima di lei."""
    at = next((i for i in range(len(lines) - 1, -1, -1) if lines[i][:1].isalpha() and _EXC.match(lines[i])), None)
    if at is None:
        return ""
    frame = ""
    for ln in lines[:at]:  # i percorsi sono già accorciati (short_path): quelli di Jarvis cominciano da jarvis/
        if (m := _FRAME.match(ln)) and re.match(r"jarvis[\\/]", m[1]):
            frame = m[1].replace("\\", "/") + f":{m[2]}"
    return " in ".join(x for x in (_EXC.match(lines[at])[1], frame) if x)


# ---------------------------------------------------------------------- il rapporto


@dataclass(frozen=True)
class Report:
    kind: str  # "caduta" | "segnalazione"
    title: str
    text: str  # già ripulito: è esattamente ciò che parte
    version: str

    def payload(self) -> dict[str, str]:
        return {"tipo": self.kind, "titolo": self.title, "testo": self.text, "versione": self.version}


def names_to_hide(user_name: str = "") -> list[str]:
    """Il nome dell'utente e quello della sua cartella di Windows."""
    found = [user_name, os.environ.get("USERNAME", ""), Path.home().name]
    return [n for n in found if n]


def machine(cfg: Any, root: Path = paths.ROOT, run: Callable[[list[str]], str] | None = None) -> list[str]:
    """Le righe sul PC e su Jarvis: niente che dica chi è l'utente."""
    from .localrepo import installed_copy
    from .setup import pc
    from .updates import current_version

    run = run or (lambda cmd: pc._run(cmd, timeout=5))
    try:
        version = ".".join(map(str, current_version(root)))
    except (OSError, ValueError, KeyError):
        version = "?"
    commit = run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"]).strip() if (root / ".git").exists() else ""
    gpus = pc.parse_nvidia_smi(run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                    "--format=csv,noheader,nounits"]))
    try:
        import psutil

        ram = f"{psutil.virtual_memory().total / 2**30:.0f} GB"
    except Exception:
        ram = "?"
    brain = getattr(getattr(cfg, "brain", None), "provider", "?")
    lang = getattr(getattr(cfg, "persona", None), "language", "?")
    return [f"- Jarvis {version}{f' (commit {commit})' if commit else ''}, "
            f"{'copia installata' if installed_copy(root) else 'copia di sviluppo'}",
            f"- Windows: {platform.platform()} | Python {platform.python_version()} | memoria {ram}",
            f"- Scheda video: {', '.join(f'{g.name} ({g.vram_mb // 1024} GB, driver {g.driver})' for g in gpus) or 'nessuna NVIDIA'}",
            f"- Cervello: {brain} | lingua: {lang}"]


def build(kind: str, description: str = "", *, cfg: Any = None, log_file: Path | None = None,
          crash_file: Path | None = None, diary_lines: Iterable[str] = (), machine_lines: list[str] | None = None,
          version: str = "?") -> Report:
    """Il rapporto, già ripulito. kind "caduta" usa il rapporto del guardiano (crash_file); "segnalazione" il registro."""
    user = getattr(getattr(cfg, "persona", None), "user_name", "") if cfg is not None else ""
    names = names_to_hide(user)
    head = []
    source = tail(log_file or paths.logs_dir() / "jarvis.log")
    if kind == "caduta" and crash_file is not None:
        crash = tail(crash_file, 10_000)
        head = [scrub(ln, names) for ln in crash[:3] if ln.lower().startswith(("motivo", "codice", "ora"))]
        source = crash
    lines = log_lines(source, names)
    where = where_it_broke(lines)
    said = scrub(" ".join(description.split()), names)  # le sue parole, ma senza email, numeri e nomi
    if kind == "caduta":
        title = f"Caduta: {where}" if where else "Caduta senza errore nel registro"
    else:
        title = "Segnalazione: " + (said[:70] + ("…" if len(said) > 70 else "") if said else (where or "senza testo"))
    parts = [f"**{'Caduta' if kind == 'caduta' else 'Segnalazione'}** da Jarvis", ""]
    if said:
        parts += ["### Cosa dice chi lo usa", said, ""]
    parts += ["### Il PC", *(machine_lines if machine_lines is not None else machine(cfg)), ""]
    if head:
        parts += ["### La caduta", *[f"- {h}" for h in head], ""]
    diary = [scrub(d, names) for d in diary_lines]
    if diary:
        parts += ["### Errori ripetuti (diario)", *[f"- {d}" for d in diary], ""]
    intro = [*parts, "### Registro (solo avvisi, errori e righe tecniche; ripulito)", "```"]
    room = MAX_TEXT - len("\n".join(intro)) - 10
    kept: list[str] = []
    for ln in reversed(lines):  # dalle più recenti: se non c'è posto si perdono le più vecchie
        room -= len(ln) + 1
        if room < 0:
            break
        kept.append(ln)
    text = "\n".join(intro + list(reversed(kept)) + ["```"])
    return Report(kind, scrub(title, names)[:120], text, version)


def diary_summary(diary: Any, now: float | None = None) -> list[str]:
    """Le firme degli errori ripetuti, senza le frasi: mai le correzioni (sono parole dell'utente)."""
    if diary is None:
        return []
    try:
        found = diary.candidates(now or time.time(), limit=10)
    except Exception:  # il diario non deve mai fermare una segnalazione
        return []
    # il titolo è generico ("lo strumento «x» non riesce", "Jarvis cade"); la firma no: può avere parti di messaggi
    return [f"{p.title} ({p.count} volte in 30 giorni)" for p in found if not p.key.startswith("correzione:")]


# ---------------------------------------------------------------------- spedizione


class SendError(Exception):
    pass


async def send(report: Report, url: str, post: Callable[..., Any] | None = None, timeout: float = 20) -> str:
    """Al server delle segnalazioni. -> il numero della issue, come lo dice il server."""
    if not url:
        raise SendError("le segnalazioni non sono ancora attive in questa versione")
    if post is None:
        import aiohttp

        async def post(u: str, data: dict) -> tuple[int, str]:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s, \
                    s.post(u, json=data) as r:
                return r.status, await r.text()
    try:
        status, body = await post(url, report.payload())
    except Exception as e:  # rete assente, server spento: si riprova più tardi
        raise SendError(f"server non raggiungibile ({type(e).__name__})") from e
    if status not in (200, 201):
        raise SendError(f"il server ha risposto {status}")
    try:
        return str(json.loads(body).get("numero", ""))
    except (ValueError, AttributeError):
        return ""


class Outbox:
    """Le segnalazioni che non sono partite (rete assente): ripartono al prossimo avvio. Già ripulite."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or paths.data_path("segnalazioni")

    def put(self, report: Report) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{time.strftime('%Y%m%d_%H%M%S')}_{report.kind}_{os.getpid()}.json"
        path.write_text(json.dumps(report.payload(), ensure_ascii=False), encoding="utf-8")
        return path

    def waiting(self) -> list[Path]:
        return sorted(self.folder.glob("*.json")) if self.folder.exists() else []

    async def flush(self, url: str, post: Callable[..., Any] | None = None) -> int:
        """Rimanda quelle in attesa. -> quante sono partite."""
        sent, ready = 0, []
        for path in self.waiting():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ready.append((path, Report(data["tipo"], data["titolo"], data["testo"], data.get("versione", "?"))))
            except (OSError, ValueError, KeyError, TypeError):
                path.unlink(missing_ok=True)  # rovinata: non partirà mai, e senza rete non deve restare lì
        for path, report in ready:
            try:
                await send(report, url, post)
            except SendError:
                break  # ancora niente rete: le altre aspettano con lei
            path.unlink(missing_ok=True)
            sent += 1
        return sent


# ---------------------------------------------------------------------- l'app "segnalazioni"


def build_app(make: Callable[[str, str], Report], deliver: Callable[[Report], Any], ready: Callable[[], str | None]):
    """make(tipo, descrizione) -> Report; deliver(report) -> frase da dire (async). Mandare chiede sempre un sì,
    anche con le conferme delle azioni delicate spente: parte qualcosa verso altre persone."""
    from .apps.base import App, AppError

    app = App("segnalazioni", "Segnalazioni agli sviluppatori di Jarvis", "sistema", ready=ready,
              aliases=("segnala", "segnalazione", "feedback", "bug", "rapporto", "problema"),
              notes="Per un problema, un'idea o una caduta: parte un rapporto tecnico ripulito (niente frasi delle "
                    "conversazioni, nomi, email, numeri), come issue in un repository privato degli sviluppatori. "
                    "descrizione = le parole dell'utente su cosa è successo (anche vuota). tipo caduta = il rapporto "
                    "dell'ultima caduta. anteprima dice cosa partirebbe, senza mandare niente.")

    def kind_of(p: dict[str, Any]) -> str:
        k = str(p.get("tipo") or "segnalazione").strip().lower()
        if k not in ("segnalazione", "caduta"):
            raise AppError("tipo va scritto segnalazione o caduta")
        return k

    def confirm(p: dict[str, Any]) -> str:
        report = make(kind_of(p), str(p.get("descrizione") or ""))
        p["_rapporto"] = report  # parte esattamente questo, quello letto all'utente
        lines = report.text.count("\n")
        return (f"mando agli sviluppatori «{report.title}» ({lines} righe tecniche: versione, PC ed errori, senza frasi "
                f"delle conversazioni né dati personali; il testo esatto si vede nella finestra con Segnala)")

    @app.action("anteprima", "cosa partirebbe, senza mandare niente",
                {"descrizione": "?le parole dell'utente", "tipo": "?segnalazione o caduta"})
    def preview(p):
        return make(kind_of(p), str(p.get("descrizione") or "")).text

    @app.action("manda", "manda la segnalazione (chiede sempre conferma)",
                {"descrizione": "?le parole dell'utente su cosa è successo", "tipo": "?segnalazione o caduta"},
                confirm=confirm, always_confirm=True)
    async def manda(p):
        report = p.get("_rapporto") or make(kind_of(p), str(p.get("descrizione") or ""))
        return await deliver(report)

    return app

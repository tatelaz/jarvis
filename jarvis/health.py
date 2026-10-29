"""La salute di Jarvis vista da dentro.

- Battito: ogni pochi secondi Jarvis scrive data/state/heartbeat.json. Il supervisore lo legge: se il
  battito si ferma, Jarvis è bloccato (non solo caduto) e va riavviato.
- Versione sana: dopo qualche minuto di funzionamento regolare il commit in uso diventa "l'ultima versione
  sana". Se una versione nuova continua a cadere, il supervisore torna a quella.
- Nota di ripresa: prima di riavviare, il supervisore lascia scritto perché. Jarvis la legge all'avvio e
  lo dice in una frase.
- Autodiagnosi: prima di caricare i motori controlla ciò che serve (modelli, motore, Claude, memoria video,
  microfono). Un problema si dice a voce invece di cadere in silenzio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import paths

log = logging.getLogger("jarvis.health")

HEARTBEAT = "heartbeat.json"
KNOWN_GOOD = "known_good.json"
RECOVERY = "recovery.json"
VOICE_VRAM_MB = 4300  # la voce: 3,9 GB con audio.cpp 0.9.2 (5,3 con la 0.9.0; misurato il 26/10), più un margine
EARS_VRAM_MB = 1800  # l'udito sulla scheda video: 1,2 GB, fino a 1,8 con la voce al lavoro (misurato il 26/10)
RESTART_CODE = 75  # Jarvis esce con questo codice quando chiede di essere riavviato (non è una caduta)
RUN_ID_ENV = "JARVIS_RUN_ID"  # un codice nuovo a ogni avvio dal guardiano: il battito lo riporta


def write_json(path: Path, data: dict) -> None:
    """Scrittura atomica: chi legge non trova mai un file a metà."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: qualcuno (il guardiano, l'antivirus) lo sta leggendo proprio ora
            if attempt == 4:
                raise
            time.sleep(0.02)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def current_commit(root: Path = paths.ROOT) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def clean_commit(root: Path = paths.ROOT) -> str | None:
    """Il commit in uso, ma solo se non ci sono modifiche non salvate: una prova a metà non è una versione."""
    commit = current_commit(root)
    if commit is None:
        return None
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True,
                            text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return commit if st.returncode == 0 and not st.stdout.strip() else None


def known_good(state_dir: Path = paths.STATE_DIR) -> str | None:
    data = read_json(state_dir / KNOWN_GOOD)
    return data.get("commit") if data else None


class Heartbeat:
    def __init__(self, state_dir: Path = paths.STATE_DIR, interval_s: float = 5.0, healthy_after_s: float = 300.0,
                 commit: str | None = None, clock: Callable[[], float] = time.time) -> None:
        self.state_dir = state_dir
        self.interval_s = interval_s
        self.healthy_after_s = healthy_after_s
        self.commit = commit
        self.clock = clock
        self.started = clock()
        self.marked_good = False

    def beat(self, status: str = "ok") -> dict:
        now = self.clock()
        # "run": il guardiano riconosce il battito da qui; il pid non basta, perché il pythonw del venv è un
        # lanciatore e Jarvis gira in un processo figlio con un altro pid
        data = {"pid": os.getpid(), "run": os.environ.get(RUN_ID_ENV), "ts": now, "status": status,
                "uptime_s": round(now - self.started, 1), "commit": self.commit}
        write_json(self.state_dir / HEARTBEAT, data)
        if not self.marked_good and self.commit and now - self.started >= self.healthy_after_s:
            write_json(self.state_dir / KNOWN_GOOD, {"commit": self.commit, "ts": now})
            self.marked_good = True
            log.info("versione sana: %s", self.commit[:10])
        return data

    async def run(self, status: Callable[[], str] = lambda: "ok") -> None:
        while True:
            try:
                await asyncio.to_thread(self.beat, status())
            except OSError as e:  # disco pieno o file bloccato: il battito riprova al giro dopo
                log.warning("battito non scritto: %s", e)
            await asyncio.sleep(self.interval_s)


def take_recovery_note(state_dir: Path = paths.STATE_DIR) -> dict | None:
    """La nota lasciata dal supervisore prima del riavvio (letta una volta sola)."""
    path = state_dir / RECOVERY
    note = read_json(path)
    path.unlink(missing_ok=True)
    return note


def recovery_sentence(note: dict, address: str = "signore") -> str:
    if note.get("reason") == "restart":
        return f"Eccomi di nuovo, {address}: riavvio completato."
    why = {"hang": "da un blocco", "crash": "da un arresto imprevisto"}.get(note.get("reason", ""), "da un errore")
    text = f"Mi sono ripreso {why}, {address}."
    if note.get("rollback"):
        text += " Per sicurezza sto usando la versione precedente del mio codice, finché quella nuova non è sistemata."
    return text


def free_vram_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def ears_device_for(wanted: str, free_mb: int | None) -> str:
    """Dove mettere l'udito: sulla scheda video solo se ci stanno voce e udito insieme, altrimenti sul processore
    (un po' più lento, ma meglio che finire nella memoria condivisa, 10 volte più lenta)."""
    if wanted == "cuda" and free_mb is not None and free_mb < VOICE_VRAM_MB + EARS_VRAM_MB:
        return "cpu"
    return wanted


def preflight(cfg, model_path: Callable[[str], str], find_cli: Callable[[], str],
              vram: Callable[[], int | None] = free_vram_mb, has_microphone: Callable[[], bool] | None = None,
              engine_exe: Path | None = None) -> list[str]:
    """Problemi trovati prima di partire, già in forma da dire a voce. Lista vuota = tutto a posto."""
    problems: list[str] = []
    exe = engine_exe or paths.AUDIOCPP_SERVER
    if not exe.exists():
        problems.append("Non trovo il motore della voce: andrà reinstallato.")
    for key, what in ((cfg.speech.stt_model, "il modello dell'udito"), (cfg.speech.tts_model, "il modello della voce"),
                      ("supertonic", "la voce di riserva")):
        try:
            model_path(key)
        except Exception:  # EngineError o simili: il file non c'è
            problems.append(f"Manca {what}.")
    if cfg.brain.provider == "claude":
        try:
            find_cli()
        except Exception:
            problems.append("Non trovo Claude Code: userò il cervello di riserva.")
    free = vram()
    if free is not None and free < VOICE_VRAM_MB:
        gb = f"{round(free / 1024, 1):g}".replace(".", ",")  # "3" o "3,5": si legge bene a voce
        problems.append(f"La memoria video è quasi piena, restano {gb} gigabyte: la mia voce potrebbe "
                        "essere lenta. Forse è aperto un gioco o un altro modello.")
    elif free is not None and ears_device_for(cfg.speech.ears_device, free) != cfg.speech.ears_device:
        problems.append("La memoria video è un po' piena: per stavolta l'udito resta sul processore, quindi "
                        "capirò le frasi lunghe un po' più lentamente.")
    if has_microphone is not None and not has_microphone():
        problems.append("Non trovo nessun microfono: posso solo leggere quello che scrive.")
    return problems

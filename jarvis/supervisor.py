"""Il guardiano: tiene in vita Jarvis da fuori.

Uso: python -m jarvis.supervisor [opzioni di jarvis, es. --console]

- Avvia Jarvis come processo figlio. Uscita con codice 0 = chiusura voluta: il guardiano si ferma.
- Caduta (codice diverso da 0): rapporto in data/logs/cadute/, nota di ripresa per Jarvis, riavvio con
  attese crescenti.
- Blocco: se il battito (data/state/heartbeat.json) non si aggiorna per troppo tempo, Jarvis è fermo anche
  se il processo esiste. Si chiude e si riavvia.
- Tre cadute in dieci minuti con un codice diverso dall'ultima versione sana: si fa girare quella versione
  da una cartella a parte (git worktree), sopra i motori, i modelli e i dati veri. I file di lavoro non si
  toccano mai. Se cade anche quella, il guardiano si arrende e lo scrive, invece di girare all'infinito.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import health, paths, single

log = logging.getLogger("jarvis.supervisor")


@dataclass
class Policy:
    backoff_s: tuple[float, ...] = (2.0, 5.0, 15.0, 30.0)
    crash_window_s: float = 600.0
    crashes_for_rollback: int = 3
    heartbeat_timeout_s: float = 90.0
    startup_grace_s: float = 180.0  # caricare i motori richiede tempo: prima del primo battito si aspetta di più
    poll_s: float = 1.0


def decide(crash_times: list[float], now: float, policy: Policy, current: str | None, good: str | None,
           rolled_back: bool) -> str:
    """Dopo una caduta: "restart", "rollback" o "give_up"."""
    recent = [t for t in crash_times if now - t <= policy.crash_window_s]
    if len(recent) < policy.crashes_for_rollback:
        return "restart"
    if rolled_back:
        return "give_up"  # cade anche la versione sana: il problema non è il codice nuovo
    if good and current and good != current:
        return "rollback"
    return "give_up"


def prepare_known_good(root: Path, commit: str, dest: Path) -> Path:
    """La versione sana in una cartella a parte (git worktree). Si riusa se è già quella giusta."""
    head = health.current_commit(dest) if dest.exists() else None
    if head == commit:
        return dest
    if dest.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(dest)], cwd=root, capture_output=True, timeout=60)
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True, timeout=60)
    r = subprocess.run(["git", "worktree", "add", "--detach", str(dest), commit], cwd=root, capture_output=True,
                       text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add fallito: {r.stderr.strip()}")
    return dest


def has_console() -> bool:
    """Il processo ha una finestra di console (avvio da "Avvia Jarvis.bat"); con pythonw no."""
    import ctypes

    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except (AttributeError, OSError):
        return True  # fuori da Windows: nessun flag speciale


def crash_report(logs_dir: Path, reason: str, code: int | None, log_file: Path, lines: int = 120) -> Path:
    out_dir = logs_dir / "cadute"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / time.strftime("%Y%m%d_%H%M%S.txt")
    try:
        tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        tail = ["(registro non leggibile)"]
    path.write_text(f"motivo: {reason}\ncodice d'uscita: {code}\nora: {time.ctime()}\n\n--- ultime righe del "
                    f"registro ---\n" + "\n".join(tail) + "\n", encoding="utf-8")
    return path


class Supervisor:
    def __init__(self, args: list[str], root: Path = paths.ROOT, state_dir: Path = paths.STATE_DIR,
                 logs_dir: Path | None = None, policy: Policy | None = None,
                 child_cmd: Callable[[Path], list[str]] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.args = args
        self.root = root
        self.state_dir = state_dir
        self.logs_dir = logs_dir or (paths.DATA_DIR / "logs")
        self.policy = policy or Policy()
        self.child_cmd = child_cmd or (lambda code_root: [self._python(), "-m", "jarvis", *self.args])
        self.clock = clock
        self.sleep = sleep
        self.crashes: deque[float] = deque(maxlen=20)
        self.rolled_back = False
        self.code_root = root
        self.restarts = 0
        self.outcome = ""

    def _python(self) -> str:
        # sempre python.exe, anche con l'avvio automatico (pythonw): senza una console sua, ogni programma a riga
        # di comando lanciato da Jarvis (la CLI di Claude a ogni sessione, node, git) apriva una finestra di
        # Terminale (26/10, l'utente). Con una console nascosta (vedi _creationflags) la ereditano e non si vede niente
        venv = self.root / ".venv" / "Scripts" / "python.exe"
        return str(venv if venv.exists() else Path(sys.executable).with_name("python.exe"))

    @staticmethod
    def _creationflags() -> int:
        """Console nascosta se il guardiano non ne ha una (avvio con pythonw); altrimenti Jarvis usa la sua."""
        return subprocess.CREATE_NO_WINDOW if not has_console() else 0

    def _env(self) -> dict:
        env = dict(os.environ, JARVIS_SUPERVISED="1")
        if self.code_root != self.root:  # versione sana in un'altra cartella: motori, modelli e dati restano quelli veri
            env.update(JARVIS_ENGINE_DIR=str(paths.ENGINE_DIR), JARVIS_MODELS_DIR=str(paths.MODELS_DIR),
                       JARVIS_DATA_DIR=str(paths.DATA_DIR))
        return env

    def run_child(self) -> tuple[str, int | None]:
        """Fa girare Jarvis finché non esce o si blocca. -> ("exit"|"hang", codice)"""
        run_id = uuid.uuid4().hex
        proc = subprocess.Popen(self.child_cmd(self.code_root), cwd=self.code_root,
                                env={**self._env(), health.RUN_ID_ENV: run_id}, creationflags=self._creationflags())
        started = self.clock()
        last_beat: float | None = None  # ora dell'ultimo battito riconosciuto di questo avvio
        try:
            while True:
                code = proc.poll()
                if code is not None:
                    return "exit", code
                beat = health.read_json(self.state_dir / health.HEARTBEAT)
                if beat and beat.get("run") == run_id:
                    last_beat = float(beat.get("ts", 0))
                # una lettura fallita (file sostituito proprio in quell'istante, antivirus) non cancella l'ultimo
                # battito: prima l'età diventava "dall'avvio" e un Jarvis sano veniva ucciso
                if last_beat is None:
                    age, limit = self.clock() - started, self.policy.startup_grace_s
                else:
                    age, limit = max(0.0, time.time() - last_beat), self.policy.heartbeat_timeout_s
                if age > limit:
                    log.error("Jarvis non batte da %.0f s: lo chiudo e lo riavvio", age)
                    proc.kill()
                    proc.wait(timeout=30)
                    return "hang", proc.returncode
                self.sleep(self.policy.poll_s)
        except KeyboardInterrupt:
            proc.wait(timeout=30)  # il Ctrl+C arriva anche a Jarvis, che si chiude da solo
            raise

    def run(self) -> int:
        log.info("guardiano avviato")
        while True:
            reason, code = self.run_child()
            if reason == "exit" and code == 0:
                self.outcome = "chiuso"
                log.info("Jarvis chiuso normalmente")
                return 0
            if reason == "exit" and code == health.RESTART_CODE:  # l'ha chiesto lui: niente rapporto, subito
                log.info("Jarvis ha chiesto di essere riavviato")
                health.write_json(self.state_dir / health.RECOVERY, {"reason": "restart", "rollback": self.rolled_back})
                self.restarts += 1
                continue
            why = "hang" if reason == "hang" else "crash"
            report = crash_report(self.logs_dir, why, code, self.logs_dir / "jarvis.log")
            log.error("Jarvis %s (codice %s): rapporto in %s", "bloccato" if why == "hang" else "caduto", code, report)
            self.crashes.append(self.clock())
            action = decide(list(self.crashes), self.clock(), self.policy, health.current_commit(self.root),
                            health.known_good(self.state_dir), self.rolled_back)
            if action == "give_up":
                self.outcome = "arreso"
                log.critical("Jarvis continua a cadere: mi fermo. Gli ultimi rapporti sono in %s", report.parent)
                return 1
            if action == "rollback":
                good = health.known_good(self.state_dir)
                assert good is not None
                self.code_root = prepare_known_good(self.root, good, self.state_dir / "known_good_tree")
                self.rolled_back = True
                log.warning("torno alla versione sana %s", good[:10])
            health.write_json(self.state_dir / health.RECOVERY,
                              {"reason": why, "code": code, "rollback": self.rolled_back, "report": str(report)})
            wait = self.policy.backoff_s[min(self.restarts, len(self.policy.backoff_s) - 1)]
            self.restarts += 1
            log.info("riavvio tra %.0f s", wait)
            self.sleep(wait)


def main() -> int:
    from . import log as jlog

    ap = argparse.ArgumentParser(prog="jarvis.supervisor", description="Tiene in vita Jarvis: riavvio, blocchi, "
                                 "ritorno all'ultima versione sana.")
    _, rest = ap.parse_known_args()
    jlog.setup(filename="guardiano.log")
    sup = Supervisor(rest)
    if os.environ.get("JARVIS_SUPERVISOR_DRYRUN") == "1":  # prova dell'avvio: dice cosa farebbe e basta
        log.info("prova di avvio: lancerei %s nella cartella %s", sup.child_cmd(sup.code_root), sup.code_root)
        return 0
    if not single.acquire(single.GUARDIAN):  # c'è già un guardiano (es. avvio automatico): si mostra Jarvis
        from .config import ConfigStore

        single.show_running_window(ConfigStore().config.ui.port)
        return 0
    try:
        return sup.run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

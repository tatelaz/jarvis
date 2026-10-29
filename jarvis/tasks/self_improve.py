"""Auto-miglioramento: Jarvis modifica il proprio codice senza mai rischiare quello che funziona.

1. Copia separata del codice (git worktree, ramo miglioria/N): la cartella principale non si tocca.
2. Un sotto-agente fa la modifica lì, con i test, e la salva nel ramo.
3. Jarvis non si fida sulla parola: rifà i controlli da sé (lint + test rapidi) nella copia.
4. Chiede all'utente. Solo con un sì unisce il ramo al codice principale e rifà i controlli; se falliscono
   torna esattamente al commit di prima (sicuro: prima di unire la cartella principale deve essere pulita).
5. Il codice nuovo entra in funzione al riavvio; se continua a cadere, il guardiano torna all'ultima
   versione sana.

La copia non ha il suo .venv né i modelli (git li ignora): si usano quelli veri attraverso variabili
d'ambiente, senza collegamenti nella cartella (cancellando la copia non si rischia di cancellare gli originali).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .. import paths
from .manager import Task, Worker, WorkerHooks

log = logging.getLogger("jarvis.self_improve")

GOAL = """Stai migliorando il codice di JARVIS stesso: sei un suo sotto-agente. Lavori in una copia separata del \
progetto, nella cartella {wt} (ramo {branch}). Non toccare niente fuori da qui.

Richiesta di {user}: {request}

Come lavorare:
- Prima leggi docs/STATO.md e docs/ARCHITETTURA.md, poi il codice che riguarda la richiesta.
- Fai la modifica più piccola e pulita che soddisfa la richiesta, nello stile del codice intorno (commenti in italiano).
- Aggiungi o aggiorna i test in tests/ che dimostrano che funziona.
- Se la novità ha un'impostazione, mettila in jarvis/config.py con un commento: Jarvis la deve poter cambiare a voce.
- Controllo obbligatorio, finché è tutto verde: bash scripts/check.sh (lint + test rapidi; usa già il Python giusto).
- Salva nel ramo: git add -A e git commit -m "messaggio chiaro in italiano". Niente merge, push o altri rami.
- Alla fine rispondi con 2-3 frasi: cosa hai cambiato e come si nota.
"""


def git(root: Path, *args: str, check: bool = True, timeout: float = 120) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def check_env(root: Path) -> dict[str, str]:
    """Ambiente per controllare una copia: il Python, i motori e i modelli veri."""
    python = root / ".venv" / "Scripts" / "python.exe"
    env = {"JARVIS_ENGINE_DIR": str(root / "engine"), "JARVIS_MODELS_DIR": str(root / "models")}
    if python.exists():
        env["JARVIS_PYTHON"] = str(python)
    return env


def short(text: str, limit: int = 300) -> str:
    """Accorcia a fine frase, mai a metà parola (la richiesta di conferma si legge ad alta voce)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > limit // 3:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def git_bash() -> str:
    """Il bash di Git per Windows, con il percorso completo.

    "bash" da solo, lanciato da Python, apre quello di WSL: Windows cerca in System32 prima che nel PATH.
    Quello gira in un Linux a parte e non vede né le variabili d'ambiente né i percorsi di Windows
    (scoperto con la prova dal vivo: i controlli fallivano con ".venv/Scripts/python: No such file")."""
    git_exe = shutil.which("git")
    if git_exe:
        for folder in Path(git_exe).resolve().parents:
            for candidate in (folder / "bin" / "bash.exe", folder / "usr" / "bin" / "bash.exe"):
                if candidate.exists():
                    return str(candidate)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    raise FileNotFoundError("non trovo il bash di Git per Windows")


def run_check(where: Path, root: Path, command: list[str] | None = None, timeout: float = 900) -> tuple[bool, str]:
    """Lint + test rapidi in una cartella. -> (verde?, ultime righe dell'esito)"""
    cmd = command or [git_bash(), "scripts/check.sh"]
    try:
        r = subprocess.run(cmd, cwd=where, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **check_env(root)})
    except subprocess.TimeoutExpired:
        return False, "controlli scaduti"
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
    return r.returncode == 0, tail


class SelfImproveWorker:
    """Avvolge un sotto-agente normale con la copia separata, i controlli, la conferma e l'unione sicura."""

    def __init__(self, task: Task, inner_factory: Callable[[Task, Path, dict[str, str]], Worker], user: str,
                 root: Path = paths.ROOT, check_command: list[str] | None = None) -> None:
        self.task = task
        self.inner_factory = inner_factory
        self.user = user
        self.root = root
        self.check_command = check_command
        self.branch = f"miglioria/{time.strftime('%Y%m%d-%H%M%S')}-{task.id}"
        self.worktree = root / "data" / "migliorie" / self.branch.replace("/", "_")
        self.inner: Worker | None = None

    def _prepare(self) -> None:
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, "worktree", "add", "-b", self.branch, str(self.worktree), "HEAD")

    def _cleanup(self, keep_branch: bool) -> None:
        git(self.root, "worktree", "remove", "--force", str(self.worktree), check=False)
        git(self.root, "worktree", "prune", check=False)
        if not keep_branch:
            git(self.root, "branch", "-D", self.branch, check=False)

    async def run(self, task: Task, hooks: WorkerHooks) -> str:
        request = task.goal
        await asyncio.to_thread(self._prepare)
        merged, keep = False, True
        try:
            task.goal = GOAL.format(wt=self.worktree, branch=self.branch, user=self.user, request=request)
            self.inner = self.inner_factory(task, self.worktree, check_env(self.root))
            summary = await self.inner.run(task, hooks)
            hooks.progress("ricontrollo la modifica da solo")
            ok, tail = await asyncio.to_thread(run_check, self.worktree, self.root, self.check_command)
            if not ok:
                return (f"Modifica NON applicata: i miei controlli non passano. Ultime righe: {tail}. "
                        f"Il lavoro resta nel ramo {self.branch}.")
            commits = git(self.root, "log", "--oneline", f"HEAD..{self.branch}")
            if not commits:
                keep = False
                return f"Nessuna modifica salvata dal sotto-agente. Diceva: {summary}"
            if git(self.root, "status", "--porcelain", "--untracked-files=no"):
                return (f"La modifica è pronta e verde, ma la cartella principale ha cambiamenti non salvati: non la "
                        f"unisco per non mescolarli. Resta nel ramo {self.branch}.")
            n = len(commits.splitlines())
            if not await hooks.confirm(f"Applico al mio codice la modifica «{task.title}» ({n} commit, controlli "
                                       f"verdi)? {short(summary)}"):
                keep = False
                return "Modifica scartata, come richiesto."
            before = git(self.root, "rev-parse", "HEAD")
            await asyncio.to_thread(git, self.root, "merge", "--no-edit", self.branch)
            ok, tail = await asyncio.to_thread(run_check, self.root, self.root, self.check_command)
            if not ok:  # sicuro: la cartella principale era pulita prima dell'unione
                await asyncio.to_thread(git, self.root, "reset", "--hard", before)
                return (f"Ho unito la modifica ma i controlli sul codice principale non passavano: sono tornato "
                        f"com'ero. Ultime righe: {tail}. Il lavoro resta nel ramo {self.branch}.")
            merged, keep = True, False
            log.info("miglioria unita: %s", self.branch)
            return (f"Modifica applicata al mio codice: {summary} Entra in funzione al prossimo riavvio: chiedi a "
                    f"{self.user} se riavviarti ora.")
        finally:
            # il ramo resta solo se contiene lavoro non applicato da guardare (controlli rossi, cartella sporca...)
            await asyncio.to_thread(self._cleanup, keep and not merged)

    async def send(self, text: str) -> None:
        if self.inner is not None:
            await self.inner.send(text)

    async def cancel(self) -> None:
        if self.inner is not None:
            await self.inner.cancel()

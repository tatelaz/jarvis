"""Prova dal vivo dell'auto-miglioramento con Claude vero, senza toccare il codice principale.

Un sotto-agente vero fa una modifica innocua nella copia separata; alla richiesta di conferma la prova
risponde NO: si verifica tutto il percorso (copia, controlli, richiesta) tranne l'unione, già coperta dai test.

Uso: python scripts/self_improve_live.py [--effort=medium]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import log, paths
from jarvis.config import Config
from jarvis.tasks.claude_worker import ClaudeWorker
from jarvis.tasks.manager import Task, WorkerHooks
from jarvis.tasks.safety import SafetyPolicy
from jarvis.tasks.self_improve import SelfImproveWorker, git

REQUEST = ("Aggiungi la frase \"Agli ordini, signore.\" all'elenco COMMON_PHRASES in jarvis/speech/voice.py "
           "(le frasi pronte in anticipo), e un test che verifichi che è nell'elenco.")


async def main(effort: str) -> dict:
    cfg = Config()
    task = Task(1, "frase pronta in più", REQUEST, paths.DATA_DIR / "workspace" / "_prova_miglioria", kind="miglioria")
    progress: list[str] = []
    asked: list[str] = []
    branches_before = git(paths.ROOT, "branch", "--list", "miglioria/*")
    head_before = git(paths.ROOT, "rev-parse", "HEAD")

    async def confirm(text: str) -> bool:
        asked.append(text)
        return False  # la prova non applica mai

    def inner(t, worktree, env):
        t.cwd = worktree
        return ClaudeWorker(t, cfg.persona.user_name, cfg.brain.worker_claude_model, effort,
                            SafetyPolicy([worktree], True), env_extra=env)

    w = SelfImproveWorker(task, inner, cfg.persona.user_name)
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(w.run(task, WorkerHooks(progress=progress.append, confirm=confirm)), 1500)
        error = None
    except Exception as e:  # è una prova: si riporta tutto
        result, error = None, repr(e)
    return {"secondi": round(time.perf_counter() - t0), "risultato": result, "errore": error, "conferme": asked,
            "passi": progress[-25:], "costo_usd": round(task.cost_usd, 3),
            "codice_principale_intatto": git(paths.ROOT, "rev-parse", "HEAD") == head_before
            and not git(paths.ROOT, "status", "--porcelain", "--untracked-files=no"),
            "rami_rimasti": git(paths.ROOT, "branch", "--list", "miglioria/*") != branches_before,
            "copia_rimossa": not w.worktree.exists()}


if __name__ == "__main__":
    log.setup()
    effort = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--effort=")), "medium")
    print(json.dumps(asyncio.run(main(effort)), ensure_ascii=False, indent=1))

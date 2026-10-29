"""Prova dal vivo delle mani dei sotto-agenti: un compito vero per ciascuna, con Claude vero.

Uso: python scripts/hands_live.py [browser|mio_browser|desktop ...]
Le conferme vengono negate in automatico: un compito innocuo non deve chiederne.
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
from jarvis.tasks.hands import DesktopLease, build_hands
from jarvis.tasks.manager import Task, WorkerHooks
from jarvis.tasks.safety import SafetyPolicy

GOALS = {
    "browser": "Usa il browser usa e getta: apri https://example.com e dimmi il titolo principale della pagina "
               "e il testo del link che contiene.",
    "mio_browser": "Usa il browser dell'utente: apri https://example.com in una nuova scheda del tuo gruppo, leggi il "
                   "titolo principale della pagina, poi chiudi quella scheda. Non toccare altre schede.",
    "desktop": "Usa gli strumenti windows: apri la Calcolatrice di Windows, calcola 37 per 43 premendo i tasti, "
               "leggi il risultato sullo schermo e poi chiudi la Calcolatrice.",
}


async def run(kind: str, effort: str) -> dict:
    cfg = Config()
    ws = paths.DATA_DIR / "workspace" / "_prove_mani"
    task = Task(1, kind, GOALS[kind], ws)
    progress: list[str] = []
    asked: list[str] = []

    async def confirm(text: str) -> bool:
        asked.append(text)
        return False

    w = ClaudeWorker(task, cfg.persona.user_name, cfg.brain.worker_claude_model, effort, SafetyPolicy([ws]),
                     hands=build_hands(cfg.hands, cfg.persona.user_name), desktop=DesktopLease())
    t = time.perf_counter()
    try:
        result = await asyncio.wait_for(w.run(task, WorkerHooks(progress=progress.append, confirm=confirm)), 300)
        error = None
    except Exception as e:  # è una prova: si riporta tutto
        result, error = None, repr(e)
    return {"prova": kind, "secondi": round(time.perf_counter() - t, 1), "risultato": result, "errore": error,
            "conferme_chieste": asked, "passi": progress, "costo_usd": round(task.cost_usd, 4)}


async def main(kinds: list[str], effort: str) -> list[dict]:
    out = []
    for k in kinds:
        r = await run(k, effort)
        print(json.dumps(r, ensure_ascii=False, indent=1), flush=True)
        out.append(r)
    return out


if __name__ == "__main__":
    log.setup()
    args = [a for a in sys.argv[1:] if not a.startswith("--")] or list(GOALS)
    effort = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--effort=")), "medium")
    asyncio.run(main(args, effort))

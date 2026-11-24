"""Avvio: python -m jarvis [--console] [--no-window] [--debug] [--setup]"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from . import log, single, winjob

ROOT = Path(__file__).resolve().parents[1]
PYTHONW = Path(sys.executable).with_name("pythonw.exe")


async def _console(app) -> None:
    """Mostra a console ciò che succede (utile per la diagnosi)."""
    q = app.bus.subscribe()
    while True:
        ev = await q.get()
        t = ev["type"]
        if t == "user":
            print(f"\n  TU     › {ev['text']}")
        elif t == "partial":
            print(f"  …      › {ev['text']}", end="\r")
        elif t == "jarvis_done":
            print(f"  JARVIS › {ev['text']}")
        elif t == "tool":
            print(f"         [strumento {ev['name']} {ev.get('args')}]")
        elif t in ("error", "interrupted", "notice", "ready", "ignored", "first_audio", "task", "engine"):
            print(f"         [{t}] { {k: v for k, v in ev.items() if k not in ('type', 'ts')} }")


def early_heartbeat(state_dir: Path | None = None, interval_s: float = 5.0) -> asyncio.Task | None:
    """Il battito per il guardiano già dall'inizio, finché non parte quello di Jarvis acceso (dentro app.start). Il
    primo avvio scarica qualche GB e dura minuti: senza battito il guardiano dopo 3 minuti lo credeva bloccato, lo
    chiudeva e alla terza volta si arrendeva (24/11, la prima installazione vera da GitHub). Solo sotto il guardiano."""
    if os.environ.get("JARVIS_SUPERVISED") != "1":
        return None
    from . import health, paths

    beat = health.Heartbeat(state_dir=state_dir or paths.STATE_DIR, interval_s=interval_s)
    return asyncio.ensure_future(beat.run(lambda: "avvio"))


def make_repo() -> str | None:
    """jarvis/localrepo.py: mai un motivo per non partire. Cosa dire all'utente, se c'è."""
    from . import localrepo

    try:
        return localrepo.ensure_repo().said()
    except Exception:  # Git che non risponde, un bundle rovinato: Jarvis parte lo stesso, come prima
        logging.getLogger("jarvis").exception("copia installata: repository non creato")
        return None


async def main(console: bool, window: bool, setup: bool = False) -> int:
    from . import certs

    # prima di ogni download e PRIMA di importare aiohttp, che prepara la sua verifica dei certificati una volta sola,
    # quando lo si importa (provato in Windows Sandbox: chiamato dopo, i download fallivano lo stesso)
    certs.trust_windows()
    early = early_heartbeat()  # primo avvio, download, motori: il guardiano non deve crederlo bloccato
    try:
        return await _run(console, window, setup, early)
    finally:
        if early is not None:
            early.cancel()


async def _run(console: bool, window: bool, setup: bool, early: asyncio.Task | None) -> int:
    from . import toolbox
    from .config import ConfigStore
    from .setup import flow
    from .vcrt import LOADED

    if LOADED:  # caricate da jarvis/__init__.py: il PC non aveva il runtime di Visual C++ (o l'aveva più vecchio)
        logging.getLogger("jarvis").info("runtime di Visual C++ preso da tools/vcrt: %s", ", ".join(LOADED))
    toolbox.use_downloaded_tools()  # OpenCode, Node.js e Git scaricati dal primo avvio, se il PC non li aveva

    missing = flow.missing_files(ConfigStore().config)
    if setup or missing:  # PC nuovo (o file spariti): prima il primo avvio, poi Jarvis
        if not window:
            logging.getLogger("jarvis").error("mancano %s: serve il primo avvio, con la finestra (python -m jarvis "
                                              "--setup)", ", ".join(p.name for p in missing) or "le scelte")
            return 2
        from .setup.run import run as run_setup

        if not await run_setup(ConfigStore()):
            return 0  # chiuso prima della fine: la prossima volta si riprende da lì
        toolbox.use_downloaded_tools()  # quelli appena scaricati
    repo_note = await asyncio.to_thread(make_repo)  # la copia installata diventa un repository (serve Git: dopo il setup)
    from .app import Jarvis

    app = Jarvis()
    win: asyncio.subprocess.Process | None = None
    try:
        await app.start()
        if early is not None:  # da qui batte Jarvis acceso (app.start), con la versione sana
            early.cancel()
        if repo_note and app.conv is not None:
            app.conv.notify(repo_note)
        if window:
            exe = PYTHONW if PYTHONW.exists() else Path(sys.executable)
            win = await asyncio.create_subprocess_exec(str(exe), "-m", "jarvis.ui.window", "--port",
                                                       str(app.cfg.ui.port), cwd=str(ROOT))
            winjob.adopt(win.pid)  # se Jarvis cade o viene chiuso dal guardiano, la finestra non resta orfana
        printer = asyncio.ensure_future(_console(app)) if console else None
        await app.exit_requested.wait()  # chiusura voluta o riavvio chiesto a voce
        if printer:
            printer.cancel()
    finally:
        if win and win.returncode is None:
            win.terminate()
        await app.stop()
    return app.exit_code


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="jarvis")
    ap.add_argument("--console", action="store_true", help="mostra la conversazione nel terminale")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-window", action="store_true", help="non aprire la finestra")
    ap.add_argument("--setup", action="store_true", help="rifà il primo avvio (scelte e download)")
    a = ap.parse_args()
    log.setup(logging.DEBUG if a.debug else logging.INFO)
    if not single.acquire(single.APP):  # già acceso: si apre la sua finestra, niente seconda copia
        from .config import ConfigStore

        logging.getLogger("jarvis").info("Jarvis è già acceso")
        single.show_running_window(ConfigStore().config.ui.port)
        sys.exit(0)
    code = 0
    try:
        with contextlib.suppress(KeyboardInterrupt):
            code = asyncio.run(main(a.console, not a.no_window, a.setup))
    except Exception:  # la causa finisce nel registro: il guardiano la mette nel rapporto della caduta
        logging.getLogger("jarvis").exception("Jarvis è caduto")
        sys.exit(1)
    sys.exit(code)

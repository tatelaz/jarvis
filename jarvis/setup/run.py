"""Il primo avvio dentro `python -m jarvis`: server, finestra, attesa della fine. Poi parte Jarvis.

Parte da solo quando manca qualcosa senza cui Jarvis non partirebbe (`flow.missing_files`), oppure con
`python -m jarvis --setup` per rifarlo. Chiuso prima della fine, Jarvis non parte: la prossima volta si riprende
(i download dalla parte salvata, le scelte dalla configurazione).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from .. import paths, winjob
from ..config import ConfigStore
from .download import Downloader
from .flow import Setup
from .server import SetupServer

log = logging.getLogger("jarvis.setup")
PYTHONW = Path(sys.executable).with_name("pythonw.exe")


def make_setup(store: ConfigStore) -> Setup:
    downloader = Downloader({"engine": paths.ENGINE_DIR, "models": paths.MODELS_DIR},
                            paths.DATA_DIR / "setup" / "scaricati.json")
    return Setup(store, downloader)


async def run(store: ConfigStore, window: bool = True) -> bool:
    """True se il primo avvio è finito bene e Jarvis può partire."""
    setup = make_setup(store)
    server = SetupServer(setup, port=store.config.ui.port)
    await server.start()
    win: asyncio.subprocess.Process | None = None
    try:
        if window:
            exe = PYTHONW if PYTHONW.exists() else Path(sys.executable)
            win = await asyncio.create_subprocess_exec(str(exe), "-m", "jarvis.ui.window", "--port",
                                                       str(store.config.ui.port), cwd=str(paths.ROOT))
            winjob.adopt(win.pid)
        waits = [asyncio.ensure_future(setup.finished.wait()), asyncio.ensure_future(setup.quit.wait())]
        if win is not None:
            waits.append(asyncio.ensure_future(win.wait()))  # finestra chiusa (Alt+F4) = chiuso prima della fine
        _, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        if setup.finished.is_set():
            await asyncio.sleep(1.2)  # il saluto della pagina, prima di chiudere
        return setup.finished.is_set()
    finally:
        await setup.cancel_download()  # la parte scaricata resta: la prossima volta si riprende da lì
        setup.stop_mic()
        if win is not None and win.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                win.terminate()
        await server.stop()

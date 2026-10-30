"""La pagina del primo avvio con i dati veri di questo PC, ma senza toccare niente: per guardarla e provarla.

Controllo del PC e stato di Claude Code sono quelli veri; configurazione e cartelle stanno in una cartella
temporanea; il download è simulato (nessun file scaricato). Si apre nel browser su una porta a parte, quindi va
anche con Jarvis acceso. I pulsanti di Claude Code sono veri: "Accedi" apre davvero il login ufficiale.
Uso: python scripts/primo_avvio_prova.py [--porta 8799]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.config import ConfigStore
from jarvis.setup import pc
from jarvis.setup.flow import Setup
from jarvis.setup.server import SetupServer

SPEED = 60 * 2**20  # byte al secondo del download finto


class SimulatedDownloader:
    """Fa finta di scaricare tutto, alla velocità di una buona connessione."""

    def __init__(self, root: Path) -> None:
        self.roots = {"engine": root / "engine", "models": root / "models"}

    def target(self, dest: str) -> Path:
        r, _, rest = dest.partition("/")
        return self.roots[r] / rest

    async def missing(self, files):
        return list(files)

    async def run(self, files, report):
        files = list(files)
        total, done = sum(f.size for f in files), 0
        for f in files:
            got = 0
            while got < f.size:
                await asyncio.sleep(0.1)
                got = min(f.size, got + SPEED // 10)
                report(done + got, total, f.dest)
            done += f.size


async def main(port: int) -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-primo-avvio-") as tmp:
        root = Path(tmp)
        store = ConfigStore(root / "config.yaml")
        setup = Setup(store, SimulatedDownloader(root), gather=lambda: pc.gather(root),
                      voices_dir=root / "voices", done_path=root / "fatto.json")
        server = SetupServer(setup, port=port)
        await server.start()
        print(f"primo avvio di prova su http://127.0.0.1:{port}/ (Ctrl+C per chiudere)")
        await asyncio.wait([asyncio.ensure_future(setup.finished.wait()), asyncio.ensure_future(setup.quit.wait())],
                           return_when=asyncio.FIRST_COMPLETED)
        await server.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8799)
    try:
        asyncio.run(main(ap.parse_args().porta))
    except KeyboardInterrupt:
        pass

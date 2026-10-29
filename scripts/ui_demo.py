"""Jarvis con la stanza simulata (niente microfono): per provare l'interfaccia in sicurezza."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import log
from jarvis.app import Jarvis
from jarvis.audio.devices import SimulatedRoom


async def main() -> None:
    app = Jarvis(backend=SimulatedRoom(speed=1.0))
    await app.start()
    try:
        await asyncio.Event().wait()
    finally:
        await app.stop()


if __name__ == "__main__":
    log.setup()
    asyncio.run(main())

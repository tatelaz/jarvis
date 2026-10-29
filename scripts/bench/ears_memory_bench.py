"""Quanta memoria prenota il motore delle orecchie (commit di Windows), con una data versione di audio.cpp.

audio.cpp prenota molta memoria virtuale: con le arene predefinite Parakeet arriva a ~4 GB di commit per 0,7 GB veri
e, con il PC carico, si esaurisce la memoria virtuale (cadute senza messaggio: 6/10 e 26/10). PARAKEET_SESSION le
riduce: questo controlla che la versione del motore le rispetti davvero.
Uso: set JARVIS_ENGINE_DIR=...  &&  python scripts/bench/ears_memory_bench.py [modello]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis import paths  # noqa: E402
from jarvis.speech import engine  # noqa: E402
from jarvis.speech.stt import transcribe_file  # noqa: E402


async def main(model: str) -> dict:
    eng = engine.EngineProcess(engine.ears_spec(8794, model=model))
    await eng.start(timeout=120)
    try:
        rng = np.random.default_rng(0)
        await transcribe_file(eng.host, eng.port, "stt", (0.05 * rng.standard_normal(16000 * 8)).astype(np.float32))
        mem = psutil.Process(eng.proc.pid).memory_info()
        return {"server": str(paths.AUDIOCPP_SERVER), "modello": model, "commit_mb": round(mem.private / 2**20),
                "ram_mb": round(mem.rss / 2**20)}
    finally:
        await eng.stop()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "parakeet-ultra")), ensure_ascii=False))

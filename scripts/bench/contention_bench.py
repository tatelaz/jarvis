"""La voce rallenta quando anche le orecchie sono sulla GPU? (VRAM satura → memoria condivisa, molto più lenta)

Uso: python scripts/bench/contention_bench.py
Tre casi: solo voce; voce + orecchie su GPU; voce + orecchie su CPU. Per ognuno: VRAM totale e tempi
di sintesi delle stesse frasi, con le orecchie che trascrivono in parallelo come durante una conversazione.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech import engine
from jarvis.speech.stt import transcribe_file
from jarvis.speech.tts import TtsClient, Voice

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vram_bench import vram_mb  # noqa: E402

PHRASES = ["Il diciassette dicembre del millenovecentotre, a Kitty Hawk, in Carolina del Nord,",
           "Orville Wright pilotò il primo volo controllato di un aeroplano a motore, per dodici secondi.",
           "Il fratello Wilbur, sebbene non fosse al posto di comando, seguiva il motore a quattro cilindri.",
           "Quell'esperimento segnò l'inizio dell'aviazione moderna."]


async def case(ears_backend: str | None) -> dict:
    voice = engine.load_voice("jarvis")
    v = engine.EngineProcess(engine.voice_spec(8797, voice, "qwen3-1.7b"))
    e = None
    if ears_backend:
        spec = engine.ears_spec(8798)
        spec.extra.update(backend=ears_backend, threads=8)
        e = engine.EngineProcess(spec)
    await asyncio.gather(v.start(), *([e.start()] if e else []))
    stop = False
    noise = (0.05 * np.random.default_rng(0).standard_normal(16000 * 3)).astype(np.float32)

    async def listening():  # le trascrizioni parziali che girano mentre si parla
        while not stop:
            await transcribe_file(e.host, e.port, "stt", noise)
            await asyncio.sleep(0.5)

    try:
        tts = TtsClient(v.host, v.port)
        if e:
            await transcribe_file(e.host, e.port, "stt", noise)
        await tts.synthesize("tts", "Sistemi in linea.", Voice(preset="jarvis", language="Italian"), seed=7)
        bg = asyncio.ensure_future(listening()) if e else None
        times = []
        for p in PHRASES:
            t = time.perf_counter()
            await tts.synthesize("tts", p, Voice(preset="jarvis", language="Italian"), seed=7)
            times.append(round(time.perf_counter() - t, 2))
        stop = True
        if bg:
            await bg
        return {"orecchie": ears_backend or "spente", "vram_mb": vram_mb(), "tempi_s": times,
                "media_s_per_100_car": round(100 * sum(times) / sum(len(p) for p in PHRASES), 2)}
    finally:
        await asyncio.gather(v.stop(), *([e.stop()] if e else []))


async def main() -> None:
    for backend in (None, "cuda", "cpu"):
        print(json.dumps(await case(backend), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

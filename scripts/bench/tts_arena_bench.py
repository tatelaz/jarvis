"""VRAM e velocità della voce di Jarvis al variare dell'arena del talker (qwen3_tts.talker_graph_arena_mb).

Uso: python scripts/bench/tts_arena_bench.py [mb ...]      (nessun valore = predefinito del motore)
Per ogni valore: VRAM occupata, tempo medio per frase, durata dell'audio (deve restare plausibile).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech import engine
from jarvis.speech.tts import TtsClient, Voice

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vram_bench import vram_mb  # noqa: E402

PHRASES = ["Certamente, signore.",
           "Ho preparato il rapporto che mi aveva chiesto questa mattina: tre pagine, con i grafici in fondo.",
           "Le ricordo che alle diciotto ha la riunione con il gruppo di progetto, e che il treno di domani "
           "parte alle sette e quaranta dal binario quattro."]


async def run(arena: int | None) -> dict:
    voice = engine.load_voice("jarvis")
    spec = engine.voice_spec(8797, voice, "qwen3-1.7b")
    if arena is not None:
        spec.models[0]["session_options"] = {"qwen3_tts.talker_graph_arena_mb": arena}
    before = vram_mb()
    eng = engine.EngineProcess(spec)
    await eng.start()
    try:
        tts = TtsClient(eng.host, eng.port)
        await tts.synthesize("tts", PHRASES[0], Voice(preset="jarvis", language="Italian"), seed=7)
        times, durs = [], []
        for p in PHRASES:
            t = time.perf_counter()
            audio, sr = await tts.synthesize("tts", p, Voice(preset="jarvis", language="Italian"), seed=7)
            times.append(round(time.perf_counter() - t, 2))
            durs.append(round(len(audio) / sr, 2))
        return {"arena_mb": arena or "predefinita", "vram_mb": vram_mb() - before, "tempi_s": times, "durate_s": durs}
    except Exception as e:  # una prova: si riporta
        return {"arena_mb": arena, "errore": repr(e)[:300]}
    finally:
        await eng.stop()


async def main(values: list[int | None]) -> None:
    for v in values:
        print(json.dumps(await run(v), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    vals = [int(a) for a in sys.argv[1:]] or [None]
    asyncio.run(main(vals))

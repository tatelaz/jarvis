"""Orecchie sulla scheda video o sul processore: velocità, memoria video e quanto si disturbano con la voce.

Il 6/10 le orecchie sulla GPU rallentavano la voce di 2x quando lavoravano insieme (buchi a metà frase): con
audio.cpp 0.9.2 la voce usa 1,1 GB in meno, quindi si rimisura tutto. Va lanciato a Jarvis SPENTO.
Uso: python scripts/bench/ears_gpu_bench.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bench"))

from tts_arena_bench import PHRASES  # noqa: E402
from vram_bench import vram_mb  # noqa: E402

from jarvis.speech import engine  # noqa: E402
from jarvis.speech.stt import transcribe_file  # noqa: E402
from jarvis.speech.tts import TtsClient, Voice  # noqa: E402

FLEURS = ROOT / "models" / "testdata" / "fleurs_it"
LENGTHS = (2, 5, 10, 20)


def clips() -> dict[int, np.ndarray]:
    audio = np.concatenate([sf.read(FLEURS / f"it_{i:03d}.wav", dtype="float32")[0] for i in range(10)])
    return {s: audio[: s * 16000] for s in LENGTHS}


async def stt_ms(eng, audio) -> float:
    t = time.perf_counter()
    await transcribe_file(eng.host, eng.port, "stt", audio, timeout=60)
    return 1000 * (time.perf_counter() - t)


async def tts_times(tts: TtsClient, rounds: int = 2) -> list[float]:
    v = Voice(preset="jarvis", language="Italian")
    out = []
    for _ in range(rounds):
        for p in PHRASES:
            t = time.perf_counter()
            await tts.synthesize("tts", p, v, seed=7)
            out.append(round(time.perf_counter() - t, 3))
    return out


async def partials(eng, audio, stop: asyncio.Event, lat: list[float]) -> None:
    """Come le trascrizioni parziali: tutto l'audio finora, ogni 450 ms, finché qualcuno parla."""
    while not stop.is_set():
        lat.append(await stt_ms(eng, audio))
        await asyncio.sleep(0.45)


async def main(device: str) -> dict:
    """Voce + orecchie su un dispositivo alla volta (tutti e tre insieme esaurivano la memoria virtuale del PC)."""
    base = vram_mb()
    voice = engine.EngineProcess(engine.voice_spec(8797, engine.load_voice("jarvis"), "qwen3-1.7b"))
    ears = engine.EngineProcess(engine.ears_spec(8796, device, 6 if device == "cpu" else 4))
    report: dict = {"orecchie": device, "vram_altre_app_mb": base}
    try:
        await voice.start(timeout=120)
        tts = TtsClient(voice.host, voice.port)
        await tts.synthesize("tts", PHRASES[0], Voice(preset="jarvis", language="Italian"), seed=7)  # riscaldamento
        report["vram_voce_mb"] = vram_mb() - base
        await ears.start(timeout=120)
        audio = clips()
        await stt_ms(ears, audio[5])
        report["vram_voce_e_orecchie_mb"] = vram_mb() - base
        report["trascrizione_ms"] = {
            f"{s}s": round(statistics.median([await stt_ms(ears, audio[s]) for _ in range(3)])) for s in LENGTHS}
        report["voce_da_sola_s"] = await tts_times(tts)
        stop, lat = asyncio.Event(), []
        job = asyncio.ensure_future(partials(ears, audio[5], stop, lat))
        report["voce_con_parziali_s"] = await tts_times(tts)
        stop.set()
        await job
        report["parziali_durante_la_voce_ms"] = round(statistics.median(lat)) if lat else None
        report["vram_picco_mb"] = vram_mb() - base
    finally:
        for e in (ears, voice):
            await e.stop()
    return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "cuda")), ensure_ascii=False))

"""Quanta VRAM usano i motori vocali di Jarvis, e quanto costano su CPU invece che su GPU.

Uso: python scripts/bench/vram_bench.py
Serve a decidere come far convivere orecchie e voce con un cervello locale (Bonsai 2, ~7 GB) su 8 GB.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech import engine
from jarvis.speech.stt import transcribe_file
from jarvis.speech.tts import TtsClient, Voice

PHRASE = "Certamente, signore. Ho preparato il rapporto che mi aveva chiesto questa mattina."


def vram_mb() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip().splitlines()[0])


async def measure(spec: engine.EngineSpec, work) -> dict:
    before = vram_mb()
    eng = engine.EngineProcess(spec)
    await eng.start()
    try:
        t = time.perf_counter()
        await work(eng)  # primo uso: caricamento
        first = time.perf_counter() - t
        times = []
        for _ in range(3):
            t = time.perf_counter()
            await work(eng)
            times.append(time.perf_counter() - t)
        return {"motore": spec.name, "backend": spec.extra["backend"], "vram_mb": vram_mb() - before,
                "primo_s": round(first, 2), "medio_s": round(sum(times) / len(times), 3)}
    finally:
        await eng.stop()


async def stt_work(eng, audio):
    await transcribe_file(eng.host, eng.port, "stt", audio)


async def tts_work(eng):
    await TtsClient(eng.host, eng.port).synthesize("tts", PHRASE, Voice(preset="jarvis", language="Italian"), seed=7)


async def main() -> list[dict]:
    voice = engine.load_voice("jarvis")
    rng = np.random.default_rng(0)
    # 4 s di parlato sintetico non servono: conta il tempo di calcolo, non il testo riconosciuto
    audio = (0.05 * rng.standard_normal(16000 * 4)).astype(np.float32)
    out = []
    for backend in ("cuda", "cpu"):
        s = engine.ears_spec(8795)
        s.extra.update(backend=backend, threads=8)
        out.append(await measure(s, lambda e: stt_work(e, audio)))
    for backend in ("cuda", "cpu"):
        s = engine.voice_spec(8796, voice, "qwen3-1.7b")
        s.extra.update(backend=backend, threads=8)
        out.append(await measure(s, tts_work))
    return out


if __name__ == "__main__":
    for row in asyncio.run(main()):
        print(json.dumps(row, ensure_ascii=False), flush=True)

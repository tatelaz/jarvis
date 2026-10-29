"""Prova del riposo sui motori veri: quanta scheda video si libera e quanto ci mette a riaccendersi.

Uso: python scripts/rest_live.py      (Jarvis dev'essere spento; usa il cervello cloud, non Claude)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import log, single
from jarvis.app import Jarvis
from jarvis.audio.devices import SimulatedRoom

CLIP = Path(__file__).resolve().parents[1] / "models" / "testdata" / "fleurs_it" / "it_000.wav"


def vram_mb() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip().splitlines()[0])


async def main() -> dict:
    if not single.acquire(single.APP):
        raise RuntimeError("Jarvis è acceso: spegnilo prima della prova")
    room = SimulatedRoom(echo_gain=0.0, speed=1.0)
    app = Jarvis(backend=room)
    app.cfg.brain.provider = "ollama-cloud"  # solo per questa prova
    import soundfile as sf

    clip, _ = sf.read(CLIP, dtype="float32")

    async def hear(tag: str) -> None:  # l'udito c'è davvero, e quanto ci mette
        t = time.perf_counter()
        text = await app.stt(clip)
        out[f"udito_{tag}"] = f"{app.ears_device} · {round(1000 * (time.perf_counter() - t))} ms · {text[:40]!r}"

    out: dict = {"vram_prima_mb": vram_mb()}
    await app.start()
    try:
        out["vram_acceso_mb"] = vram_mb()
        await hear("acceso")
        t = time.perf_counter()
        await app.rest()
        out["riposo_s"] = round(time.perf_counter() - t, 1)
        await asyncio.sleep(2)
        out["vram_a_riposo_mb"] = vram_mb()
        out["stato_a_riposo"] = app.conv.state
        await hear("a_riposo")
        t = time.perf_counter()
        await app.wake_up(None)
        out["risveglio_s"] = round(time.perf_counter() - t, 1)
        out["vram_risvegliato_mb"] = vram_mb()
        await hear("risvegliato")
        t = time.perf_counter()
        audio, sr, _ = await app._voice.say("Di nuovo operativo, signore: tutti i sistemi sono in linea.")
        out["prima_frase_dopo_risveglio_s"] = round(time.perf_counter() - t, 2)
        out["audio_s"] = round(audio.size / sr, 2)
    finally:
        await app.stop()
    out["liberati_mb"] = out["vram_acceso_mb"] - out["vram_a_riposo_mb"]
    return out


if __name__ == "__main__":
    log.setup()
    print(json.dumps(asyncio.run(main()), ensure_ascii=False, indent=1))

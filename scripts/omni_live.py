"""Prova del modello dei suoni sui motori veri: guarda_video con suoni=true, prestito della scheda video, risposta,
voce e udito che tornano. Non avvia Jarvis: solo i suoi pezzi (orecchie, voce, e il collegamento di app.py).

Uso: python scripts/omni_live.py [clip]      (Jarvis dev'essere spento; media.omni impostato e il modello scaricato)
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import health, paths, single
from jarvis.app import EARS_PORT, VOICE_PORT, Jarvis
from jarvis.config import ConfigStore
from jarvis.core.bus import Bus
from jarvis.speech import engine
from jarvis.speech.tts import TtsClient
from jarvis.speech.voice import VoiceOutput

CLIP = paths.DATA_DIR / "prove" / "omni_clips" / "video_cucina.webm"


def vram_mb() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip().splitlines()[0])


class Peak:
    """La memoria video più alta vista mentre lavora (nvidia-smi ogni mezzo secondo, in un thread)."""

    def __init__(self) -> None:
        self.value, self._stop = 0, threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self.value = max(self.value, vram_mb())

    def __enter__(self) -> Peak:
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join(2)


async def main(clip: Path) -> dict:
    if not single.acquire(single.APP):
        raise RuntimeError("Jarvis è acceso: spegnilo prima della prova")
    cfg = ConfigStore().config
    j = Jarvis.__new__(Jarvis)  # solo i pezzi che servono a guarda_video, niente conversazione né cervello
    j.cfg, j.bus, j.conv, j.resting, j._rest_lock = cfg, Bus(), None, False, asyncio.Lock()
    j.identity, j.whatsapp = None, None
    out: dict = {"modello": cfg.media.omni, "clip": clip.name, "vram_prima_mb": vram_mb()}
    meta = engine.load_voice(cfg.speech.tts_voice)
    j.ears_device = health.ears_device_for(cfg.speech.ears_device, health.free_vram_mb())
    j.ears = j._ears_process(j.ears_device, EARS_PORT)
    j.voice_engine = engine.EngineProcess(engine.voice_spec(VOICE_PORT, meta, cfg.speech.tts_model),
                                          on_event=j._engine_event)
    try:
        await asyncio.gather(j.ears.start(), j.voice_engine.start())
        j.ears.supervise()
        j.voice_engine.supervise()
        j._voice = VoiceOutput(TtsClient(j.voice_engine.host, j.voice_engine.port), meta, speed=lambda: 1.0)
        await j._warm_engines(j._voice)
        out["vram_jarvis_pronto_mb"] = vram_mb()
        out["udito_prima"] = j.ears_device
        media = j._build_media()
        t = time.perf_counter()
        with Peak() as peak:
            res = await media.look({"sorgente": str(clip), "suoni": True})
        out["guarda_video_s"] = round(time.perf_counter() - t, 1)
        out["vram_picco_mb"] = peak.value
        out["vram_dopo_mb"] = vram_mb()
        out["immagini"] = len(res.images)
        text = res.text
        out["suoni"] = text.split("Musica e suoni", 1)[1][:600] if "Musica e suoni" in text else None
        out["note"] = [line for line in text.splitlines() if line.startswith("Nota:")]
        out["parlato"] = text.split("Parlato", 1)[1][:300] if "Parlato" in text else None
        out["udito_dopo"] = j.ears_device
        out["voce_viva"] = await j.voice_engine.health()
        out["udito_vivo"] = await j.ears.health()
        t = time.perf_counter()
        audio, sr, _ = await j._voice.say("Ho ascoltato il video, signore: ecco cosa ho sentito.")
        out["frase_dopo_s"] = round(time.perf_counter() - t, 2)
        out["frase_dopo_audio_s"] = round(audio.size / sr, 2)
        t = time.perf_counter()
        heard = await j.stt(audio if sr == 16000 else __import__("soxr").resample(audio, sr, 16000))
        out["udito_dopo_ms"] = round(1000 * (time.perf_counter() - t))
        out["udito_dopo_testo"] = heard
    finally:
        for e in (j.ears, j.voice_engine):
            await e.stop()
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s: %(message)s")
    for name in ("jarvis.media", "jarvis.media.omni", "jarvis.engine"):
        logging.getLogger(name).setLevel(logging.INFO)
    print(json.dumps(asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else CLIP)), ensure_ascii=False,
                     indent=1))

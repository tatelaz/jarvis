"""Prova di microfono e casse al primo avvio, senza accendere i motori della voce.

Il microfono: un flusso d'ingresso breve (si chiude da solo) che misura il livello, come la barra della pagina; quando
la voce supera la soglia per un attimo vuol dire "ti sento". Le casse: una frase già registrata con la voce di base di
Jarvis (`prova.wav` accanto a ogni voce inclusa), suonata sul dispositivo scelto. I dispositivi si trovano come li
trova Jarvis (WASAPI, `jarvis.audio.devices.find_device`), così quello che funziona qui funziona anche dopo.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.setup")

RATE = 48000
BLOCK = 480  # 10 ms
FLOOR_DB, TOP_DB = -60.0, -12.0  # la barra va da qui a qui
HEARD_LEVEL = 0.45  # sopra questa altezza della barra è voce, non rumore di fondo
HEARD_BLOCKS = 25  # ...per almeno 250 ms in tutto
MIC_SECONDS = 30.0  # il microfono si chiude da solo: non resta aperto se la pagina se ne dimentica
DECAY = 0.9  # la barra scende piano (un picco si vede anche se la pagina chiede ogni 100 ms)


def level_of(block: np.ndarray) -> float:
    """Altezza della barra (0-1) per un blocco di audio."""
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if block.size else 0.0
    db = 20 * np.log10(rms + 1e-9)
    return float(min(1.0, max(0.0, (db - FLOOR_DB) / (TOP_DB - FLOOR_DB))))


class AudioCheck:
    def __init__(self, sd=None, find: Callable[[str, str | None], int] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if sd is None:
            import sounddevice as sd

            from ..audio.devices import _fix_sounddevice
            _fix_sounddevice(sd)
        if find is None:
            from ..audio.devices import find_device as find
        self.sd, self.find, self.clock = sd, find, clock
        self._lock = threading.Lock()
        self._stream = None
        self._level, self._heard_blocks, self._until = 0.0, 0, 0.0
        self.error = ""

    def _wasapi(self) -> int | None:
        for i, api in enumerate(self.sd.query_hostapis()):
            if "WASAPI" in api["name"]:
                return i
        return None

    def devices(self) -> dict:
        """Microfoni e uscite come li vede Windows, più quali sono i predefiniti."""
        api = self._wasapi()
        devs = self.sd.query_devices()
        out: dict = {"inputs": [], "outputs": [], "default_input": None, "default_output": None}
        if api is None:
            return out
        host = self.sd.query_hostapis(api)
        for i in host["devices"]:
            d = devs[i]
            if d["max_input_channels"] > 0:
                out["inputs"].append(d["name"])
            if d["max_output_channels"] > 0:
                out["outputs"].append(d["name"])
        for key, kind in (("default_input", "default_input_device"), ("default_output", "default_output_device")):
            idx = host.get(kind, -1)
            if idx is not None and idx >= 0:
                out[key] = devs[idx]["name"]
        return out

    def _extra(self, index: int):
        d = self.sd.query_devices(index)
        return self.sd.WasapiSettings(auto_convert=True) if d["hostapi"] == self._wasapi() else None

    # --- microfono ---

    def start_mic(self, name: str | None) -> None:
        self.stop_mic()
        self.error = ""
        try:
            idx = self.find("input", name)
            stream = self.sd.InputStream(device=idx, channels=1, samplerate=RATE, blocksize=BLOCK, dtype="float32",
                                         callback=self._on_block, extra_settings=self._extra(idx))
            stream.start()
        except Exception as e:  # microfono occupato, staccato, negato da Windows: la pagina lo dice
            log.warning("primo avvio: microfono non aperto: %s", e)
            self.error = str(e) or type(e).__name__
            return
        with self._lock:
            self._stream = stream
            self._level, self._heard_blocks = 0.0, 0
            self._until = self.clock() + MIC_SECONDS

    def _on_block(self, indata, frames, _time, status) -> None:
        lv = level_of(np.asarray(indata).reshape(-1))
        with self._lock:
            self._level = max(lv, self._level * DECAY)
            if lv >= HEARD_LEVEL:
                self._heard_blocks += 1

    def mic(self) -> dict:
        if self._stream is not None and self.clock() > self._until:
            self.stop_mic()
        with self._lock:
            return {"running": self._stream is not None, "level": round(self._level, 3),
                    "heard": self._heard_blocks >= HEARD_BLOCKS, "error": self.error}

    def stop_mic(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # già chiuso dal driver
                log.debug("primo avvio: chiusura del microfono", exc_info=True)

    # --- casse ---

    def play(self, path: Path, name: str | None) -> float:
        """Suona il file sul dispositivo scelto (senza aspettare la fine). Restituisce la durata in secondi."""
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32")
        idx = self.find("output", name)
        self.sd.play(data, sr, device=idx, extra_settings=self._extra(idx))
        return len(data) / sr

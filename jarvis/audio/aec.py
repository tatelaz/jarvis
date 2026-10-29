"""Cancellazione d'eco (WebRTC AEC3) e pulizia del microfono, a 16 kHz mono.

Il riferimento ("far end") sono esattamente i campioni che mandiamo alle casse: il modulo li
confronta con ciò che rientra dal microfono e sottrae la voce di Jarvis, lasciando la tua.
Ingresso e riferimento vanno dati a blocchi di 10 ms (160 campioni); questa classe accetta
blocchi di qualunque lunghezza e tiene il resto per la volta successiva.
"""

from __future__ import annotations

import numpy as np
from livekit import rtc

SR = 16000
FRAME = SR // 100  # 10 ms


def _to_i16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


class EchoCanceller:
    def __init__(self, echo_cancellation: bool = True, noise_suppression: bool = True,
                 high_pass: bool = True, auto_gain: bool = False, delay_ms: int = 0) -> None:
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=echo_cancellation, noise_suppression=noise_suppression,
            high_pass_filter=high_pass, auto_gain_control=auto_gain,
        )
        self._near = np.zeros(0, np.float32)
        self._far = np.zeros(0, np.float32)
        self._frame = rtc.AudioFrame.create(SR, 1, FRAME)
        if delay_ms:
            self._apm.set_stream_delay_ms(int(delay_ms))

    def set_delay(self, ms: int) -> None:
        """Ritardo stimato tra uscita alle casse e rientro nel microfono (AEC3 lo affina da solo)."""
        self._apm.set_stream_delay_ms(int(max(0, ms)))

    def _run(self, x: np.ndarray, reverse: bool) -> np.ndarray:
        buf = np.frombuffer(self._frame.data, dtype=np.int16)
        buf[:] = _to_i16(x)
        if reverse:
            self._apm.process_reverse_stream(self._frame)
            return x
        self._apm.process_stream(self._frame)
        return np.frombuffer(self._frame.data, dtype=np.int16).astype(np.float32) / 32768.0

    def push_far(self, audio: np.ndarray) -> None:
        """Audio appena inviato alle casse (16 kHz). Va chiamato prima del corrispondente process()."""
        self._far = np.concatenate([self._far, np.asarray(audio, np.float32)])
        while self._far.size >= FRAME:
            self._run(self._far[:FRAME], reverse=True)
            self._far = self._far[FRAME:]

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Audio del microfono (16 kHz) → audio ripulito. Restituisce solo i blocchi completi."""
        self._near = np.concatenate([self._near, np.asarray(audio, np.float32)])
        n = self._near.size // FRAME
        if n == 0:
            return np.zeros(0, np.float32)
        out = np.empty(n * FRAME, np.float32)
        for i in range(n):
            out[i * FRAME:(i + 1) * FRAME] = self._run(self._near[i * FRAME:(i + 1) * FRAME], reverse=False)
        self._near = self._near[n * FRAME:]
        return out

    def close(self) -> None:
        """Rilascia il modulo nativo in modo esplicito (evita avvisi alla chiusura dell'interprete)."""
        apm = getattr(self, "_apm", None)
        if apm is not None:
            handle = getattr(apm, "_ffi_handle", None)
            if handle is not None and hasattr(handle, "dispose"):
                handle.dispose()
            self._apm = None

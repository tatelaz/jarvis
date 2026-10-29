"""Rilevamento della voce con Silero VAD (ONNX, CPU).

Il modello lavora su finestre di 512 campioni a 16 kHz (32 ms) precedute da 64 campioni di
contesto, e mantiene uno stato ricorrente tra una finestra e l'altra. Questa classe accetta
blocchi di qualunque lunghezza e restituisce una probabilità per ogni finestra completa.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

from .. import paths

WINDOW = 512
CONTEXT = 64
SAMPLE_RATE = 16000


def _session(path: Path) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.inter_op_num_threads = 1
    so.intra_op_num_threads = 1
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


class SileroVad:
    def __init__(self, model_path: Path | None = None) -> None:
        self._sess = _session(model_path or paths.SILERO_VAD)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def window_prob(self, window: np.ndarray) -> float:
        """Probabilità di voce per esattamente 512 campioni float32 in [-1, 1]."""
        if window.shape != (WINDOW,):
            raise ValueError(f"servono {WINDOW} campioni, ricevuti {window.shape}")
        x = np.concatenate([self._context, window[None, :].astype(np.float32)], axis=1)
        out, self._state = self._sess.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -CONTEXT:]
        return float(out[0, 0])

    def feed(self, audio: np.ndarray) -> list[float]:
        """Accoda audio e restituisce le probabilità delle finestre complete ora disponibili."""
        self._pending = np.concatenate([self._pending, audio.astype(np.float32, copy=False)])
        probs = []
        while self._pending.size >= WINDOW:
            probs.append(self.window_prob(self._pending[:WINDOW]))
            self._pending = self._pending[WINDOW:]
        return probs

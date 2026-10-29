"""Smart Turn v3.2: capisce dall'audio se chi parla ha finito il turno o sta solo facendo una pausa.

Preprocessing identico al riferimento di pipecat-ai/smart-turn: ultimi 8 s di audio (zeri a
sinistra se più corto), normalizzazione a media zero e varianza unitaria, log-mel Whisper.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

from .. import paths
from .features import SAMPLE_RATE, log_mel, normalize

SECONDS = 8
MAX_SAMPLES = SECONDS * SAMPLE_RATE


def prepare(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float32)
    if x.size > MAX_SAMPLES:
        x = x[-MAX_SAMPLES:]
    elif x.size < MAX_SAMPLES:
        x = np.pad(x, (MAX_SAMPLES - x.size, 0))
    return log_mel(normalize(x))[None, :, :]


class SmartTurn:
    def __init__(self, model_path: Path | None = None) -> None:
        so = ort.SessionOptions()
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 2
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.log_severity_level = 3
        self._sess = ort.InferenceSession(
            str(model_path or paths.SMART_TURN), sess_options=so, providers=["CPUExecutionProvider"]
        )

    def probability(self, audio: np.ndarray) -> float:
        """Probabilità (0..1) che il turno sia concluso, dato l'audio del turno fino a ora."""
        (logits,) = self._sess.run(None, {"input_features": prepare(audio)})
        return float(logits[0, 0])

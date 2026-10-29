"""Impronta vocale: ReDimNet2+ (lab260, 2026, Apache 2.0) convertito in ONNX, sul processore.

Scelto il 7/10 con una gara su voci italiane (MLS, 33 lettori) nelle condizioni di Jarvis
(scripts/explore/speaker_arena.py): migliore sulle frasi da 1 s (EER 3,3% contro 4,3-5,4%) e soprattutto con un
microfono scadente (EER 3,3% contro 12% di CAM++, che respingeva la persona giusta 4 volte su 10).
Prende l'audio grezzo a 16 kHz (lo spettrogramma è dentro il modello: TFMelBanks a 72 bande, non le 64 della
scheda) e restituisce 192 numeri già normalizzati. ~50 ms per secondo di voce con 6 thread.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from .. import paths

log = logging.getLogger("jarvis.identity")
MODEL = paths.MODELS_DIR / "speaker" / "redimnet2-plus" / "redimnet2plus.onnx"
SR = 16000
MIN_S = 0.6  # sotto questa durata l'impronta non è affidabile: "non so"
MAX_S = 4.0  # oltre, la precisione non migliora (misurato) e il tempo sì: si prende il centro della frase


class SpeakerModel:
    def __init__(self, path: Path = MODEL, threads: int = 4) -> None:
        self.path = path
        self.threads = threads
        self._session = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.path.exists()

    def _load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort

            t = time.perf_counter()
            so = ort.SessionOptions()
            so.intra_op_num_threads = self.threads
            so.log_severity_level = 3
            self._session = ort.InferenceSession(str(self.path), so, providers=["CPUExecutionProvider"])
            log.info("modello dell'impronta vocale caricato in %.1f s", time.perf_counter() - t)

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        """Impronta di una frase (16 kHz mono). None se troppo corta."""
        a = np.asarray(audio, np.float32).reshape(-1)
        if a.size < MIN_S * SR:
            return None
        if a.size > MAX_S * SR:
            start = (a.size - int(MAX_S * SR)) // 2
            a = a[start:start + int(MAX_S * SR)]
        self._load()
        e = self._session.run(None, {"wav": a[None, :]})[0][0]
        return (e / max(float(np.linalg.norm(e)), 1e-9)).astype(np.float32)

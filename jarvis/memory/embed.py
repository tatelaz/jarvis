"""Il significato delle frasi come vettori: EmbeddingGemma 2 (Google, 6/10/2026, Apache 2.0), solo testo, sul processore.

Scelto e misurato il 7/10 su frasi italiane del tipo che Jarvis deve ritrovare ("chi è la mia ragazza?" ->
"Giulia è la fidanzata di Andrea"): 14 giuste su 15 al primo posto. Variante q4: 26 ms a frase con 6 thread,
contro 209 ms della int8 a parità di qualità (la int8 quantizza al volo a ogni moltiplicazione). Niente VRAM.

Il modello vuole un'istruzione davanti al testo: "task: search result | query: ..." per le domande e
"title: none | text: ..." per i ricordi (scheda del modello). Il vettore esce già normalizzato (768 numeri).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from .. import paths

log = logging.getLogger("jarvis.memory.embed")
MODEL_DIR = paths.ONNX_DIR / "embeddinggemma-2"
QUERY = "task: search result | query: "
DOCUMENT = "title: none | text: "


class Embedder:
    def __init__(self, model_dir: Path = MODEL_DIR, threads: int = 4, max_tokens: int = 512) -> None:
        self.model_dir = model_dir
        self.threads = threads
        self.max_tokens = max_tokens  # i testi lunghi si tagliano: tempo e memoria restano prevedibili
        self._session = None
        self._tok = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return (self.model_dir / "onnx" / "model_q4.onnx").exists() and (self.model_dir / "tokenizer.json").exists()

    def _load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            t = time.perf_counter()
            so = ort.SessionOptions()
            so.intra_op_num_threads = self.threads
            so.log_severity_level = 3  # niente avvisi di onnxruntime nei registri
            tok = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
            tok.enable_truncation(self.max_tokens)  # i token speciali (<bos>, <eos>) restano
            self._session = ort.InferenceSession(str(self.model_dir / "onnx" / "model_q4.onnx"), so,
                                                 providers=["CPUExecutionProvider"])
            self._tok = tok
            log.info("modello del significato caricato in %.1f s", time.perf_counter() - t)

    def _embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 768), np.float32)
        self._load()
        encs = self._tok.encode_batch(texts)
        n = max(len(e.ids) for e in encs)
        ids = np.zeros((len(encs), n), np.int64)
        mask = np.zeros_like(ids)
        for i, e in enumerate(encs):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = 1
        empty = np.zeros((0, 512), np.float32)  # solo testo: niente immagini, video, audio
        out = self._session.run(["sentence_embedding"], {
            "input_ids": ids, "attention_mask": mask, "image_features": empty, "video_features": empty,
            "audio_features": empty})[0]
        return (out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)).astype(np.float32)

    def queries(self, texts: list[str]) -> np.ndarray:
        return self._embed([QUERY + t for t in texts])

    def documents(self, texts: list[str], batch: int = 16) -> np.ndarray:
        parts = [self._embed([DOCUMENT + t for t in texts[i:i + batch]]) for i in range(0, len(texts), batch)]
        return np.concatenate(parts) if parts else np.zeros((0, 768), np.float32)

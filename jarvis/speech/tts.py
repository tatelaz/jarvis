"""Sintesi vocale tramite il motore audio.cpp."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .http import HttpError, request, sse_events

log = logging.getLogger("jarvis.tts")


@dataclass
class Voice:
    """Come ottenere la voce: clonazione da riferimento, preset del modello o descrizione."""

    ref_wav: Path | None = None
    ref_text: str | None = None
    preset: str | None = None  # "voice" nativo del modello o preset configurato sul server
    instruct: str | None = None  # descrizione per il voice design
    language: str | None = None

    def fields(self) -> dict:
        out: dict = {}
        if self.ref_wav is not None:
            out["voice_ref"] = {"type": "path", "path": str(self.ref_wav)}
            if self.ref_text:
                out["reference_text"] = self.ref_text
        if self.preset:
            out["voice"] = self.preset
        if self.instruct:
            out["instructions"] = self.instruct  # il server lo passa al modello come "instruction"
        if self.language:
            out["language"] = self.language
        return out


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        i = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        i = np.where(i & 0x800000, i - 0x1000000, i)
        x = i.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"WAV a {8 * width} bit non supportato")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


class TtsClient:
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    async def synthesize(self, model: str, text: str, voice: Voice | None = None,
                         options: dict | None = None, seed: int | None = None,
                         speed: float | None = None, timeout: float = 60.0) -> tuple[np.ndarray, int]:
        """Sintetizza un testo intero e restituisce (audio float32 mono, frequenza)."""
        payload: dict = {"model": model, "input": text, "response_format": "wav"}
        payload.update((voice or Voice()).fields())
        if options:
            payload["options"] = options
        if seed is not None:
            payload["seed"] = seed
        if speed is not None:
            payload["speed"] = speed
        body = json.dumps(payload).encode()
        resp, reader, writer = await request(self.host, self.port, "POST", "/v1/audio/speech", body,
                                             timeout=timeout)
        try:
            data = await asyncio.wait_for(reader.read_all(), timeout=timeout)
        finally:
            writer.close()
        if resp.status >= 400:
            raise HttpError(resp.status, data.decode("utf-8", "replace"))
        return decode_wav(data)

    async def stream(self, model: str, text: str, voice: Voice | None = None,
                     options: dict | None = None, seed: int | None = None,
                     sample_rate: int = 24000) -> AsyncIterator[np.ndarray]:
        """Sintesi in streaming (modelli configurati in modalità streaming): blocchi PCM float32."""
        payload: dict = {"model": model, "input": text, "response_format": "pcm", "stream_format": "sse"}
        payload.update((voice or Voice()).fields())
        if options:
            payload["options"] = options
        if seed is not None:
            payload["seed"] = seed
        resp, reader, writer = await request(self.host, self.port, "POST", "/v1/audio/speech",
                                             json.dumps(payload).encode(), accept="text/event-stream")
        try:
            if resp.status >= 400:
                raise HttpError(resp.status, (await reader.read_all()).decode("utf-8", "replace"))
            async for ev in sse_events(reader):
                kind = ev.get("type", "")
                if kind == "speech.audio.delta":
                    pcm = base64.b64decode(ev["audio"])
                    yield np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                elif kind == "error" or "error" in ev:
                    raise RuntimeError(f"errore del motore TTS: {ev}")
        finally:
            writer.close()


async def timed(coro) -> tuple[object, float]:
    t = time.perf_counter()
    r = await coro
    return r, time.perf_counter() - t

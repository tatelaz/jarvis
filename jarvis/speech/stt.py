"""Trascrizione vocale tramite il motore audio.cpp.

`transcribe_file` è quella che usa Jarvis: l'intera frase in una richiesta (parakeet-ultra, sulla scheda
video: 30-75 ms), anche ogni 450 ms durante il parlato per il testo in diretta.

`LiveTranscription` apre una connessione all'endpoint live: l'audio parte mentre l'utente parla e i pezzi
di testo tornano subito (cache-aware streaming di Nemotron). Resta solo per i banchi di prova
(`scripts/bench/asr_bench.py`): misurato e scartato, più errori senza vantaggi di velocità.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from collections.abc import Callable

import numpy as np

from .http import DuplexRequest, HttpError, request, sse_events

log = logging.getLogger("jarvis.stt")


def to_pcm16(audio: np.ndarray) -> bytes:
    x = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


def to_wav(audio: np.ndarray, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(to_pcm16(audio))
    return buf.getvalue()


class LiveTranscription:
    """Una sessione di trascrizione live per un singolo turno di parola."""

    def __init__(self, host: str, port: int, model: str, language: str | None = None,
                 on_partial: Callable[[str], None] | None = None, sample_rate: int = 16000) -> None:
        query: dict[str, str | int] = {"model": model, "sample_rate": sample_rate,
                                       "channels": 1, "sample_format": "s16le"}
        if language:
            query["language"] = language
        self._req = DuplexRequest(host, port, "/v1/audio/transcriptions/live", query)
        self._on_partial = on_partial
        self._text = ""
        self._final: str | None = None
        self._error: Exception | None = None
        self._reader_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self.opened_at = 0.0
        self.first_delta_at: float | None = None
        self.ended_at: float | None = None
        self.final_at: float | None = None

    @property
    def text(self) -> str:
        """Il testo riconosciuto finora (parziale finché il turno non è chiuso)."""
        return self._final if self._final is not None else self._text

    async def start(self) -> None:
        self.opened_at = time.perf_counter()
        await self._req.open()
        self._reader_task = asyncio.create_task(self._read(), name="stt-live-reader")

    async def _read(self) -> None:
        try:
            resp, body = await self._req.response()
            if resp.status >= 400:
                raise HttpError(resp.status, (await body.read_all()).decode("utf-8", "replace"))
            async for ev in sse_events(body):
                kind = ev.get("type", "")
                if kind == "transcript.text.delta":
                    if self.first_delta_at is None:
                        self.first_delta_at = time.perf_counter()
                    self._text += ev.get("delta", "")
                    if self._on_partial:
                        self._on_partial(self._text)
                elif kind == "transcript.text.done":
                    self._final = (ev.get("text") or self._text).strip()
                    self.final_at = time.perf_counter()
                elif kind == "error" or "error" in ev:
                    raise RuntimeError(f"errore del motore STT: {ev}")
        except Exception as e:  # l'errore viene rilanciato a chi aspetta il risultato
            self._error = e

    async def send(self, audio: np.ndarray) -> None:
        if self._error:
            raise self._error
        async with self._send_lock:
            await self._req.send(to_pcm16(audio))

    async def finish(self, timeout: float = 10.0) -> str:
        """Chiude il turno e restituisce il testo definitivo."""
        async with self._send_lock:
            await self._req.end_body()
        self.ended_at = time.perf_counter()
        try:
            assert self._reader_task is not None
            await asyncio.wait_for(self._reader_task, timeout=timeout)
        finally:
            self._req.close()
        if self._error:
            raise self._error
        if self._final is None:
            raise RuntimeError("il motore STT ha chiuso il flusso senza un testo definitivo")
        return self._final

    async def abort(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._req.close()


async def transcribe_file(host: str, port: int, model: str, audio: np.ndarray, sr: int = 16000,
                          language: str | None = None, timeout: float = 60.0, fields: dict[str, str] | None = None
                          ) -> dict:
    """Trascrizione offline di un intero segmento (upload multipart). fields = opzioni della richiesta in più (es.
    "prompt" con la domanda per i modelli che rispondono sull'audio, come Audio Flamingo)."""
    boundary = "jarvisboundary7f3a"
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode()]
    if language:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{language}\r\n".encode())
    for k, v in (fields or {}).items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
        "Content-Type: audio/wav\r\n\r\n".encode() + to_wav(audio, sr) + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    resp, reader, writer = await request(host, port, "POST", "/v1/audio/transcriptions", body,
                                         content_type=f"multipart/form-data; boundary={boundary}",
                                         timeout=timeout)
    try:
        data = await asyncio.wait_for(reader.read_all(), timeout=timeout)
    finally:
        writer.close()
    if resp.status >= 400:
        raise HttpError(resp.status, data.decode("utf-8", "replace"))
    import json

    return json.loads(data)

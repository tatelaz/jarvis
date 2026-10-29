"""Client HTTP/1.1 minimale e full-duplex per il motore audio.cpp.

Serve perché l'endpoint di trascrizione live vuole il corpo della richiesta in chunked encoding
*mentre* risponde con eventi SSE sulla stessa connessione: le librerie HTTP comuni prima inviano
tutto il corpo e solo dopo leggono la risposta, il che vanificherebbe lo streaming.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import urlencode


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)


class _BodyReader:
    """Legge il corpo della risposta gestendo chunked encoding e Content-Length."""

    def __init__(self, reader: asyncio.StreamReader, headers: dict[str, str]) -> None:
        self._r = reader
        self._chunked = "chunked" in headers.get("transfer-encoding", "").lower()
        cl = headers.get("content-length")
        self._remaining = int(cl) if cl is not None and not self._chunked else None
        self._done = False

    async def read(self) -> bytes:
        """Restituisce il prossimo pezzo di corpo, b"" a fine corpo."""
        if self._done:
            return b""
        if self._chunked:
            size_line = await self._r.readline()
            if not size_line:
                self._done = True
                return b""
            size = int(size_line.split(b";")[0].strip() or b"0", 16)
            if size == 0:
                await self._r.readline()  # CRLF finale (ignoriamo eventuali trailer)
                self._done = True
                return b""
            data = await self._r.readexactly(size)
            await self._r.readexactly(2)
            return data
        if self._remaining is not None:
            if self._remaining <= 0:
                self._done = True
                return b""
            data = await self._r.read(min(65536, self._remaining))
            if not data:
                self._done = True
            self._remaining -= len(data)
            return data
        data = await self._r.read(65536)
        if not data:
            self._done = True
        return data

    async def read_all(self) -> bytes:
        parts = []
        while chunk := await self.read():
            parts.append(chunk)
        return b"".join(parts)


async def _read_head(reader: asyncio.StreamReader) -> Response:
    status_line = await reader.readline()
    if not status_line:
        raise ConnectionError("il motore vocale ha chiuso la connessione senza rispondere")
    parts = status_line.decode("latin-1").split(" ", 2)
    resp = Response(status=int(parts[1]))
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        k, _, v = line.decode("latin-1").partition(":")
        resp.headers[k.strip().lower()] = v.strip()
    return resp


async def sse_events(body: _BodyReader) -> AsyncIterator[dict]:
    """Interpreta un corpo Server-Sent Events e produce gli oggetti JSON dei campi `data:`."""
    buf = b""
    while True:
        chunk = await body.read()
        if not chunk:
            break
        # normalizza i fine riga sull'intero buffer: un "\r" rimasto a fine pezzo si ricompone
        # col "\n" del pezzo successivo
        buf = (buf + chunk).replace(b"\r\n", b"\n")
        while b"\n\n" in buf:
            event, buf = buf.split(b"\n\n", 1)
            data_lines = [ln[5:].strip() for ln in event.splitlines() if ln.startswith(b"data:")]
            if not data_lines:
                continue
            payload = b"\n".join(data_lines)
            if payload == b"[DONE]":
                return
            yield json.loads(payload)


class DuplexRequest:
    """Una richiesta POST con corpo chunked inviato a pezzi e risposta letta in parallelo."""

    def __init__(self, host: str, port: int, path: str, query: dict[str, str | int] | None = None,
                 content_type: str = "application/octet-stream") -> None:
        self.host, self.port = host, port
        self.target = path + ("?" + urlencode(query) if query else "")
        self.content_type = content_type
        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self._closed_body = False

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        head = (
            f"POST {self.target} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Content-Type: {self.content_type}\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n\r\n"
        )
        self._writer.write(head.encode("latin-1"))
        await self._writer.drain()

    async def send(self, data: bytes) -> None:
        if not data or self._closed_body:
            return
        assert self._writer is not None
        self._writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        await self._writer.drain()

    async def end_body(self) -> None:
        if self._closed_body or self._writer is None:
            return
        self._closed_body = True
        self._writer.write(b"0\r\n\r\n")
        await self._writer.drain()

    async def response(self) -> tuple[Response, _BodyReader]:
        assert self._reader is not None
        resp = await _read_head(self._reader)
        return resp, _BodyReader(self._reader, resp.headers)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None


async def request(host: str, port: int, method: str, path: str, body: bytes | None = None,
                  content_type: str = "application/json", accept: str = "*/*",
                  timeout: float = 120.0) -> tuple[Response, _BodyReader, asyncio.StreamWriter]:
    """Richiesta normale: restituisce risposta, lettore del corpo e writer (da chiudere)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    head = f"{method} {path} HTTP/1.1\r\nHost: {host}:{port}\r\nAccept: {accept}\r\nConnection: close\r\n"
    if body is not None:
        head += f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
    writer.write(head.encode("latin-1") + b"\r\n" + (body or b""))
    await writer.drain()
    resp = await asyncio.wait_for(_read_head(reader), timeout=timeout)
    return resp, _BodyReader(reader, resp.headers), writer


async def request_json(host: str, port: int, method: str, path: str, payload: dict | None = None,
                       timeout: float = 120.0) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    resp, reader, writer = await request(host, port, method, path, body, timeout=timeout)
    try:
        data = await asyncio.wait_for(reader.read_all(), timeout=timeout)
    finally:
        writer.close()
    if resp.status >= 400:
        raise HttpError(resp.status, data.decode("utf-8", "replace"))
    return json.loads(data) if data else {}

"""Cervello locale su qualunque server OpenAI-compatibile (llama.cpp/Bonsai 2, Ollama, LM Studio).

Ciclo agentico nostro: il modello scrive in streaming, se chiede strumenti li eseguiamo e
rimandiamo i risultati, finché non risponde a parole. La cronologia è tenuta qui e potata in
base a un budget di caratteri, perché i modelli locali hanno contesti piccoli (Bonsai: 8K token).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import aiohttp

from .base import BrainEvent, BrainUnavailable, Done, TextDelta, ToolCall, ToolResult
from .tools import ToolRegistry

log = logging.getLogger("jarvis.brain.local")


class LocalBrain:
    def __init__(self, system_prompt: str, tools: ToolRegistry, base_url: str, model: str, api_key: str = "local",
                 kind: str = "llamacpp", history_chars: int = 14000, max_tool_rounds: int = 6,
                 temperature: float = 0.7, name: str = "local", provider: str = "local") -> None:
        self.name = name
        self.provider = provider  # "local" = sul PC; "ollama-cloud" = server di Ollama
        self.system_prompt = system_prompt
        self.tools = tools
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.kind = kind
        self.history_chars = history_chars
        self.max_tool_rounds = max_tool_rounds
        self.temperature = temperature
        self.history: list[dict] = []
        self._session: aiohttp.ClientSession | None = None
        self._resp: aiohttp.ClientResponse | None = None
        self._interrupted = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
        try:
            async with self._session.get(f"{self.base_url}/models", headers=self._headers()) as r:
                if r.status >= 400:
                    raise BrainUnavailable(f"server locale: HTTP {r.status}")
        except aiohttp.ClientError as e:
            raise BrainUnavailable(f"server locale non raggiungibile su {self.base_url}: {e}") from e

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _extra(self) -> dict:
        # il ragionamento esplicito rende i modelli locali lentissimi a voce (Bonsai: migliaia di token)
        if self.kind == "llamacpp":
            return {"chat_template_kwargs": {"enable_thinking": False}}
        if self.kind == "ollama":
            return {"think": False}
        return {}

    def _trimmed_history(self) -> list[dict]:
        """Ultimi messaggi entro il budget, senza spezzare una sequenza strumento/risultato."""
        total, keep = 0, []
        for msg in reversed(self.history):
            size = len(json.dumps(msg, ensure_ascii=False))
            if total + size > self.history_chars and keep:
                break
            keep.append(msg)
            total += size
        keep.reverse()
        while keep and keep[0]["role"] == "tool":
            keep.pop(0)
        return keep

    async def interrupt(self) -> None:
        self._interrupted = True
        if self._resp is not None:
            self._resp.close()

    async def respond(self, message: str) -> AsyncIterator[BrainEvent]:
        async with self._lock:
            if self._session is None:
                await self.start()
            self._interrupted = False
            self.history.append({"role": "user", "content": message})
            full = ""
            for _round in range(self.max_tool_rounds + 1):
                text, calls = "", {}
                try:
                    async for kind, payload in self._stream():
                        if kind == "text":
                            text += payload
                            full += payload
                            yield TextDelta(payload)
                        else:
                            calls = payload
                except (aiohttp.ClientError, TimeoutError) as e:
                    if not self._interrupted:
                        raise BrainUnavailable(f"server locale: {e}") from e
                if self._interrupted:
                    if text:
                        self.history.append({"role": "assistant", "content": text})
                    break
                if not calls:
                    self.history.append({"role": "assistant", "content": text})
                    break
                tool_calls = [{"id": c["id"] or f"call_{i}", "type": "function",
                               "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                              for i, c in sorted(calls.items())]
                self.history.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                        result = f"Errore: argomenti non validi (JSON malformato) per {name}"
                    else:
                        yield ToolCall(name, args)
                        result = await self.tools.call(name, args)
                    yield ToolResult(name, not result.startswith("Errore"), result[:200])
                    self.history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            yield Done(full, interrupted=self._interrupted, model=self.model)

    async def _stream(self):
        """Una chiamata in streaming: produce ("text", pezzo) e alla fine ("tools", {indice: chiamata})."""
        assert self._session is not None
        body = {"model": self.model, "stream": True, "temperature": self.temperature,
                "messages": [{"role": "system", "content": self.system_prompt}, *self._trimmed_history()],
                "tools": self.tools.openai_tools(), **self._extra()}
        calls: dict[int, dict] = {}
        async with self._session.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers()) as r:
            self._resp = r
            if r.status >= 400:
                raise BrainUnavailable(f"server locale: HTTP {r.status}: {(await r.text())[:300]}")
            buf = b""
            async for chunk in r.content.iter_any():
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        break
                    ev = json.loads(data)
                    for choice in ev.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield "text", delta["content"]
                        for tc in delta.get("tool_calls") or []:
                            c = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                            c["id"] = tc.get("id") or c["id"]
                            fn = tc.get("function") or {}
                            c["name"] += fn.get("name") or ""
                            c["args"] += fn.get("arguments") or ""
        self._resp = None
        if calls:
            yield "tools", calls

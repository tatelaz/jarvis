"""Strumenti del cervello, definiti una volta sola e adattati a ogni fornitore.

Ogni strumento ha nome, descrizione (scritta per il modello), schema JSON degli argomenti e una
funzione asincrona. Da qui si generano sia gli strumenti MCP in-process per il Claude Agent SDK,
sia le definizioni "function calling" per i server OpenAI-compatibili (Bonsai, Ollama...).
Gli errori di uno strumento non fanno mai cadere Jarvis: tornano al modello come testo, così può
correggersi o spiegare cosa non ha funzionato.

Un risultato può contenere anche immagini (`ToolOutput`, es. i fotogrammi di un video): Claude le riceve come
blocchi immagine MCP; i cervelli OpenAI-compatibili (Ollama cloud, Bonsai) leggono solo testo e ricevono il testo
con una nota che dice quante immagini non hanno visto.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("jarvis.tools")


@dataclass(frozen=True)
class Image:
    """Un'immagine nel risultato di uno strumento (già compressa: JPEG o PNG)."""
    data: bytes
    mime: str = "image/jpeg"

    def __repr__(self) -> str:  # mai i byte nei registri
        return f"Image({self.mime}, {len(self.data)} byte)"


@dataclass
class ToolOutput:
    """Risultato ricco: testi e immagini nell'ordine in cui il modello li deve leggere."""
    parts: list[str | Image] = field(default_factory=list)

    @property
    def images(self) -> list[Image]:
        return [p for p in self.parts if isinstance(p, Image)]

    @property
    def text(self) -> str:
        return "\n".join(p for p in self.parts if isinstance(p, str))

    def as_text(self) -> str:
        """Per chi non vede le immagini: il testo, più quante immagini sono rimaste fuori (mai finta di averle viste)."""
        n = len(self.images)
        if not n:
            return self.text
        return (self.text + f"\n[{n} immagini non mostrate: questo cervello legge solo testo. Di' all'utente che hai "
                "solo il parlato e le scritte, non le immagini.]")

    def mcp_content(self) -> list[dict]:
        """I blocchi del risultato MCP per il Claude Agent SDK (immagine = base64 + mimeType)."""
        out: list[dict] = []
        for p in self.parts:
            if isinstance(p, Image):
                out.append({"type": "image", "data": base64.b64encode(p.data).decode("ascii"), "mimeType": p.mime})
            elif p:
                out.append({"type": "text", "text": p})
        return out or [{"type": "text", "text": ""}]


Handler = Callable[[dict[str, Any]], Awaitable[str | dict | ToolOutput] | str | dict | ToolOutput]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema (type=object)
    handler: Handler
    read_only: bool = False
    tags: set[str] = field(default_factory=set)
    group: str = "base"  # permesso che serve per usarlo (vedi jarvis/identity/guard.py)
    # il cancello dei permessi: None = si può; altrimenti il motivo del rifiuto (controllato qui, non dal cervello)
    gate: Callable[[Tool], str | None] | None = None

    async def run(self, args: dict[str, Any]) -> ToolOutput:
        """Il risultato completo, immagini comprese (per Claude)."""
        t = time.perf_counter()
        if self.gate is not None and (why := self.gate(self)):
            log.warning("strumento %s rifiutato: permesso del gruppo %s", self.name, self.group)
            return ToolOutput([why])
        try:
            missing = [k for k in self.parameters.get("required", []) if k not in args]
            if missing:
                return ToolOutput([f"Errore: argomenti mancanti per {self.name}: {', '.join(missing)}"])
            res = self.handler(args)
            if inspect.isawaitable(res):
                res = await res
            if isinstance(res, ToolOutput):
                out = res
            else:
                out = ToolOutput([res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)])
            pics = f" + {len(out.images)} immagini" if out.images else ""
            log.info("strumento %s(%s) -> %s%s [%.0f ms]", self.name, _short(args), _short(out.text), pics,
                     1000 * (time.perf_counter() - t))
            return out
        except Exception as e:  # l'errore diventa informazione per il modello
            log.exception("strumento %s fallito", self.name)
            return ToolOutput([f"Errore nello strumento {self.name}: {type(e).__name__}: {e}"])

    async def call(self, args: dict[str, Any]) -> str:
        """Solo testo (cervelli che non vedono immagini): le immagini diventano una nota."""
        return (await self.run(args)).as_text()


def _short(x: Any, n: int = 160) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def schema(properties: dict[str, dict], required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._gate: Callable[[Tool], str | None] | None = None

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"strumento duplicato: {tool.name}")
        tool.gate = tool.gate or self._gate
        self._tools[tool.name] = tool
        return tool

    def set_gate(self, gate: Callable[[Tool], str | None] | None) -> None:
        """Da qui in poi ogni strumento chiede il permesso prima di partire (anche quelli già registrati)."""
        self._gate = gate
        for t in self._tools.values():
            t.gate = gate

    def register(self, name: str, description: str, parameters: dict | None = None, read_only: bool = False,
                 tags: set[str] | None = None, group: str = "base") -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.add(Tool(name, description, parameters or schema({}), fn, read_only, tags or set(), group))
            return fn
        return deco

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    async def call(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Errore: strumento sconosciuto '{name}'. Disponibili: {', '.join(self._tools)}"
        return await tool.call(args)

    # ------------------------------------------------------------ adattatori

    def openai_tools(self) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}} for t in self]

    def sdk_tools(self) -> list:
        """Gli strumenti nel formato del Claude Agent SDK: il risultato porta anche le immagini."""
        from claude_agent_sdk import tool as sdk_tool

        out = []
        for t in self:
            async def _run(args: dict, _t: Tool = t) -> dict:
                res = await _t.run(args or {})
                return {"content": res.mcp_content(), "is_error": res.text.startswith("Errore")}

            out.append(sdk_tool(t.name, t.description, t.parameters)(_run))
        return out

    def claude_mcp_server(self, server_name: str = "jarvis"):
        """Server MCP in-process per il Claude Agent SDK; restituisce (config, nomi consentiti)."""
        from claude_agent_sdk import create_sdk_mcp_server

        server = create_sdk_mcp_server(server_name, tools=self.sdk_tools())
        allowed = [f"mcp__{server_name}__{t.name}" for t in self]
        return server, allowed

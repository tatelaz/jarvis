"""Ponte MCP: gli strumenti di Jarvis (ToolRegistry) esposti a OpenCode come server MCP "remoto" sul PC.

OpenCode chiama gli strumenti dei server MCP che ha in configurazione; il cervello di Jarvis su OpenCode (e i
suoi sotto-agenti) li raggiungono da qui. È il trasporto "streamable HTTP" di MCP ridotto al necessario, senza
sessioni né flussi aperti dal server: ogni richiesta JSON-RPC riceve una risposta JSON (lo prevede la specifica,
e il client ufficiale lo accetta: verificato nei test con `mcp.client.streamable_http`).

Sicurezza: questi strumenti agiscono sul PC (aprire app, usare WhatsApp, cambiare impostazioni), quindi
- ascolta solo su 127.0.0.1, su una porta scelta dal sistema;
- vuole il gettone segreto (generato a ogni avvio, mai scritto nei registri) nell'intestazione Authorization;
- rifiuta Host diversi da 127.0.0.1/localhost (un sito che si fa passare per il PC) e qualunque Origin (le
  richieste di una pagina web ce l'hanno sempre, OpenCode no).
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from .tools import ToolRegistry

log = logging.getLogger("jarvis.brain.mcp")

# versioni del protocollo che sappiamo servire (l'inizializzazione restituisce quella chiesta, se è fra queste)
PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
DEFAULT_PROTOCOL = "2025-06-18"

# una chiamata può essere intercettata prima di arrivare allo strumento (es. le conferme dei sotto-agenti)
Interceptor = Callable[[str, dict], Awaitable[str | None]]


class McpBridge:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self.host = host
        self.port: int | None = None
        self.token = secrets.token_urlsafe(32)
        self._servers: dict[str, ToolRegistry] = {}
        self._intercept: dict[str, Interceptor] = {}
        self._runner: web.AppRunner | None = None

    # ------------------------------------------------------------ configurazione

    def mount(self, name: str, registry: ToolRegistry, intercept: Interceptor | None = None) -> None:
        """Un server MCP di nome `name` (in OpenCode i suoi strumenti si chiamano `name_strumento`)."""
        self._servers[name] = registry
        if intercept is not None:
            self._intercept[name] = intercept

    def url(self, name: str) -> str:
        assert self.port is not None, "ponte non avviato"
        return f"http://{self.host}:{self.port}/mcp/{name}"

    def opencode_config(self) -> dict:
        """La sezione `mcp` della configurazione di OpenCode: un server remoto per ogni gruppo di strumenti."""
        return {name: {"type": "remote", "url": self.url(name), "enabled": True, "oauth": False,
                       "headers": {"Authorization": f"Bearer {self.token}"}} for name in self._servers}

    # ------------------------------------------------------------ avvio

    async def start(self) -> None:
        if self._runner is not None:
            return
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_route("*", "/mcp/{name}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        log.info("ponte MCP per OpenCode su %s:%s (%s)", self.host, self.port, ", ".join(self._servers))

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self.port = None

    # ------------------------------------------------------------ richieste

    def _allowed(self, request: web.Request) -> str | None:
        """None se la richiesta può passare, altrimenti il motivo del rifiuto."""
        host = request.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return "host"
        if request.headers.get("Origin") is not None:
            return "origin"
        auth = request.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {self.token}".encode()):
            return "gettone"
        return None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        why = self._allowed(request)
        if why is not None:
            log.warning("ponte MCP: richiesta rifiutata (%s)", why)
            return web.Response(status=403 if why != "gettone" else 401, text="non autorizzato")
        name = request.match_info["name"]
        registry = self._servers.get(name)
        if registry is None:
            return web.Response(status=404, text="server MCP sconosciuto")
        if request.method == "DELETE":  # chiusura della sessione: non ne teniamo
            return web.Response(status=200)
        if request.method != "POST":  # niente flusso di eventi aperto dal server (la specifica lo permette)
            return web.Response(status=405, headers={"Allow": "POST"})
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response(_error(None, -32700, "JSON non valido"), status=400)
        batch = isinstance(body, list)
        replies = [r for r in [await self._one(name, registry, m) for m in (body if batch else [body])] if r]
        if not replies:  # solo notifiche o risposte: nulla da restituire
            return web.Response(status=202)
        return web.json_response(replies if batch else replies[0])

    async def _one(self, name: str, registry: ToolRegistry, msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(None, -32600, "richiesta non valida")
        if "id" not in msg or "method" not in msg:
            return None  # notifica (es. notifications/initialized) o risposta del client
        mid, method, params = msg["id"], msg["method"], msg.get("params") or {}
        if method == "initialize":
            asked = str(params.get("protocolVersion", ""))
            return _result(mid, {"protocolVersion": asked if asked in PROTOCOLS else DEFAULT_PROTOCOL,
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": f"jarvis-{name}", "version": "1.0"}})
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.parameters}
                                           for t in registry]})
        if method == "tools/call":
            tool, args = str(params.get("name", "")), params.get("arguments") or {}
            if not isinstance(args, dict):
                return _error(mid, -32602, "arguments deve essere un oggetto")
            text = None
            if name in self._intercept:
                text = await self._intercept[name](tool, args)
            if text is None:
                text = await registry.call(tool, args)
            return _result(mid, {"content": [{"type": "text", "text": text}], "isError": text.startswith("Errore")})
        return _error(mid, -32601, f"metodo non supportato: {method}")


def _result(mid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

"""Cervello su Claude tramite Claude Agent SDK (usa il login di Claude Code: abbonamento, niente API key).

Una sessione persistente: la conversazione resta viva tra un turno e l'altro, così il modello
ricorda il filo del discorso e la cache del prompt rende ogni turno rapido (~0,8 s alla prima
parola con Opus 5.5 a sforzo basso, misurato). Gli strumenti di Jarvis girano nello stesso
processo (server MCP in-process); la sessione è isolata dai connettori e plugin dell'utente.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, SystemMessage, UserMessage

from .base import BrainEvent, BrainUnavailable, Done, Notice, TextDelta, ToolCall, ToolResult, ToolStart
from .claude_cli import ISOLATED_ENV, find_cli
from .tools import ToolRegistry

log = logging.getLogger("jarvis.brain.claude")

# Modelli senza ragionamento disattivabile accettano solo lo sforzo; Haiku 4.5 invece va spento
# esplicitamente, altrimenti "pensa" per ~1 s prima di ogni risposta (misurato: 1,36 s → 0,52 s).
NO_THINKING_MODELS = ("claude-haiku-4-5",)


# limiti dell'abbonamento: valgono per tutti i modelli Claude dell'account
_ACCOUNT_LIMIT = ("session limit", "usage limit", "weekly limit", "rate limit", "hit your limit", "credit balance",
                  "out of extra usage")
_TRANSIENT = ("overloaded", "529", "503", "timeout", "timed out", "connection")

# Claude Code stima 1600 token per ogni immagine di un risultato MCP e, sopra 25.000 token stimati (la metà per non
# contare davvero), toglie o rimpicciolisce le immagini in più (letto nel codice della CLI 2.1.310). Un video
# guardato da Jarvis ne ha fino a 20 (stima 32.000, veri ~8.500 a 768 px): col tetto alzato arrivano tutte intere.
MCP_OUTPUT_TOKENS = 100_000


def preview(content) -> str:
    """Il risultato di uno strumento in breve, per gli eventi: il testo, e le immagini contate (mai il base64)."""
    if not isinstance(content, list):
        return str(content)[:200]
    texts = [str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"]
    n = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image")
    return (" ".join(texts) + (f" [+{n} immagini]" if n else ""))[:200]


def classify_error(err: str, rejected_until: float | None = None) -> BrainUnavailable | None:
    """Errore del risultato -> BrainUnavailable (con portata e ora di ripristino) oppure None se è un errore normale."""
    low = err.lower()
    if rejected_until is not None or any(k in low for k in _ACCOUNT_LIMIT):
        return BrainUnavailable(err, retry_at=rejected_until or None, scope="provider")
    if any(k in low for k in _TRANSIENT):
        return BrainUnavailable(err)
    return None


class ClaudeBrain:
    def __init__(self, system_prompt: str, tools: ToolRegistry, model: str, effort: str | None = "low",
                 builtin_tools: list[str] | None = None, cwd: Path | None = None, cli_path: str | None = None,
                 max_turns: int = 12, name: str = "claude") -> None:
        self.name = name
        self.provider = "claude"  # tutti i modelli Claude condividono l'abbonamento
        self.system_prompt = system_prompt
        self.tools = tools
        self.model = model
        self.effort = effort
        self.builtin_tools = builtin_tools if builtin_tools is not None else ["WebSearch", "WebFetch"]
        self.cwd = cwd
        self.cli_path = cli_path
        self.max_turns = max_turns
        self._client: ClaudeSDKClient | None = None
        self._lock = asyncio.Lock()
        self._interrupting = False
        self._unfinished = False  # una risposta interrotta il cui risultato è ancora nella coda della sessione
        self.drain_timeout_s = 10.0
        self.session_id: str | None = None
        self.last_rate_limit: dict | None = None

    def _options(self, resume: str | None = None) -> ClaudeAgentOptions:
        server, allowed = self.tools.claude_mcp_server("jarvis")
        kw: dict = dict(
            cli_path=find_cli(self.cli_path), system_prompt=self.system_prompt, model=self.model,
            include_partial_messages=True, tools=list(self.builtin_tools),
            allowed_tools=allowed + list(self.builtin_tools), mcp_servers={"jarvis": server},
            strict_mcp_config=True, setting_sources=[], permission_mode="bypassPermissions",
            max_turns=self.max_turns, env={**ISOLATED_ENV, "MAX_MCP_OUTPUT_TOKENS": str(MCP_OUTPUT_TOKENS)},
            stderr=lambda line: log.debug("cli: %s", line.rstrip()),
        )
        if self.cwd:
            Path(self.cwd).mkdir(parents=True, exist_ok=True)
            kw["cwd"] = str(self.cwd)
        if resume:
            kw["resume"] = resume  # dopo un crash si riprende la stessa conversazione
        if self.effort == "off" or any(self.model.startswith(m) for m in NO_THINKING_MODELS):
            kw["thinking"] = {"type": "disabled"}
        elif self.effort:
            kw["effort"] = self.effort
        return ClaudeAgentOptions(**kw)

    async def start(self) -> None:
        if self._client is not None:
            return
        t = time.perf_counter()
        attempts = [self.session_id, None] if self.session_id else [None]
        last: Exception | None = None
        for resume in attempts:
            client = ClaudeSDKClient(options=self._options(resume))
            try:
                await asyncio.wait_for(client.connect(), timeout=60)
                self._client = client
                break
            except Exception as e:  # se la ripresa della sessione fallisce, si riparte puliti
                last = e
                log.warning("avvio Claude fallito (ripresa=%s): %s", bool(resume), e)
                with contextlib.suppress(Exception):
                    await client.disconnect()
        if self._client is None:
            raise BrainUnavailable(f"Claude non si avvia: {type(last).__name__}: {last}") from last
        log.info("sessione Claude pronta (%s) in %.1f s", self.model, time.perf_counter() - t)

    async def stop(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None
        self._unfinished = False  # una sessione nuova non ha avanzi da leggere

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def set_model(self, model: str) -> None:
        self.model = model
        if self._client is not None:
            await self._client.set_model(model)

    async def interrupt(self) -> None:
        if self._client is not None and self._lock.locked():
            self._interrupting = True
            with contextlib.suppress(Exception):
                await self._client.interrupt()

    async def _finish_interrupted(self) -> None:
        """La sessione ha un'unica coda di messaggi: chi smette di leggere a metà (interruzione) lascia lì il
        resto del turno, risultato compreso. Va letto prima della domanda nuova: il 26/10 il turno dopo riceveva
        quel risultato (error_during_execution, "Ho avuto un problema") e da lì ogni risposta arrivava una domanda
        in ritardo. Se il risultato non arriva, si riparte con una sessione ripresa."""
        if not self._unfinished or self._client is None:
            return
        try:
            async with asyncio.timeout(self.drain_timeout_s):
                async for m in self._client.receive_response():
                    if isinstance(m, ResultMessage):
                        break
        except Exception as e:  # anche TimeoutError
            log.warning("turno interrotto mai chiuso (%s): riparto con una sessione nuova",
                        type(e).__name__)
            await self.restart()
        self._unfinished = False

    async def respond(self, message: str) -> AsyncIterator[BrainEvent]:
        """Invia un messaggio e produce gli eventi della risposta, fino a Done (sempre emesso)."""
        async with self._lock:
            if self._client is None:
                await self.start()
            await self._finish_interrupted()
            assert self._client is not None
            self._interrupting = False
            text = ""
            pending_tools: dict[str, str] = {}
            rejected_until: float | None = None  # limite d'uso raggiunto: fino a quando
            try:
                await self._client.query(message)
                self._unfinished = True  # finché non si legge il risultato (un'interruzione può fermarci prima)
                async for m in self._client.receive_response():
                    if isinstance(m, StreamEvent):
                        ev = m.event
                        if (ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta"
                                and m.parent_tool_use_id is None):
                            text += ev["delta"]["text"]
                            yield TextDelta(ev["delta"]["text"])
                        elif (ev.get("type") == "content_block_start" and m.parent_tool_use_id is None
                              and (ev.get("content_block") or {}).get("type") in ("tool_use", "server_tool_use")):
                            yield ToolStart(str(ev["content_block"].get("name", "")).removeprefix("mcp__jarvis__"))
                    elif isinstance(m, AssistantMessage):
                        for b in m.content:
                            if type(b).__name__ == "ToolUseBlock":
                                pending_tools[b.id] = b.name
                                yield ToolCall(b.name.removeprefix("mcp__jarvis__"), dict(b.input or {}))
                        if m.error:
                            log.warning("errore del modello: %s", m.error)
                    elif isinstance(m, UserMessage) and isinstance(m.content, list):
                        for b in m.content:
                            if type(b).__name__ == "ToolResultBlock" and b.tool_use_id in pending_tools:
                                name = pending_tools.pop(b.tool_use_id).removeprefix("mcp__jarvis__")
                                yield ToolResult(name, not bool(b.is_error), preview(b.content))
                    elif isinstance(m, SystemMessage):
                        if m.subtype == "init":
                            self.session_id = m.data.get("session_id")
                    elif type(m).__name__ == "RateLimitEvent":
                        info = getattr(m, "rate_limit_info", None)
                        status, resets = getattr(info, "status", None), getattr(info, "resets_at", None)
                        self.last_rate_limit = {"status": status, "resets_at": resets,
                                                "type": getattr(info, "rate_limit_type", None),
                                                "utilization": getattr(info, "utilization", None)}
                        if status == "rejected":
                            rejected_until = float(resets) if resets else 0.0
                        yield Notice("rate_limit", self.last_rate_limit)
                    elif isinstance(m, ResultMessage):
                        self._unfinished = False
                        interrupted = self._interrupting
                        err = None
                        if m.is_error and not interrupted:
                            err = str(m.result or m.subtype)
                            unavailable = classify_error(err, rejected_until)
                            if unavailable is not None:
                                raise unavailable
                        yield Done(text, interrupted=interrupted, error=err, usage=m.usage or {}, model=self.model)
                        return
                self._unfinished = False  # il flusso è finito: non c'è altro da leggere
                yield Done(text, interrupted=self._interrupting, error="flusso terminato senza risultato")
            except BrainUnavailable:
                raise
            except Exception as e:
                log.exception("sessione Claude caduta")
                await self.stop()  # alla prossima richiesta si riparte da una sessione nuova
                raise BrainUnavailable(f"{type(e).__name__}: {e}") from e

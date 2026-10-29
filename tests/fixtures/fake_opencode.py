"""Un finto `opencode serve` per i test: stesse API, stessi eventi e stesso incapsulamento del vero.

Le forme vengono dalla specifica OpenAPI di OpenCode 1.19.3 (letta da /doc il 28/10/2026) e dagli eventi registrati
dal vivo quel giorno (sessione, stato "busy", stato "retry" del limite gratuito, flusso /global/event con
{"directory", "project", "payload"}). Ogni test decide cosa fa il "modello" con `behave(turn, testo)`.

Si usa nello stesso processo (`FakeOpenCode().start()`) oppure come processo vero al posto di opencode.exe:
    python fake_opencode.py serve --port N --hostname 127.0.0.1 --pure
(la parola d'ordine arriva da OPENCODE_SERVER_PASSWORD; FAKE_OC_ENV scrive l'ambiente ricevuto in un file).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import web

CATALOG = {
    "connected": ["opencode"],
    "default": {"opencode": "big-pickle"},
    "all": [{
        "id": "opencode", "name": "OpenCode", "env": ["OPENCODE_API_KEY"], "options": {"apiKey": "public"},
        "models": {
            "glm-5.3-flash-free": {
                "id": "glm-5.3-flash-free", "name": "GLM-5.3-Flash Free", "release_date": "2026-08-26",
                "capabilities": {"toolcall": True, "output": {"text": True}}, "status": "active",
                "cost": {"input": 0, "output": 0}, "limit": {"context": 200000, "output": 32000},
                "variants": {"low": {"reasoningEffort": "low"}, "high": {}, "max": {}}},
            "big-pickle": {
                "id": "big-pickle", "name": "Big Pickle", "release_date": "2025-10-17",
                "capabilities": {"toolcall": True, "output": {"text": True}}, "status": "active",
                "cost": {"input": 0, "output": 0}, "limit": {"context": 200000, "output": 32000}},
            "nemotron-3.5-lightning-free": {
                "id": "nemotron-3.5-lightning-free", "release_date": "2026-08-11",
                "capabilities": {"toolcall": True, "output": {"text": True}}, "status": "active",
                "cost": {"input": 0, "output": 0}, "variants": {"minimal": {}, "high": {}}},
            "vecchio-free": {  # deprecato: fuori
                "id": "vecchio-free", "release_date": "2026-01-01", "capabilities": {"toolcall": True},
                "status": "deprecated", "cost": {"input": 0, "output": 0}},
            "muto-free": {  # niente strumenti: fuori
                "id": "muto-free", "release_date": "2026-09-01", "capabilities": {"toolcall": False},
                "status": "active", "cost": {"input": 0, "output": 0}},
            "kimi-k3": {  # a pagamento: fuori
                "id": "kimi-k3", "release_date": "2026-07-16", "capabilities": {"toolcall": True},
                "status": "active", "cost": {"input": 3, "output": 15}},
        },
    }],
}

_ids = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}{next(_ids):06d}"


class Turn:
    """Un giro del modello dentro una sessione: i suoi aiutanti emettono gli eventi come il server vero."""

    def __init__(self, fake: FakeOpenCode, sid: str, body: dict) -> None:
        self.fake, self.sid, self.body = fake, sid, body
        self.session = fake.sessions[sid]
        self.user_id, self.asst_id = new_id("msg"), new_id("msg")
        self.parts: list[dict] = []
        self.error: dict | None = None
        self.model = (body.get("model") or {}).get("modelID")
        self.text = "".join(p.get("text", "") for p in body.get("parts", []) if p.get("type") == "text")

    @property
    def aborted(self) -> bool:
        return self.sid in self.fake.aborted

    async def emit(self, typ: str, **props: Any) -> None:
        await self.fake.emit(self.session["directory"], {"id": new_id("evt"), "type": typ,
                                                         "properties": {"sessionID": self.sid, **props}})

    async def user_message(self, hold_assistant: bool = False) -> None:
        await self.emit("message.updated", info={"id": self.user_id, "role": "user", "sessionID": self.sid,
                                                 "agent": self.body.get("agent"), "system": self.body.get("system")})
        await self.emit("message.part.updated", part={"type": "text", "text": self.text, "id": new_id("prt"),
                                                      "messageID": self.user_id, "sessionID": self.sid})
        await self.status("busy")
        if not hold_assistant:
            await self.assistant_message()

    async def assistant_message(self) -> None:
        await self.emit("message.updated", info={"id": self.asst_id, "role": "assistant", "sessionID": self.sid,
                                                 "parentID": self.user_id, "modelID": self.model,
                                                 "providerID": "opencode"})

    async def status(self, kind: str, **extra: Any) -> None:
        await self.emit("session.status", status={"type": kind, **extra})

    async def say(self, text: str, pieces: int = 3, kind: str = "text", final: bool = True, delay: float = 0.0,
                  part_first: bool = True, in_flight: bool = False) -> str:
        """in_flight=True: i pezzi continuano ad arrivare anche dopo /abort (quelli già partiti dal server)."""
        pid = new_id("prt")
        part = {"id": pid, "messageID": self.asst_id, "sessionID": self.sid, "type": kind, "text": ""}
        if part_first:
            await self.emit("message.part.updated", part=dict(part))
        step = max(1, -(-len(text) // pieces))
        sent = ""
        for i in range(0, len(text), step):
            if self.aborted and not in_flight:
                break
            await self.emit("message.part.delta", messageID=self.asst_id, partID=pid, field="text",
                            delta=text[i:i + step])
            sent += text[i:i + step]
            if delay:
                await asyncio.sleep(delay)
        if not part_first:
            await self.emit("message.part.updated", part=dict(part))
        part["text"] = sent  # interrotto: la parte finale ha solo ciò che è stato generato
        if final:
            await self.emit("message.part.updated", part=dict(part))
        self.parts.append(part)
        return pid

    async def tool(self, tool: str, inp: dict, output: str | None = None, error: str | None = None,
                   ask: str | None = None, patterns: list[str] | None = None, metadata: dict | None = None,
                   call_mcp: bool = False) -> str | None:
        """Un uso di strumento come lo fa OpenCode: in attesa -> in corso -> (permesso) -> finito o errore.
        call_mcp=True: lo strumento lo esegue davvero il ponte MCP di Jarvis (come farebbe OpenCode)."""
        call, pid = new_id("call"), new_id("prt")
        base = {"id": pid, "messageID": self.asst_id, "sessionID": self.sid, "type": "tool", "callID": call,
                "tool": tool}
        await self.emit("message.part.updated", part={**base, "state": {"status": "pending", "input": {}, "raw": ""}})
        await self.emit("message.part.updated", part={**base, "state": {"status": "running", "input": inp,
                                                                         "time": {"start": 1}}})
        reply = None
        if ask is not None:
            reply = await self.fake.ask(self.sid, ask, patterns or ["*"], metadata or {},
                                        {"messageID": self.asst_id, "callID": call})
            if reply == "reject":
                error = self.fake.reply_messages.get(call) or "rifiutato"
        if error is None and call_mcp:
            output = await self.fake.call_mcp(tool, inp)
        state = ({"status": "error", "input": inp, "error": error, "time": {"start": 1, "end": 2}} if error is not None
                 else {"status": "completed", "input": inp, "output": output or "", "title": tool, "metadata": {},
                       "time": {"start": 1, "end": 2}})
        final = {**base, "state": state}
        await self.emit("message.part.updated", part=final)
        self.parts.append(final)
        return reply

    async def wait_abort(self, timeout: float = 10.0) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.fake.abort_event(self.sid).wait(), timeout)

    def result(self) -> dict:
        info = {"id": self.asst_id, "role": "assistant", "sessionID": self.sid, "modelID": self.model,
                "providerID": "opencode"}
        if self.aborted:
            info["error"] = {"name": "MessageAbortedError", "data": {"message": "Aborted"}}
        elif self.error is not None:
            info["error"] = self.error
        return {"info": info, "parts": self.parts}


async def echo(turn: Turn, text: str) -> None:
    await turn.say("Ricevuto.")


class FakeOpenCode:
    def __init__(self, password: str = "segreta", catalog: dict | None = None) -> None:
        self.password = password
        self.catalog = catalog or CATALOG
        self.behave: Callable[[Turn, str], Awaitable[None]] = echo
        self.sessions: dict[str, dict] = {}
        self.prompts: list[dict] = []  # {"sid", "directory", "body", "async"}
        self.subscribers: list[asyncio.Queue] = []
        self.pending: dict[str, asyncio.Future] = {}  # richieste di permesso -> risposta
        self.asked: list[dict] = []
        self.replies: dict[str, dict] = {}
        self.reply_messages: dict[str, str] = {}  # callID -> messaggio del rifiuto
        self.aborted: set[str] = set()
        self.abort_calls: list[str] = []  # ogni /abort, nell'ordine
        self.deleted: list[str] = []
        self.busy: set[str] = set()
        self.forget_sessions = False
        self._abort_events: dict[str, asyncio.Event] = {}
        self._bg: set[asyncio.Task] = set()
        self.mcp: dict = {}  # la sezione mcp della configurazione (come la riceve OpenCode)
        self.turns: list[Turn] = []
        self._runner: web.AppRunner | None = None
        self.url = ""
        app = web.Application(middlewares=[self._auth])
        r = app.router

        def answer(make):
            async def handler(_request):
                return web.json_response(make())
            return handler

        r.add_get("/global/health", answer(lambda: {"healthy": True, "version": "1.19.3"}))
        r.add_get("/global/event", self._events)
        r.add_post("/global/dispose", answer(lambda: True))
        r.add_get("/provider", answer(lambda: self.catalog))
        r.add_post("/session", self._create)
        r.add_get("/session/status", answer(lambda: {s: {"type": "busy"} for s in self.busy}))
        r.add_delete("/session/{sid}", self._delete)
        r.add_post("/session/{sid}/message", self._prompt)
        r.add_post("/session/{sid}/prompt_async", self._prompt_async)
        r.add_post("/session/{sid}/abort", self._abort)
        r.add_get("/session/{sid}/message", self._messages)
        r.add_get("/session/{sid}/message/{mid}", self._message)
        r.add_post("/permission/{pid}/reply", self._reply)
        self.app = app

    # ------------------------------------------------------------ server

    async def start(self, port: int = 0) -> str:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self.url

    async def stop(self) -> None:
        for q in self.subscribers:
            q.put_nowait(None)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    @web.middleware
    async def _auth(self, request: web.Request, handler):
        want = "Basic " + base64.b64encode(f"opencode:{self.password}".encode()).decode()
        if request.headers.get("Authorization") != want:
            return web.Response(status=401, text="Unauthorized")
        return await handler(request)

    async def emit(self, directory: str, payload: dict) -> None:
        for q in list(self.subscribers):
            q.put_nowait({"directory": directory, "project": "global", "payload": payload})
        await asyncio.sleep(0)

    async def _events(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        try:
            first = {"payload": {"id": new_id("evt"), "type": "server.connected", "properties": {}}}
            await resp.write(f"data: {json.dumps(first)}\n\n".encode())
            await resp.write(f"data: {json.dumps({'payload': {'type': 'server.heartbeat', 'properties': {}}})}\n\n"
                             .encode())
            while True:
                item = await q.get()
                if item is None:
                    break
                await resp.write(f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.subscribers.remove(q)
        return resp

    # ------------------------------------------------------------ sessioni e messaggi

    async def _create(self, request: web.Request) -> web.Response:
        body = await request.json()
        sid = new_id("ses")
        directory = request.query.get("directory", "")
        info = {"id": sid, "directory": directory, "title": body.get("title"), "messages": []}
        if not self.forget_sessions:  # forget_sessions: archivio rotto, la sessione sparisce subito
            self.sessions[sid] = info
        await self.emit(directory, {"id": new_id("evt"), "type": "session.created",
                                    "properties": {"sessionID": sid, "info": info}})
        return web.json_response({"id": sid, "directory": directory, "title": body.get("title")})

    async def _delete(self, request: web.Request) -> web.Response:
        sid = request.match_info["sid"]
        self.deleted.append(sid)
        self.sessions.pop(sid, None)
        return web.json_response(True)

    def _session(self, request: web.Request) -> str:
        sid = request.match_info["sid"]
        if sid not in self.sessions:
            raise web.HTTPNotFound(text=json.dumps({"name": "NotFoundError", "data": {"message": "Session not found"}}))
        return sid

    async def _run(self, sid: str, body: dict) -> dict:
        turn = Turn(self, sid, body)
        self.turns.append(turn)
        self.aborted.discard(sid)
        self._abort_events[sid] = asyncio.Event()
        self.busy.add(sid)
        try:
            await turn.user_message()
            await self.behave(turn, turn.text)
        finally:
            self.busy.discard(sid)
        result = turn.result()
        self.sessions.get(sid, {}).setdefault("messages", []).extend(
            [{"info": {"id": turn.user_id, "role": "user"}, "parts": [{"type": "text", "text": turn.text}]}, result])
        await turn.status("idle")
        await turn.emit("session.idle")
        return result

    async def _prompt(self, request: web.Request) -> web.Response:
        sid = self._session(request)
        body = await request.json()
        self.prompts.append({"sid": sid, "directory": request.query.get("directory"), "body": body, "async": False})
        return web.json_response(await self._run(sid, body))

    async def _prompt_async(self, request: web.Request) -> web.Response:
        sid = self._session(request)
        body = await request.json()
        self.prompts.append({"sid": sid, "directory": request.query.get("directory"), "body": body, "async": True})
        if sid in self.busy:  # come il vero: il messaggio entra in coda e il modello lo legge al passo dopo
            return web.Response(status=204)
        task = asyncio.ensure_future(self._run(sid, body))
        self._bg.add(task)  # un riferimento forte: un compito senza riferimenti può sparire a metà
        task.add_done_callback(self._bg.discard)
        return web.Response(status=204)

    def abort_event(self, sid: str) -> asyncio.Event:
        return self._abort_events.setdefault(sid, asyncio.Event())

    async def _abort(self, request: web.Request) -> web.Response:
        sid = request.match_info["sid"]
        self.aborted.add(sid)
        self.abort_calls.append(sid)
        self.abort_event(sid).set()
        return web.json_response(True)

    async def _messages(self, request: web.Request) -> web.Response:
        sid = self._session(request)
        return web.json_response(self.sessions[sid].get("messages", []))

    async def _message(self, request: web.Request) -> web.Response:
        sid, mid = self._session(request), request.match_info["mid"]
        for t in self.turns:
            if t.sid == sid and t.asst_id == mid:
                return web.json_response({"info": {"id": mid, "role": "assistant"}, "parts": t.parts})
        raise web.HTTPNotFound()

    # ------------------------------------------------------------ permessi

    async def ask(self, sid: str, permission: str, patterns: list[str], metadata: dict, tool: dict | None) -> str:
        pid = new_id("per")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[pid] = fut
        props = {"id": pid, "sessionID": sid, "permission": permission, "patterns": patterns, "metadata": metadata,
                 "always": patterns}
        if tool is not None:
            props["tool"] = tool
        self.asked.append(props)
        await self.emit(self.sessions[sid]["directory"], {"id": new_id("evt"), "type": "permission.asked",
                                                          "properties": props})
        reply = await asyncio.wait_for(fut, 30)
        if tool is not None and self.replies[pid].get("message"):
            self.reply_messages[tool["callID"]] = self.replies[pid]["message"]
        return reply

    async def _reply(self, request: web.Request) -> web.Response:
        pid = request.match_info["pid"]
        body = await request.json()
        assert body.get("reply") in ("once", "always", "reject"), body
        self.replies[pid] = body
        fut = self.pending.pop(pid, None)
        if fut is None:
            raise web.HTTPNotFound()
        fut.set_result(body["reply"])
        return web.json_response(True)

    # ------------------------------------------------------------ strumenti MCP (come li chiama OpenCode)

    async def call_mcp(self, tool: str, args: dict) -> str:
        server, _, name = tool.partition("_")
        conf = self.mcp[server]
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        async with aiohttp.ClientSession() as s, s.post(conf["url"], json=req, headers=conf["headers"]) as r:
            data = await r.json()
        return data["result"]["content"][0]["text"]


# ---------------------------------------------------------------------- come processo vero


def main(argv: list[str]) -> None:
    port = int(argv[argv.index("--port") + 1])
    out = os.environ.get("FAKE_OC_ENV")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd()}, f)
    if os.environ.get("FAKE_OC_CRASH"):
        sys.exit(3)
    if os.environ.get("FAKE_OC_CHILD"):  # come un server MCP locale (browser, desktop) avviato da OpenCode
        import subprocess

        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    fake = FakeOpenCode(os.environ["OPENCODE_SERVER_PASSWORD"])
    with contextlib.suppress(ValueError, KeyError):
        fake.mcp = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}")).get("mcp", {})

    async def run() -> None:
        await fake.start(port)
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main(sys.argv[1:])

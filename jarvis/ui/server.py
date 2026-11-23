"""Server locale dell'interfaccia: pagina web + WebSocket con gli eventi di Jarvis.

Ascolta solo su 127.0.0.1. Ogni client riceve lo stato corrente appena si collega e poi gli
eventi in tempo reale; può inviare comandi (testo scritto, stop, premi-per-parlare, conferme).
I livelli audio sono limitati a ~20 aggiornamenti al secondo per non sprecare CPU.

Chi può collegarsi (revisione del 27/10): il testo scritto vale come l'utente e le conferme approvano azioni,
quindi una pagina web aperta nel browser non deve poter parlare con Jarvis. Il browser mette da solo
Origin e Host e una pagina non può falsificarli: si accettano solo l'Origin della finestra stessa (o nessuno:
un programma locale, che gira già con i permessi dell'utente) e solo Host 127.0.0.1/localhost sulla nostra porta
(contro il "DNS rebinding", un sito che si fa passare per 127.0.0.1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from .look import ui_config

log = logging.getLogger("jarvis.ui")
WEB_DIR = Path(__file__).parent / "web"


class UiServer:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 8791) -> None:
        self.app = app  # istanza di Jarvis
        self.host, self.port = host, port
        self._runner: web.AppRunner | None = None
        self.clients: set[web.WebSocketResponse] = set()
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        self.refused = 0

    def trusted(self, req: web.Request, ws: bool = False) -> bool:
        """Host nostro sempre; per il canale dei comandi anche l'Origin (assente = programma locale, non un sito)."""
        if req.headers.get("Host", "") not in self.hosts:
            return False
        origin = req.headers.get("Origin")
        return not ws or origin is None or origin in self.origins

    @web.middleware
    async def _guard(self, req: web.Request, handler):
        if not self.trusted(req, ws=req.path == "/ws"):
            self.refused += 1
            log.warning("collegamento rifiutato all'interfaccia: Host %r, Origin %r",
                        req.headers.get("Host"), req.headers.get("Origin"))
            return web.Response(status=403, text="vietato")
        return await handler(req)

    async def start(self) -> None:
        wapp = web.Application(middlewares=[self._guard, no_cache])
        wapp.router.add_get("/ws", self._ws)
        wapp.router.add_get("/", self._index)
        wapp.router.add_static("/static", WEB_DIR)
        self._runner = web.AppRunner(wapp, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        log.info("interfaccia su http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        for ws in list(self.clients):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _index(self, _req: web.Request) -> web.Response:
        return index_response(WEB_DIR)

    def _snapshot(self) -> dict:
        a = self.app
        tasks = [t.public() for t in a.tasks.tasks.values()] if getattr(a, "tasks", None) else []
        return {"type": "snapshot", "state": a.bus.state.get("state", {}).get("state", "avvio"),
                "history": [e for e in a.bus.history if e["type"] in ("user", "jarvis_done", "show", "error", "notice", "memory", "enroll")][-40:],
                "tasks": tasks, "config": ui_config(a.cfg), "rest": a.bus.state.get("rest"),
                "audio": a.bus.state.get("audio"),
                "identity": a.identity_state() if hasattr(a, "identity_state") else None}

    async def _ws(self, req: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        self.clients.add(ws)
        q = self.app.bus.subscribe(1000)
        sender = asyncio.ensure_future(self._pump(ws, q))
        try:
            await ws.send_json(self._snapshot(), dumps=_dumps)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._command(json.loads(msg.data))
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            sender.cancel()
            self.app.bus.unsubscribe(q)
            self.clients.discard(ws)
        return ws

    async def _pump(self, ws: web.WebSocketResponse, q: asyncio.Queue) -> None:
        last_level = 0.0
        while not ws.closed:
            ev = await q.get()
            if ev["type"] == "level":
                now = time.monotonic()
                if now - last_level < 0.05:
                    continue
                last_level = now
            elif ev["type"] == "settings_changed" and str(ev.get("key", "")).startswith(("ui.", "persona.")):
                ev = ui_config(self.app.cfg)  # tema, colore, testo, effetti: la pagina si ridisegna subito
            try:
                await ws.send_json(ev, dumps=_dumps)
            except (ConnectionResetError, RuntimeError):
                return

    async def _report_preview(self, text: str, crash: bool) -> None:
        why = self.app._reports_ready()
        if why:
            self._report = None
            self.app.bus.emit("notice", kind="report_preview", info={"ok": False, "message": why.capitalize() + "."})
            return
        self._report = await asyncio.to_thread(self.app.make_report, "caduta" if crash else "segnalazione", text)
        self.app.bus.emit("notice", kind="report_preview", info={"ok": True, "text": self._report.text})

    async def _report_send(self) -> None:
        report, self._report = getattr(self, "_report", None), None  # una volta sola: per rimandarla si rivede
        if report is None:
            message, ok = "Prima guarda cosa parte.", False
        else:
            message = await self.app.deliver_report(report)
            ok = message.startswith("Segnalazione mandata")
        self.app.bus.emit("notice", kind="report", info={"ok": ok, "message": message})

    async def _command(self, cmd: dict) -> None:
        conv = self.app.conv
        kind = cmd.get("cmd")
        if kind == "text" and conv:
            await conv.submit_text(str(cmd.get("text", "")))
        elif kind == "stop" and conv:
            await conv.stop_all()
        elif kind == "ptt" and conv:
            conv.arm_push_to_talk()
        elif kind == "confirm":
            self.app.tasks.answer(int(cmd["id"]), bool(cmd["approved"]))
        elif kind == "cancel_task":
            await self.app.tasks.cancel(int(cmd["id"]))
        elif kind == "rest":  # pulsante della luna: riposo o risveglio
            if self.app.resting:
                await self.app.wake_up(None)
            else:
                await self.app.rest()
        elif kind == "passphrase":  # dalla finestra: il testo non si scrive mai da nessuna parte
            result = self.app.set_passphrase(str(cmd.get("value", "")))
            self.app.bus.emit("notice", kind="passphrase", info={"ok": result == "ok", "message": result})
        elif kind == "gmail_app_password":  # dalla finestra: la password non si scrive mai da nessuna parte
            result = await self.app.gmail_app_password(str(cmd.get("email", "")), str(cmd.get("value", "")))
            self.app.bus.emit("notice", kind="gmail", info={"ok": result == "ok", "message": result})
        elif kind == "report_preview":  # dalla finestra: prima si vede il testo esatto, poi parte solo quello
            await self._report_preview(str(cmd.get("text", "")), bool(cmd.get("crash")))
        elif kind == "report_send":
            await self._report_send()
        elif kind == "mute":
            self.app.pipeline.listener.enabled = not bool(cmd.get("muted"))
            self.app.bus.emit("muted", sticky=True, muted=bool(cmd.get("muted")))
        else:
            log.warning("comando sconosciuto dall'interfaccia: %s", {k: v for k, v in cmd.items() if k != "value"})


_STATIC = re.compile(r'(/static/[\w./-]+\.(?:js|css))"')


def index_response(web_dir: Path) -> web.Response:
    """La pagina con ogni file firmato dalla sua data di modifica: un file cambiato ha un indirizzo nuovo e non
    può arrivare da una copia vecchia (successo davvero: la finestra mostrava ancora la grafica di prima)."""
    html = (web_dir / "index.html").read_text(encoding="utf-8")

    def sign(m: re.Match) -> str:
        f = web_dir / m.group(1).removeprefix("/static/")
        v = int(f.stat().st_mtime) if f.exists() else 0
        return f'{m.group(1)}?v={v}"'

    return web.Response(text=_STATIC.sub(sign, html), content_type="text/html", headers={"Cache-Control": "no-store"})


@web.middleware
async def no_cache(request: web.Request, handler):
    """La pagina non si tiene copie vecchie: dopo una modifica alla grafica (anche fatta da Jarvis) si vede subito."""
    resp = await handler(request)
    if request.path == "/" or request.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

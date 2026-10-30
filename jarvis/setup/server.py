"""Server della pagina del primo avvio: la pagina (jarvis/setup/web) e le richieste che le servono, in JSON.

Ascolta solo su 127.0.0.1, sulla stessa porta della finestra di Jarvis (prima c'è il primo avvio, poi Jarvis).
Le stesse difese della finestra (jarvis/ui/server.py): Host nostro sempre, contro il "DNS rebinding"; per ogni
richiesta che cambia qualcosa (POST) anche l'Origin nostro (o nessuno: un programma locale) e il corpo in JSON,
che un sito non può mandare da un'altra origine senza un permesso che qui non diamo. Le GET non cambiano niente.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

from ..ui.server import index_response, no_cache
from .flow import Setup, SetupError

log = logging.getLogger("jarvis.setup")
WEB_DIR = Path(__file__).parent / "web"


class SetupServer:
    def __init__(self, setup: Setup, host: str = "127.0.0.1", port: int = 8791) -> None:
        self.setup = setup
        self.host, self.port = host, port
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        self._runner: web.AppRunner | None = None
        self.refused = 0

    def trusted(self, req: web.Request) -> bool:
        if req.headers.get("Host", "") not in self.hosts:
            return False
        if req.method == "GET":
            return True
        origin = req.headers.get("Origin")
        return (origin is None or origin in self.origins) and req.content_type == "application/json"

    @web.middleware
    async def _guard(self, req: web.Request, handler):
        if not self.trusted(req):
            self.refused += 1
            log.warning("primo avvio: richiesta rifiutata (%s %s, Host %r, Origin %r)", req.method, req.path,
                        req.headers.get("Host"), req.headers.get("Origin"))
            return web.Response(status=403, text="vietato")
        try:
            return await handler(req)
        except SetupError as e:
            return web.json_response({"error": str(e)}, status=409)

    def app(self) -> web.Application:
        a = web.Application(middlewares=[self._guard, no_cache])
        r = a.router
        r.add_get("/", self._index)
        r.add_static("/static", WEB_DIR)
        s = self.setup
        r.add_get("/api/start", self._json(lambda body: s.start_info()))
        r.add_post("/api/lang", self._json(lambda body: s.set_language(str(body.get("lang", "")))))
        r.add_get("/api/pc", self._json(lambda body: s.check_pc()))
        r.add_get("/api/brain", self._json(lambda body: s.brain_status()))
        r.add_post("/api/brain", self._json(lambda body: s.set_brain(str(body.get("provider", "")))))
        r.add_post("/api/brain/login", self._json(lambda body: s.claude_login()))
        r.add_post("/api/brain/install", self._json(lambda body: s.claude_install()))
        r.add_get("/api/download", self._json(lambda body: s.download_status()))
        r.add_post("/api/download", self._json(lambda body: s.start_download()))
        r.add_post("/api/download/cancel", self._json(lambda body: s.cancel_download()))
        r.add_post("/api/finish", self._json(lambda body: s.finish(str(body.get("name", "")),
                                                                     body.get("autostart") is True)))
        r.add_post("/api/quit", self._json(lambda body: s.quit.set()))
        return a

    @staticmethod
    async def _index(_req: web.Request) -> web.Response:
        return index_response(WEB_DIR)

    @staticmethod
    def _json(action):
        async def handler(req: web.Request) -> web.Response:
            body = {}
            if req.method == "POST" and req.can_read_body:
                try:
                    body = await req.json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return web.json_response({"error": "richiesta_non_valida"}, status=400)
                if not isinstance(body, dict):
                    return web.json_response({"error": "richiesta_non_valida"}, status=400)
            result = action(body)
            if hasattr(result, "__await__"):
                result = await result
            return web.json_response(result if isinstance(result, dict) else {"ok": True})
        return handler

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app(), access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        log.info("primo avvio su http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

"""Vetrina dell'interfaccia: la pagina vera di Jarvis con dati finti, senza motori né cervello.

Serve per disegnare e controllare la grafica in ogni stato e con ogni tema, e per i test automatici della
finestra. Parla lo stesso protocollo del server vero (fotografia iniziale + eventi + comandi). Nessuna
conversazione si ripete da sola: si sceglie una scena e la si guarda.

Uso: python scripts/vetrina.py [--porta 8793] [--tema mark3|cinema] [--scena attesa|ascolto|ragiono|parla|compiti|conferma|riposo|demo]
Comandi in più (per i test): {"cmd": "vetrina", "scena": "..."} · {"cmd": "vetrina", "tema": "cinema"}
· {"cmd": "vetrina", "audio": false} (audio sparito) / true (tornato)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from pathlib import Path

from aiohttp import WSMsgType, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.ui.server import index_response, no_cache

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "jarvis" / "ui" / "web"

HISTORY = [
    {"type": "user", "text": "Jarvis, com'è il tempo domani a Milano?"},
    {"type": "jarvis_done", "text": "Pioggia leggera fino a metà pomeriggio, signore, poi schiarite. Massima di "
                                    "diciassette gradi: le consiglio l'impermeabile, non l'ombrello."},
]
QUESTION = "Jarvis, a che ora parte il mio volo domani?"
ANSWER = ("Alle sette e quaranta da Linate, signore: terminal due, uscita B12. Con il traffico di adesso le "
          "conviene partire entro le sei e cinque.")
PARTIAL = "Jarvis, a che ora parte il mio vo"
RUNNING = {"id": 3, "title": "Rapporto vendite di settembre", "goal": "", "status": "in corso",
           "progress": ["crea il grafico per regione"], "result": None, "error": None, "question": None,
           "created": 0, "started": 0, "finished": None, "kind": "lavoro"}
ASKING = {"id": 2, "title": "Pulizia della cartella Download", "goal": "", "status": "attende conferma",
          "progress": ["trovati 214 file vecchi"], "result": None, "error": None,
          "question": "Sposto nel cestino 214 file vecchi della cartella Download (3,1 GB)?",
          "created": 0, "started": 0, "finished": None, "kind": "lavoro"}
CARD = {"type": "show", "title": "Volo AZ 2041 · domani",
        "markdown": "**Linate → Fiumicino**\n\n- Partenza 07:40, terminal 2, uscita B12\n- Arrivo 08:50\n\n`PNR: X7K2QM`"}
SCENES = ["attesa", "ascolto", "ragiono", "parla", "compiti", "conferma", "riposo", "demo"]


class Vetrina:
    def __init__(self, web_dir: Path, scene: str, theme: str) -> None:
        self.web_dir, self.scene, self.theme = web_dir, scene, theme
        self.clients: set[web.WebSocketResponse] = set()
        self.jobs: set[asyncio.Task] = set()  # riferimenti: un'attività senza riferimenti può sparire a metà
        self.state = "in attesa"
        self.speak_from = 0.0
        self.commands: list[dict] = []  # i test controllano cosa ha mandato la pagina
        self.audio: dict | None = None  # ultimo stato dell'audio, per chi si collega dopo

    def spawn(self, coro) -> None:
        job = asyncio.ensure_future(coro)
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def broadcast(self, ev: dict) -> None:
        if ev["type"] == "state":
            self.state = ev["state"]
            if ev["state"] == "parla":
                self.speak_from = time.monotonic()
        for ws in list(self.clients):
            try:
                await ws.send_str(json.dumps(ev, ensure_ascii=False))
            except (ConnectionResetError, RuntimeError):
                self.clients.discard(ws)

    def config(self) -> dict:
        return {"type": "ui_config", "theme": self.theme, "accent": "", "text_scale": 1.0, "effects": "pieni",
                "user": "Andrea", "brain": "claude-opus-5-5"}

    def snapshot(self) -> dict:
        return {"type": "snapshot", "state": "in attesa", "history": HISTORY, "tasks": [], "config": self.config(),
                "rest": {"active": False, "listening": True}, "audio": self.audio,
                "identity": {"available": True, "active": True, "people": [{"name": "Andrea", "owner": True}],
                             "passphrase": False}}

    # ------------------------------------------------------------ livelli finti, con la parte già detta
    async def levels(self) -> None:
        t0 = time.monotonic()
        while True:
            t = time.monotonic() - t0
            syll = abs(math.sin(t * 7.3)) * (0.55 + 0.45 * abs(math.sin(t * 1.7)))
            voice = 0.045 * syll + 0.004 * random.random()
            ev = {"type": "level", "mic": round(voice if self.state == "ascolto" else 0.002 * random.random(), 4),
                  "out": round(voice * 1.3 if self.state == "parla" else 0.0, 4), "said": -1}
            if self.state == "parla":
                ev["said"] = int((time.monotonic() - self.speak_from) * 17)  # ~ritmo della voce di Jarvis
            await self.broadcast(ev)
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------ scene
    async def hold(self, scene: str) -> None:
        """Mette la pagina in una scena e la lascia lì (lo stato resta, i livelli si muovono)."""
        await self.broadcast({"type": "rest", "active": False, "listening": True})
        await self.broadcast({"type": "task", **RUNNING})
        if scene != "conferma":
            await self.broadcast({"type": "task", **{**ASKING, "question": None, "status": "in coda"}})
        if scene == "ascolto":
            await self.broadcast({"type": "state", "state": "ascolto"})
            await self.broadcast({"type": "partial", "text": PARTIAL})
        elif scene == "ragiono":
            await self.broadcast({"type": "user", "text": QUESTION})
            await self.broadcast({"type": "state", "state": "elaborazione"})
        elif scene == "parla":
            await self.broadcast({"type": "user", "text": QUESTION})
            await self.broadcast({"type": "state", "state": "parla"})
            await self.broadcast({"type": "jarvis_delta", "text": ANSWER})
        elif scene == "compiti":
            await self.broadcast({"type": "state", "state": "in attesa"})
            await self.broadcast(CARD)
        elif scene == "conferma":
            await self.broadcast({"type": "task", **ASKING})
            await self.broadcast({"type": "state", "state": "in attesa"})
        elif scene == "riposo":
            await self.broadcast({"type": "rest", "active": True, "listening": True})
            await self.broadcast({"type": "state", "state": "riposo"})
        else:
            await self.broadcast({"type": "state", "state": "in attesa"})

    async def say(self, text: str) -> None:
        await self.broadcast({"type": "state", "state": "elaborazione"})
        await asyncio.sleep(0.9)
        await self.broadcast({"type": "state", "state": "parla"})
        for word in text.split(" "):
            await self.broadcast({"type": "jarvis_delta", "text": word + " "})
            await asyncio.sleep(0.06)
        await asyncio.sleep(len(text) / 17 + 0.5)
        await self.broadcast({"type": "jarvis_done", "text": text})
        await self.broadcast({"type": "awake", "seconds": 30})
        await self.broadcast({"type": "state", "state": "in attesa"})

    async def demo(self) -> None:
        """Una sola volta, poi ferma: ripetuta all'infinito sembrava qualcuno che fa domande vere."""
        await asyncio.sleep(3)
        await self.broadcast({"type": "state", "state": "ascolto"})
        for i in range(8, len(QUESTION) + 1, 6):
            await self.broadcast({"type": "partial", "text": QUESTION[:i]})
            await asyncio.sleep(0.25)
        await self.broadcast({"type": "user", "text": QUESTION})
        await self.say(ANSWER)

    # ------------------------------------------------------------ server
    async def index(self, _req: web.Request) -> web.Response:
        return index_response(self.web_dir)

    async def received(self, _req: web.Request) -> web.Response:
        """I comandi arrivati dalla pagina (per i test)."""
        return web.json_response(self.commands)

    async def ws(self, req: web.Request) -> web.WebSocketResponse:
        sock = web.WebSocketResponse(heartbeat=20)
        await sock.prepare(req)
        self.clients.add(sock)
        await sock.send_str(json.dumps(self.snapshot(), ensure_ascii=False))
        if self.scene == "demo":
            self.spawn(self.demo())
        else:
            self.spawn(self.hold(self.scene))
        async for msg in sock:
            if msg.type == WSMsgType.TEXT:
                await self.command(json.loads(msg.data))
        self.clients.discard(sock)
        return sock

    async def command(self, cmd: dict) -> None:
        self.commands.append(cmd)
        kind = cmd.get("cmd")
        if kind == "vetrina":
            if cmd.get("tema"):
                self.theme = cmd["tema"]
                await self.broadcast(self.config())
            if cmd.get("scena"):
                self.scene = cmd["scena"]
                await self.hold(cmd["scena"])
            if "utente" in cmd:  # una frase detta da qualcuno (es. un ospite), come la manda il Jarvis vero
                await self.broadcast({"type": "user", **cmd["utente"]})
            if "audio" in cmd:  # come il Jarvis vero quando le cuffie spariscono o tornano
                ok = bool(cmd["audio"])
                self.audio = {"type": "audio", "ok": ok, "reason": "audio tornato" if ok else "il dispositivo ha smesso di rispondere",
                              "input": "Microphone (vetrina)" if ok else None, "output": "Headphones (vetrina)" if ok else None,
                              "error": None if ok else "Error starting stream: Unanticipated host error"}
                await self.broadcast(self.audio)
        elif kind == "passphrase":  # come il Jarvis vero: salvata, e il testo non torna indietro
            await self.broadcast({"type": "notice", "kind": "passphrase", "info": {"ok": True, "message": "ok"}})
        elif kind == "text":
            await self.broadcast({"type": "user", "text": cmd.get("text", "")})
            self.spawn(self.say("Certamente, signore. Questa è una risposta di prova della vetrina."))
        elif kind == "rest":
            resting = self.state != "riposo"
            await self.broadcast({"type": "rest", "active": resting, "listening": True})
            await self.broadcast({"type": "state", "state": "riposo" if resting else "in attesa"})
        elif kind == "confirm":
            await self.broadcast({"type": "task", **{**ASKING, "question": None, "status": "in corso"}})
        elif kind == "ptt":
            await self.broadcast({"type": "ptt", "armed": True})
            await self.broadcast({"type": "state", "state": "ascolto"})
        elif kind == "stop":
            await self.broadcast({"type": "state", "state": "in attesa"})


async def serve(port: int, web_dir: Path = WEB, scene: str = "attesa", theme: str = "mark3") -> tuple[Vetrina, web.AppRunner]:
    v = Vetrina(web_dir, scene, theme)
    app = web.Application(middlewares=[no_cache])
    app.router.add_get("/", v.index)
    app.router.add_get("/ws", v.ws)
    app.router.add_get("/comandi", v.received)
    app.router.add_static("/static", web_dir)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    v.spawn(v.levels())
    return v, runner


async def main(port: int, web_dir: Path, scene: str, theme: str) -> None:
    await serve(port, web_dir, scene, theme)
    print(f"vetrina su http://127.0.0.1:{port} (tema {theme}, scena {scene})", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8793)
    ap.add_argument("--web", type=Path, default=WEB)
    ap.add_argument("--scena", default="attesa", choices=SCENES)
    ap.add_argument("--tema", default="mark3", choices=["mark3", "cinema"])
    a = ap.parse_args()
    asyncio.run(main(a.porta, a.web, a.scena, a.tema))

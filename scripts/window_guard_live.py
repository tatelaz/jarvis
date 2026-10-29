"""Prova dal vivo: la finestra vera (pywebview + WebView2) passa il controllo di Origin e Host del server.

Avvia il server dell'interfaccia (col controllo) su una porta libera con un Jarvis finto, apre la finestra vera e
controlla che si colleghi e che nessun collegamento sia stato rifiutato. Il mouse non si tocca.
Uso: python scripts/window_guard_live.py   (OK o NON VA; codice di uscita 0 solo se va bene)
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import webview

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.config import Config
from jarvis.core.bus import Bus
from jarvis.ui.server import UiServer

result: dict = {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int, ready: threading.Event) -> None:
    async def main() -> None:
        app = SimpleNamespace(bus=Bus(), cfg=Config(), tasks=None, conv=None)
        srv = UiServer(app, port=port)
        await srv.start()
        stop = asyncio.Event()
        result.update(server=srv, loop=asyncio.get_running_loop(), stop=stop)
        ready.set()
        await stop.wait()
        await srv.stop()

    asyncio.run(main())


def check(win: webview.Window) -> None:
    try:
        for _ in range(40):
            time.sleep(0.25)
            if win.evaluate_js("window.JarvisCore && JarvisCore.J.connected") is True:
                result["collegata"] = True
                break
    except Exception as e:  # la prova riporta, non nasconde
        result["errore"] = repr(e)
    finally:
        win.destroy()


def main() -> int:
    port = free_port()
    ready = threading.Event()
    t = threading.Thread(target=serve, args=(port, ready), daemon=True)
    t.start()
    ready.wait(10)
    win = webview.create_window("Jarvis (prova)", f"http://127.0.0.1:{port}/", width=900, height=600,
                                frameless=True, easy_drag=False, background_color="#000000")
    webview.start(check, win, private_mode=True)
    result["loop"].call_soon_threadsafe(result["stop"].set)
    t.join(5)
    refused = result["server"].refused if "server" in result else None
    print({"collegata": result.get("collegata", False), "rifiutati": refused, "errore": result.get("errore")})
    ok = result.get("collegata") and refused == 0
    print("OK" if ok else "NON VA")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

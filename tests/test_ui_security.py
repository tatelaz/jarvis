"""Chi può parlare con Jarvis dalla finestra (revisione del 27/10): solo la finestra stessa e i programmi locali.

Il canale /ws accetta testo che vale come Andrea, conferme di azioni delicate e la parola d'ordine: una pagina web
aperta nel browser non deve potersi collegare, né direttamente né facendosi passare per 127.0.0.1.
"""

import socket
from types import SimpleNamespace

import aiohttp
import pytest

from jarvis.config import Config
from jarvis.core.bus import Bus
from jarvis.ui.server import UiServer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server():
    received = []
    conv = SimpleNamespace(submit_text=lambda text: received.append(text))

    async def submit(text):
        received.append(text)

    conv.submit_text = submit
    app = SimpleNamespace(bus=Bus(), cfg=Config(), tasks=None, conv=conv)
    srv = UiServer(app, port=free_port())
    await srv.start()
    yield srv, received
    await srv.stop()


async def test_a_web_page_cannot_open_the_command_channel(server):
    srv, received = server
    url = f"http://127.0.0.1:{srv.port}/ws"
    async with aiohttp.ClientSession() as s:
        for origin in ("https://sito-cattivo.example", "null", f"http://127.0.0.1:{srv.port + 1}"):
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(url, headers={"Origin": origin})
            assert e.value.status == 403
    assert srv.refused == 3 and received == []


async def test_the_window_and_local_programs_can_talk(server):
    srv, received = server
    url = f"http://127.0.0.1:{srv.port}/ws"
    async with aiohttp.ClientSession() as s:
        for headers in ({"Origin": f"http://127.0.0.1:{srv.port}"}, {}):  # la finestra; un programma locale
            async with s.ws_connect(url, headers=headers) as ws:
                assert (await ws.receive_json())["type"] == "snapshot"
                await ws.send_json({"cmd": "text", "text": "ciao"})
                for _ in range(50):
                    if received:
                        break
                    await __import__("asyncio").sleep(0.02)
            received_now = list(received)
            received.clear()
            assert received_now == ["ciao"]


async def test_dns_rebinding_is_refused(server):
    """Un sito che risolve il suo nome in 127.0.0.1: il browser manda il suo nome come Host."""
    srv, _ = server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{srv.port}/", headers={"Host": f"sito-cattivo.example:{srv.port}"}) as r:
            assert r.status == 403
        async with s.get(f"http://127.0.0.1:{srv.port}/") as r:
            assert r.status == 200

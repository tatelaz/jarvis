"""Cervello locale contro un finto server OpenAI-compatibile: streaming, strumenti, interruzione."""

import asyncio
import json
import time

import pytest
from aiohttp import web

from jarvis.brain.base import Done, TextDelta, ToolCall, ToolResult
from jarvis.brain.local import LocalBrain
from jarvis.brain.tools import ToolRegistry, schema

REQUESTS = web.AppKey("requests", list)


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


async def fake_completions(request):
    body = await request.json()
    request.app[REQUESTS].append(body)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    last = body["messages"][-1]
    if last["role"] == "user" and "ore" in last["content"]:
        # chiamata a strumento spezzata in due pezzi, come fanno i server veri
        await resp.write(sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "ora_attuale", "arguments": "{\"fus"}}]}}]}))
        await resp.write(sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "o\": \"it\"}"}}]}, "finish_reason": "tool_calls"}]}))
    elif last["role"] == "user" and "lungo" in last["content"]:
        for i in range(200):
            await resp.write(sse({"choices": [{"delta": {"content": f"parola{i} "}}]}))
            await asyncio.sleep(0.02)
    else:
        for piece in ["Sono ", "le dieci", "."]:
            await resp.write(sse({"choices": [{"delta": {"content": piece}}]}))
    await resp.write(b"data: [DONE]\n\n")
    return resp


@pytest.fixture
async def server():
    app = web.Application()
    app[REQUESTS] = []

    async def models(_request):
        return web.json_response({"data": [{"id": "fake"}]})

    app.router.add_post("/v1/chat/completions", fake_completions)
    app.router.add_get("/v1/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield app, f"http://127.0.0.1:{port}/v1"
    await runner.cleanup()


def registry():
    reg = ToolRegistry()

    @reg.register("ora_attuale", "Ora corrente.", schema({"fuso": {"type": "string"}}))
    def ora(args):
        return "10:00 " + args.get("fuso", "")

    return reg


async def collect(brain, msg):
    return [ev async for ev in brain.respond(msg)]


async def test_tool_loop_and_text(server):
    app, url = server
    brain = LocalBrain("sistema", registry(), url, "fake")
    events = await collect(brain, "che ore sono?")
    await brain.stop()
    assert [type(e) for e in events if not isinstance(e, TextDelta)] == [ToolCall, ToolResult, Done]
    call = next(e for e in events if isinstance(e, ToolCall))
    assert call.name == "ora_attuale" and call.args == {"fuso": "it"}
    assert events[-1].text == "Sono le dieci."
    # il secondo giro ha rimandato al modello la richiesta e il risultato dello strumento
    second = app[REQUESTS][1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "ora_attuale"
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": "10:00 it"}
    # il ragionamento è spento per llama.cpp
    assert app[REQUESTS][0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_interrupt_stops_quickly(server):
    _app, url = server
    brain = LocalBrain("sistema", registry(), url, "fake")
    got = []

    async def run():
        async for ev in brain.respond("dimmi un testo lungo"):
            got.append(ev)
            if isinstance(ev, TextDelta) and len(got) == 3:
                await brain.interrupt()

    t = time.perf_counter()
    await asyncio.wait_for(run(), timeout=5)
    await brain.stop()
    assert time.perf_counter() - t < 1.5  # 200 pezzi × 20 ms = 4 s se non si fermasse
    assert isinstance(got[-1], Done) and got[-1].interrupted
    assert brain.history[-1]["role"] == "assistant"  # il testo parziale resta in cronologia


async def test_history_trimming_keeps_tool_pairs():
    brain = LocalBrain("s", registry(), "http://x/v1", "m", history_chars=400)
    for i in range(30):
        brain.history.append({"role": "user", "content": f"domanda {i} " + "x" * 20})
        brain.history.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]})
        brain.history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r" * 30})
    kept = brain._trimmed_history()
    assert sum(len(json.dumps(m)) for m in kept) <= 400 + 200
    assert kept[0]["role"] != "tool"
    assert kept[-1]["content"] == "r" * 30


async def test_unreachable_server_raises_brain_unavailable():
    from jarvis.brain.base import BrainUnavailable

    brain = LocalBrain("s", registry(), "http://127.0.0.1:9/v1", "m")
    with pytest.raises(BrainUnavailable):
        await brain.start()
    await brain.stop()


async def test_tool_errors_become_text_for_the_model():
    reg = ToolRegistry()

    @reg.register("rompi", "Fallisce sempre.")
    def rompi(args):
        raise RuntimeError("disco pieno")

    out = await reg.call("rompi", {})
    assert out.startswith("Errore") and "disco pieno" in out
    assert (await reg.call("inesistente", {})).startswith("Errore: strumento sconosciuto")
    with pytest.raises(ValueError):
        reg.add(reg.get("rompi"))

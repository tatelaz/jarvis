"""OpenCode come ripiego gratuito: ponte MCP, server isolato, cervello a voce, modelli gratis ed esame.

Tutto contro un finto `opencode serve` (tests/fixtures/fake_opencode.py) costruito sulla specifica OpenAPI vera e
sugli eventi registrati dal vivo il 28/10: nessuna rete, nessun modello vero, nessuna chiave."""

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
import pytest

from jarvis.brain.base import BrainUnavailable, Done, Notice, TextDelta, ToolCall, ToolResult
from jarvis.brain.mcp_bridge import McpBridge
from jarvis.brain.opencode import (
    BUILTIN_PERMISSIONS,
    EXAM_AGENT,
    FRONT_AGENT,
    TEXT_AGENT,
    WORKER_AGENT,
    ApiKeyStore,
    ModelProblem,
    OpenCodeBrain,
    OpenCodeClient,
    OpenCodeHub,
    TurnState,
    build_config,
    classify_error,
    free_limit_until,
    next_utc_midnight,
    server_env,
)
from jarvis.brain.router import BrainRouter
from jarvis.brain.tools import ToolRegistry, schema
from jarvis.brain.zen import (
    PRIVACY,
    Examiner,
    ExamResult,
    ModelPicker,
    free_models,
    italian_ok,
    voice_variant,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from fake_opencode import CATALOG, FakeOpenCode

FAKE = Path(__file__).parent / "fixtures" / "fake_opencode.py"
GLM = "glm-5.3-flash-free"


def registry(calls=None):
    reg = ToolRegistry()

    @reg.register("somma", "Somma due numeri.", schema({"a": {"type": "integer"}, "b": {"type": "integer"}},
                                                         ["a", "b"]))
    def somma(args):
        if calls is not None:
            calls.append(args)
        return str(int(args["a"]) + int(args["b"]))

    @reg.register("rompi", "Fallisce sempre.")
    def rompi(_args):
        return "Errore: disco pieno"

    return reg


class StubHub:
    """Al posto di OpenCodeHub: il client del finto server già acceso."""

    def __init__(self, client, picker=None, directory="C:/jarvis/voce"):
        self.client, self.picker, self.directory = client, picker, directory
        self.ensured = 0

    async def ensure(self):
        self.ensured += 1
        return self.client

    @contextlib.asynccontextmanager
    async def using(self):
        yield await self.ensure()


@pytest.fixture
async def oc():
    fake = FakeOpenCode()
    url = await fake.start()
    client = OpenCodeClient(url, fake.password)
    await client.start()
    yield fake, client
    await client.close()
    await fake.stop()


async def collect(brain, text="ciao"):
    return [ev async for ev in brain.respond(text)]


def texts(evs):
    return "".join(e.text for e in evs if isinstance(e, TextDelta))


# ---------------------------------------------------------------------- ponte MCP


async def test_bridge_speaks_mcp_with_the_official_client():
    """Il ponte parla il protocollo vero: lo prova il client MCP ufficiale (pacchetto mcp)."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    calls = []
    bridge = McpBridge()
    bridge.mount("prova", registry(calls))
    await bridge.start()
    try:
        async with (httpx2.AsyncClient(headers={"Authorization": f"Bearer {bridge.token}"}) as http,
                    streamable_http_client(bridge.url("prova"), http_client=http) as streams):
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert sorted(t.name for t in listed.tools) == ["rompi", "somma"]
                res = await session.call_tool("somma", {"a": 2, "b": 40})
                assert res.content[0].text == "42" and not res.is_error
                bad = await session.call_tool("rompi", {})
                assert bad.is_error
        assert calls == [{"a": 2, "b": 40}]
    finally:
        await bridge.stop()


async def test_bridge_refuses_strangers():
    """Solo OpenCode col gettone: niente gettone, gettone sbagliato, un'altra pagina web o un altro Host = rifiuto."""
    bridge = McpBridge()
    bridge.mount("prova", registry())
    await bridge.start()
    url = bridge.url("prova")
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    good = {"Authorization": f"Bearer {bridge.token}"}
    try:
        async with aiohttp.ClientSession() as s:
            for headers, status in (({}, 401), ({"Authorization": "Bearer sbagliato"}, 401),
                                    ({**good, "Origin": "https://sito.example"}, 403),
                                    ({**good, "Host": f"evil.example:{bridge.port}"}, 403)):
                async with s.post(url, json=req, headers=headers) as r:
                    assert r.status == status, headers
            async with s.post(url, json=req, headers=good) as r:
                assert r.status == 200 and len((await r.json())["result"]["tools"]) == 2
            async with s.get(url, headers=good) as r:  # niente flusso aperto dal server
                assert r.status == 405
            async with s.post(url.replace("prova", "altro"), json=req, headers=good) as r:
                assert r.status == 404
            # notifiche: nessuna risposta; metodo sconosciuto: errore JSON-RPC; JSON rotto: errore di parsing
            async with s.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=good) as r:
                assert r.status == 202
            async with s.post(url, json={"jsonrpc": "2.0", "id": 7, "method": "resources/list"}, headers=good) as r:
                assert (await r.json())["error"]["code"] == -32601
            async with s.post(url, data=b"{rotto", headers={**good, "Content-Type": "application/json"}) as r:
                assert r.status == 400
            batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                     {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}}]
            async with s.post(url, json=batch, headers=good) as r:
                out = await r.json()
                assert out[0]["result"] == {} and out[1]["result"]["protocolVersion"] == "2025-06-18"
    finally:
        await bridge.stop()


async def test_bridge_interceptor_runs_before_the_tool():
    seen = []

    async def intercept(tool, args):
        seen.append(tool)
        return "intercettato" if tool == "somma" else None

    bridge = McpBridge()
    bridge.mount("prova", registry(), intercept)
    await bridge.start()
    try:
        conf = bridge.opencode_config()["prova"]
        assert conf["type"] == "remote" and conf["headers"]["Authorization"] == f"Bearer {bridge.token}"
        async with aiohttp.ClientSession() as s:
            for tool, want in (("somma", "intercettato"), ("rompi", "Errore: disco pieno")):
                body = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": {}}}
                async with s.post(conf["url"], json=body, headers=conf["headers"]) as r:
                    assert (await r.json())["result"]["content"][0]["text"] == want
        assert seen == ["somma", "rompi"]
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------- dagli eventi alle parole


def test_turn_state_never_speaks_the_question_or_the_reasoning():
    st = TurnState("jarvis_")
    out = []
    # la domanda dell'utente arriva come parte di testo, prima e dopo che si sappia di chi è il messaggio
    out += st.feed({"type": "message.part.updated", "properties": {"part": {"id": "p0", "messageID": "u1",
                                                                            "type": "text", "text": "Che ore sono?"}}})
    out += st.feed({"type": "message.updated", "properties": {"info": {"id": "u1", "role": "user"}}})
    out += st.feed({"type": "message.updated", "properties": {"info": {"id": "a1", "role": "assistant"}}})
    out += st.feed({"type": "message.part.updated", "properties": {"part": {"id": "r1", "messageID": "a1",
                                                                            "type": "reasoning", "text": ""}}})
    out += st.feed({"type": "message.part.delta", "properties": {"messageID": "a1", "partID": "r1", "field": "text",
                                                                 "delta": "penso in inglese"}})
    out += st.feed({"type": "message.part.updated", "properties": {"part": {"id": "s1", "messageID": "a1",
                                                                            "type": "text", "text": "x",
                                                                            "synthetic": True}}})
    assert out == [] and st.text == "" and not st.produced


def test_turn_state_holds_early_pieces_and_never_repeats():
    st = TurnState("jarvis_")
    ev = []
    # pezzi arrivati prima che si sappia di chi è il messaggio e prima che la parte si presenti
    ev += st.feed({"type": "message.part.delta", "properties": {"messageID": "a1", "partID": "t1", "field": "text",
                                                                "delta": "Sono "}})
    ev += st.feed({"type": "message.updated", "properties": {"info": {"id": "a1", "role": "assistant"}}})
    ev += st.feed({"type": "message.part.delta", "properties": {"messageID": "a1", "partID": "t1", "field": "text",
                                                                "delta": "le "}})
    assert ev == []  # testo o ragionamento? lo dice la parte
    ev += st.feed({"type": "message.part.updated", "properties": {"part": {"id": "t1", "messageID": "a1",
                                                                           "type": "text", "text": ""}}})
    ev += st.feed({"type": "message.part.delta", "properties": {"messageID": "a1", "partID": "t1", "field": "text",
                                                                "delta": "dieci"}})
    ev += st.feed({"type": "message.part.updated", "properties": {"part": {"id": "t1", "messageID": "a1",
                                                                           "type": "text", "text": "Sono le dieci."}}})
    assert [e.text for e in ev] == ["Sono ", "le ", "dieci", "."] and st.text == "Sono le dieci."
    # la risposta finale del server non ripete niente
    assert st.finish({"info": {"id": "a1", "role": "assistant"},
                      "parts": [{"id": "t1", "type": "text", "text": "Sono le dieci."}]}) == []


async def test_text_arrives_in_pieces_with_persona_and_voice_variant(oc, tmp_path):
    fake, client = oc

    async def behave(turn, text):
        await turn.say("Buonasera, signore. Tutto pronto.", pieces=4)

    fake.behave = behave
    picker = ModelPicker(tmp_path / "esame.json")
    picker.set_catalog(CATALOG)
    brain = OpenCodeBrain("Sei Jarvis.", StubHub(client, picker), lambda: [GLM])
    evs = await collect(brain, "Jarvis, ci sei?")
    pieces = [e.text for e in evs if isinstance(e, TextDelta)]
    assert len(pieces) == 4 and "".join(pieces) == "Buonasera, signore. Tutto pronto."
    assert "ci sei" not in texts(evs)  # la domanda non torna indietro come risposta
    done = evs[-1]
    assert isinstance(done, Done) and done.text == "Buonasera, signore. Tutto pronto." and not done.interrupted
    assert done.model == f"opencode/{GLM}"
    body = fake.prompts[0]["body"]
    assert body["agent"] == FRONT_AGENT and body["system"] == "Sei Jarvis."
    assert body["model"] == {"providerID": "opencode", "modelID": GLM} and body["variant"] == "low"
    assert fake.prompts[0]["directory"] == "C:/jarvis/voce"
    # la stessa sessione al turno dopo (il filo della conversazione)
    await collect(brain, "e adesso?")
    assert fake.prompts[1]["sid"] == fake.prompts[0]["sid"] and len(fake.sessions) == 1


async def test_lost_pieces_are_recovered_from_the_final_answer(oc):
    fake, client = oc

    async def behave(turn, text):  # niente eventi delle parti: solo la risposta finale
        turn.parts.append({"id": "prt_x", "messageID": turn.asst_id, "type": "text", "text": "Fatto, signore."})

    fake.behave = behave
    evs = await collect(OpenCodeBrain("p", StubHub(client), lambda: [GLM]))
    assert texts(evs) == "Fatto, signore." and evs[-1].text == "Fatto, signore."


async def test_tools_become_brain_events_and_permissions_only_for_own_tools(oc):
    fake, client = oc

    async def behave(turn, text):
        await turn.say("Apro. ")
        await turn.tool("jarvis_apri", {"cosa": "spotify"}, output="aperto Spotify", ask="jarvis_apri")
        await turn.tool("jarvis_volume", {"azione": "alza"}, error="Errore: niente audio")
        await turn.tool("bash", {"command": "del /s C:\\"}, output="mai", ask="bash")
        await turn.say("Fatto.")

    fake.behave = behave
    evs = await collect(OpenCodeBrain("p", StubHub(client), lambda: [GLM]))
    kinds = [(type(e).__name__, getattr(e, "name", None)) for e in evs if not isinstance(e, TextDelta | Done)]
    assert kinds[:5] == [("ToolStart", "apri"), ("ToolCall", "apri"), ("ToolResult", "apri"),
                         ("ToolStart", "volume"), ("ToolCall", "volume")]
    call = next(e for e in evs if isinstance(e, ToolCall))
    assert call.args == {"cosa": "spotify"}
    results = [e for e in evs if isinstance(e, ToolResult)]
    assert results[0].ok and results[0].preview == "aperto Spotify" and not results[1].ok
    replies = {a["permission"]: fake.replies[a["id"]] for a in fake.asked}
    assert replies["jarvis_apri"] == {"reply": "once"}
    assert replies["bash"]["reply"] == "reject"  # a voce niente shell, anche se un modello ci prova
    assert not results[2].ok  # e lo strumento rifiutato risulta fallito
    assert texts(evs) == "Apro. Fatto."


async def test_free_limit_pauses_the_whole_provider_until_reset(oc):
    """Visto dal vivo il 28/10: nessun errore, solo lo stato "retry" con free_tier_limit fino a mezzanotte UTC."""
    fake, client = oc
    reset_ms = 1793232000881

    async def behave(turn, text):
        await turn.status("retry", attempt=1, message="Free usage exceeded, subscribe to Go", next=reset_ms,
                          action={"reason": "free_tier_limit", "provider": "opencode", "title": "Free limit reached",
                                  "message": "Subscribe to OpenCode Go", "label": "subscribe",
                                  "link": "https://opencode.ai/go"})
        await turn.wait_abort()

    fake.behave = behave
    now = {"t": reset_ms / 1000 - 1800}  # mezz'ora prima del ripristino annunciato
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM, "big-pickle"], clock=lambda: now["t"])
    t = time.perf_counter()
    with pytest.raises(BrainUnavailable) as ei:
        await collect(brain)
    assert time.perf_counter() - t < 5  # non aspetta la mezzanotte
    assert ei.value.scope == "provider" and ei.value.retry_at == reset_ms / 1000
    assert len(fake.prompts) == 1  # inutile provare l'altro modello: il limite è di tutti
    assert fake.prompts[0]["sid"] in fake.aborted
    # il 28/10 il limite "fino a mezzanotte" era già passato dopo meno di 4 ore: si riprova al più dopo un'ora
    now["t"] = reset_ms / 1000 - 8 * 3600
    with pytest.raises(BrainUnavailable) as ei:
        await collect(brain)
    assert ei.value.retry_at == now["t"] + 3600


class Fixed:
    def __init__(self, name, provider, error=None, reply="ok"):
        self.name, self.provider, self.error, self.reply = name, provider, error, reply
        self.asked = 0

    async def respond(self, message):
        self.asked += 1
        if self.error:
            raise self.error
        yield TextDelta(self.reply)
        yield Done(self.reply)

    async def interrupt(self):
        pass


async def test_router_order_claude_then_opencode_then_ollama(oc):
    """Claude esaurito -> OpenCode; anche OpenCode esaurito -> Ollama cloud, e OpenCode resta in pausa fino al
    ripristino (non lo si riprova a ogni frase)."""
    fake, client = oc
    clock = {"t": 1_800_000_000.0}

    async def answer(turn, text):
        await turn.say("Dal modello gratis.")

    fake.behave = answer
    claude = Fixed("claude", "claude", BrainUnavailable("session limit", retry_at=clock["t"] + 3600, scope="provider"))
    cloud = Fixed("gpt-oss", "ollama-cloud", reply="dal cloud")
    opencode = OpenCodeBrain("p", StubHub(client), lambda: [GLM], clock=lambda: clock["t"])
    r = BrainRouter([claude, opencode, cloud], clock=lambda: clock["t"])
    evs = [ev async for ev in r.respond("ciao")]
    switch = next(e for e in evs if isinstance(e, Notice))
    assert switch.info["to_provider"] == "opencode" and texts(evs) == "Dal modello gratis." and cloud.asked == 0
    assert r.provider_paused_until("claude") == clock["t"] + 3600 and r.provider_paused_until("opencode") is None

    async def limit(turn, text):
        await turn.status("retry", attempt=1, message="Free usage exceeded", next=int((clock["t"] + 7200) * 1000),
                          action={"reason": "free_tier_limit"})
        await turn.wait_abort()

    fake.behave = limit
    evs = [ev async for ev in r.respond("ancora")]
    assert texts(evs) == "dal cloud" and r.provider_paused_until("opencode") == clock["t"] + 3600  # al più un'ora
    prompts = len(fake.prompts)
    evs = [ev async for ev in r.respond("e ancora")]
    assert texts(evs) == "dal cloud" and len(fake.prompts) == prompts  # OpenCode non disturbato


async def test_a_vanished_or_busy_model_hands_over_to_the_next(oc):
    fake, client = oc
    problems = []

    async def behave(turn, text):
        if turn.model == GLM:
            turn.error = {"name": "APIError", "data": {"message": "Model not found: glm-5.3-flash-free",
                                                       "statusCode": 404, "isRetryable": False}}
        elif turn.model == "nemotron-3.5-lightning-free":
            await turn.status("retry", attempt=1, message="overloaded", next=0)
            await turn.wait_abort()
        else:
            await turn.say("Eccomi.")

    fake.behave = behave
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM, "nemotron-3.5-lightning-free", "big-pickle"],
                          on_model_problem=lambda m, p: problems.append((m, p.gone)))
    evs = await collect(brain)
    assert texts(evs) == "Eccomi." and evs[-1].model == "opencode/big-pickle"
    assert problems == [(GLM, True), ("nemotron-3.5-lightning-free", False)]
    assert [p["body"]["model"]["modelID"] for p in fake.prompts] == [GLM, "nemotron-3.5-lightning-free", "big-pickle"]


async def test_a_model_too_slow_for_the_voice_hands_over_to_the_next(oc):
    """28/10 dal vivo: un modello promosso una volta, la volta dopo è rimasto zitto. A voce non si aspetta."""
    fake, client = oc
    problems = []

    async def behave(turn, text):
        if turn.model == GLM:
            await turn.wait_abort(timeout=30)
        else:
            await turn.say("Eccomi, signore.")

    fake.behave = behave
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM, "big-pickle"], first_word_s=0.3,
                          on_model_problem=lambda m, p: problems.append((m, p.gone, str(p))))
    t = time.perf_counter()
    evs = await collect(brain)
    assert time.perf_counter() - t < 5 and texts(evs) == "Eccomi, signore."
    assert problems == [(GLM, False, "nessuna parola dopo 0 s")] and fake.abort_calls == [fake.prompts[0]["sid"]]


def test_a_hiccup_does_not_undo_a_promotion(tmp_path):
    p, _ = picker_with(tmp_path)
    p.record([ExamResult(GLM, True, True, 1.1)])
    p.record([ExamResult(GLM, False, False, None, "nessuna parola", unavailable=True)])
    assert p.results[GLM].passed and p.best() == GLM  # un intoppo non è una bocciatura
    p.record([ExamResult(GLM, False, True, 0.9, "non è italiano")])
    assert not p.results[GLM].passed  # una bocciatura nel merito invece vale
    p.record([ExamResult("big-pickle", False, False, None, "lento", unavailable=True)])
    assert p.results["big-pickle"].unavailable  # mai promosso prima: si annota


async def test_no_free_model_left_is_brain_unavailable(oc):
    fake, client = oc

    async def behave(turn, text):
        turn.error = {"name": "APIError", "data": {"message": "not found", "statusCode": 404, "isRetryable": False}}

    fake.behave = behave
    with pytest.raises(BrainUnavailable, match="nessun modello gratis"):
        await collect(OpenCodeBrain("p", StubHub(client), lambda: [GLM, "big-pickle"]))
    with pytest.raises(BrainUnavailable, match="nessuno"):
        await collect(OpenCodeBrain("p", StubHub(client), lambda: []))


async def test_interrupt_stops_quickly_and_is_marked(oc):
    fake, client = oc

    async def behave(turn, text):
        await turn.say(" ".join(f"parola{i}" for i in range(400)), pieces=200, delay=0.02)

    fake.behave = behave
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM])
    got = []
    t = time.perf_counter()
    async for ev in brain.respond("raccontami una storia lunga"):
        got.append(ev)
        if sum(isinstance(e, TextDelta) for e in got) == 3:
            await brain.interrupt()
    assert time.perf_counter() - t < 2.5  # 200 pezzi x 20 ms = 4 s se non si fermasse
    assert isinstance(got[-1], Done) and got[-1].interrupted and fake.prompts[0]["sid"] in fake.aborted
    assert sum(isinstance(e, TextDelta) for e in got) == 3  # dopo l'interruzione non esce più una parola


async def test_words_in_flight_after_an_interrupt_are_not_spoken(oc):
    """Dopo lo stop arrivano ancora pezzi già partiti dal server: non si dicono."""
    fake, client = oc

    async def behave(turn, text):
        await turn.say("Uno. Due. Tre. Quattro. Cinque.", pieces=5, delay=0.05, in_flight=True)

    fake.behave = behave
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM])
    got = []
    async for ev in brain.respond("conta"):
        got.append(ev)
        if isinstance(ev, TextDelta):
            await brain.interrupt()
    assert [e.text for e in got if isinstance(e, TextDelta)] == ["Uno. Du"]  # il primo pezzo, poi niente
    assert got[-1].interrupted


async def test_silence_from_opencode_becomes_unavailable(oc):
    fake, client = oc

    async def behave(turn, text):
        await turn.wait_abort(timeout=30)

    fake.behave = behave
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM], stall_s=0.3)
    with pytest.raises(BrainUnavailable, match="non risponde"):
        await collect(brain)
    assert fake.prompts[0]["sid"] in fake.aborted


async def test_lost_session_is_recreated(oc):
    fake, client = oc
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM])
    await collect(brain)
    first = brain.session_id
    fake.sessions.clear()  # archivio di OpenCode svuotato: si apre un'altra sessione e la domanda si ripete
    evs = await collect(brain)
    assert texts(evs) == "Ricevuto." and brain.session_id not in (None, first)
    assert [p["sid"] for p in fake.prompts][-1] == brain.session_id

    fake.sessions.clear()
    fake.forget_sessions = True  # sparisce anche la nuova: si riprova una volta sola, poi si dice che non va
    with pytest.raises(BrainUnavailable, match="404"):
        await collect(brain)
    assert brain.session_id is None


async def test_disabled_brain_is_skipped_not_paused(oc):
    fake, client = oc
    on = {"v": False}
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM], enabled=lambda: on["v"])
    cloud = Fixed("gpt-oss", "ollama-cloud", reply="dal cloud")
    r = BrainRouter([brain, cloud])
    evs = [ev async for ev in r.respond("ciao")]
    assert texts(evs) == "dal cloud" and id(brain) not in r.paused_until and fake.prompts == []
    with pytest.raises(BrainUnavailable, match="spento"):
        await collect(brain)
    with pytest.raises(BrainUnavailable, match="spento"):
        await brain.start()
    on["v"] = True  # "Jarvis, riaccendi OpenCode": vale dal turno dopo
    evs = [ev async for ev in r.respond("ciao")]
    assert texts(evs) == "Ricevuto."


async def test_ephemeral_sessions_are_deleted(oc):
    fake, client = oc
    brain = OpenCodeBrain("p", StubHub(client), lambda: [GLM], agent=TEXT_AGENT, ephemeral=True)
    await collect(brain)
    sid = brain.session_id
    await brain.stop()
    assert fake.deleted == [sid] and fake.prompts[0]["body"]["agent"] == TEXT_AGENT


def test_classify_error():
    now = 1_800_000_000.0
    assert classify_error(None) is None
    assert classify_error({"name": "MessageAbortedError", "data": {"message": "Aborted"}}) is None
    free = classify_error({"name": "APIError", "data": {"message": "Free usage exceeded, subscribe to Go",
                                                         "statusCode": 429}}, now)
    assert isinstance(free, BrainUnavailable) and free.scope == "provider" and free.retry_at == now + 3600
    late = next_utc_midnight(now) - 600  # dieci minuti alla mezzanotte UTC: si riprova a mezzanotte
    assert classify_error({"name": "APIError", "data": {"message": "Free limit reached"}}, late).retry_at == \
        next_utc_midnight(now)
    auth = classify_error({"name": "ProviderAuthError", "data": {"providerID": "opencode", "message": "chiave"}}, now)
    assert isinstance(auth, BrainUnavailable) and auth.scope == "provider"
    gone = classify_error({"name": "APIError", "data": {"message": "x", "statusCode": 404}})
    assert isinstance(gone, ModelProblem) and gone.gone
    busy = classify_error({"name": "APIError", "data": {"message": "x", "statusCode": 429}})
    assert isinstance(busy, ModelProblem) and not busy.gone
    other = classify_error({"name": "ContextOverflowError", "data": {"message": "troppo lungo"}})
    assert isinstance(other, BrainUnavailable) and other.scope == "brain"
    assert next_utc_midnight(now) % 86400 == 0 and 0 < next_utc_midnight(now) - now <= 86400
    assert free_limit_until({"type": "retry", "next": 1793232000881, "action": {"reason": "free_tier_limit"}}) == \
        1793232000.881
    assert free_limit_until({"type": "retry", "next": 5, "action": {"reason": "rate_limit"}}) is None
    assert free_limit_until({"type": "busy"}) is None


# ---------------------------------------------------------------------- processo e configurazione


def test_server_env_is_isolated(tmp_path):
    parent = {"PATH": "C:/bin", "OPENCODE_CONFIG": "C:/andrea/opencode.json", "OPENCODE_PERMISSION": '{"*":"allow"}',
              "opencode_config_dir": "C:/x", "OPENCODE_GIT_BASH_PATH": "C:/git/bash.exe", "XDG_CONFIG_HOME": "C:/y"}
    env = server_env(tmp_path, "pw", None, parent, {"model": "opencode/x"})
    assert env["PATH"] == "C:/bin" and env["OPENCODE_GIT_BASH_PATH"] == "C:/git/bash.exe"
    for gone in ("OPENCODE_CONFIG", "OPENCODE_PERMISSION", "opencode_config_dir"):
        assert gone not in env  # niente configurazioni o permessi ereditati dall'OpenCode di Andrea
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "config") and env["XDG_DATA_HOME"] == str(tmp_path / "dati")
    assert env["OPENCODE_SERVER_PASSWORD"] == "pw" and json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {"model": "opencode/x"}
    for flag in ("OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_AUTOUPDATE"):
        assert env[flag] == "1"
    assert "OPENCODE_API_KEY" not in env  # senza chiave: solo i modelli gratis
    assert server_env(tmp_path, "pw", "sk-chiave", parent, {})["OPENCODE_API_KEY"] == "sk-chiave"


def test_config_locks_down_agents_and_providers():
    cfg = build_config(GLM, {"jarvis": {"type": "remote"}, "windows": {"type": "local"}},
                       {"browser_browser_run_code_unsafe": False})
    assert cfg["enabled_providers"] == ["opencode"]  # mai un altro fornitore (e mai a pagamento per sbaglio)
    assert cfg["model"] == cfg["small_model"] == f"opencode/{GLM}"
    assert cfg["autoupdate"] is False and cfg["share"] == "disabled"
    front, worker = cfg["agent"][FRONT_AGENT], cfg["agent"][WORKER_AGENT]
    for name in (FRONT_AGENT, EXAM_AGENT, TEXT_AGENT):
        perm = cfg["agent"][name]["permission"]
        assert all(perm[k] == "deny" for k in BUILTIN_PERMISSIONS) and perm["*"] == "ask"
        assert cfg["agent"][name]["tools"]["bash"] is False and cfg["agent"][name]["tools"]["windows_*"] is False
    assert front["tools"]["jarvis_*"] is True and cfg["agent"][TEXT_AGENT]["tools"]["jarvis_*"] is False
    assert cfg["agent"][EXAM_AGENT]["tools"]["esame_*"] is True and cfg["agent"][EXAM_AGENT]["tools"]["jarvis_*"] is False
    wp = worker["permission"]
    assert wp["*"] == "ask" and wp["bash"] == "ask" and wp["edit"] == "ask" and wp["read"] == "allow"
    assert wp["task"] == "deny" and wp["question"] == "deny"
    # ogni permesso scritto per intero: niente da ereditare da ~/.opencode
    assert set(BUILTIN_PERMISSIONS) <= set(wp) and set(BUILTIN_PERMISSIONS) <= set(cfg["permission"])
    assert worker["tools"]["jarvis_*"] is False and worker["tools"]["browser_browser_run_code_unsafe"] is False
    assert "model" not in build_config(None, {}, None)


async def test_hub_runs_a_real_isolated_process_and_stops_it(tmp_path):
    import psutil

    env_out = tmp_path / "env.json"
    environ = {**os.environ, "FAKE_OC_ENV": str(env_out), "OPENCODE_CONFIG": "C:/andrea/opencode.json",
               "FAKE_OC_CHILD": "1"}  # il finto avvia un figlio, come i server MCP locali di OpenCode
    picker = ModelPicker(tmp_path / "esame.json")
    keys = ApiKeyStore(tmp_path / "chiavi", environ={"OPENCODE_API_KEY": "sk-prova-0123456789abcdef"})
    hub = OpenCodeHub(tmp_path / "oc", tmp_path / "opencode.log", picker, keys=keys,
                      command=lambda: [sys.executable, str(FAKE)], environ=environ)
    hub.mount("jarvis", registry())
    try:
        client = await hub.ensure()
        pid = hub.process.proc.pid
        seen = json.loads(env_out.read_text(encoding="utf-8"))
        env, argv = seen["env"], seen["argv"]
        assert argv[0] == "serve" and "--pure" in argv and argv[argv.index("--hostname") + 1] == "127.0.0.1"
        assert env["XDG_CONFIG_HOME"] == str(tmp_path / "oc" / "config") and "OPENCODE_CONFIG" not in env
        assert env["OPENCODE_API_KEY"] == "sk-prova-0123456789abcdef" and len(env["OPENCODE_SERVER_PASSWORD"]) >= 24
        cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert cfg["mcp"]["jarvis"]["url"] == hub.bridge.url("jarvis")
        assert "model" not in cfg  # nessun esame ancora: nessun modello imposto
        assert [m.id for m in picker.catalog] == [GLM, "nemotron-3.5-lightning-free", "big-pickle"]
        children = psutil.Process(pid).children(recursive=True)  # il figlio (e il python vero, se c'è un lanciatore)
        assert any("time.sleep" in " ".join(c.cmdline()) for c in children)
        # senza la parola d'ordine il server non risponde a nessuno
        async with aiohttp.ClientSession() as s, s.get(f"{hub.process.url}/provider") as r:
            assert r.status == 401
        brain = OpenCodeBrain("p", hub, picker.ranking)
        evs = await collect(brain)
        assert texts(evs) == "Ricevuto."
        assert await hub.ensure() is client  # stesso server, niente riavvii inutili
        assert not await hub.stop_if_idle(idle_s=3600)
        assert await hub.stop_if_idle(idle_s=0) and not hub.running and not psutil.pid_exists(pid)
        await asyncio.sleep(0.2)
        assert not any(c.is_running() for c in children)  # nessun figlio orfano
        await hub.ensure()  # si riaccende alla richiesta dopo
        assert hub.running and texts(await collect(brain)) == "Ricevuto."
    finally:
        await hub.stop()
    assert not hub.running and hub.bridge.port is None


async def test_hub_without_opencode_or_with_a_crashing_one_is_unavailable(tmp_path):
    def missing():
        raise FileNotFoundError("OpenCode non è installato (npm i -g opencode-ai)")

    hub = OpenCodeHub(tmp_path / "oc", tmp_path / "log.txt", ModelPicker(tmp_path / "e.json"), command=missing)
    assert not hub.installed()
    with pytest.raises(BrainUnavailable, match="non è installato"):
        await hub.ensure()
    crash = OpenCodeHub(tmp_path / "oc2", tmp_path / "log2.txt", ModelPicker(tmp_path / "e2.json"),
                        command=lambda: [sys.executable, str(FAKE)], environ={**os.environ, "FAKE_OC_CRASH": "1"})
    assert crash.installed()
    with pytest.raises(BrainUnavailable, match="non parte"):
        await crash.ensure()
    assert not crash.running
    await hub.stop()
    await crash.stop()


def test_api_key_is_sealed_and_never_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    sealed = {}

    def protect(b):
        sealed["v"] = b"X" + b[::-1]
        return sealed["v"]

    def unprotect(b):
        assert b.startswith(b"X")
        return b[1:][::-1]

    folder = tmp_path / "opencode"
    folder.mkdir()
    store = ApiKeyStore(folder, environ={}, protect=protect, unprotect=unprotect)
    assert store.get() is None  # senza chiave: solo i modelli gratis
    (folder / "chiave.txt").write_text("sk-zen-abcdefghijklmnop1234\n", encoding="utf-8")
    assert store.get() == "sk-zen-abcdefghijklmnop1234"
    assert not (folder / "chiave.txt").exists() and (folder / "chiave.bin").read_bytes() == sealed["v"]
    assert b"sk-zen" not in (folder / "chiave.bin").read_bytes()
    assert store.get() == "sk-zen-abcdefghijklmnop1234"  # dal file cifrato
    env_store = ApiKeyStore(folder, environ={"OPENCODE_API_KEY": "sk-da-ambiente-0123456789"}, protect=protect,
                            unprotect=unprotect)
    assert env_store.get() == "sk-da-ambiente-0123456789"  # la variabile d'ambiente vince

    def broken(_b):
        raise OSError("DPAPI")

    assert ApiKeyStore(folder, environ={}, protect=protect, unprotect=broken).get() is None
    assert "sk-zen" not in caplog.text and "sk-da-ambiente" not in caplog.text


# ---------------------------------------------------------------------- modelli gratis


def test_free_models_from_the_catalog():
    found = free_models(CATALOG)
    assert [m.id for m in found] == [GLM, "nemotron-3.5-lightning-free", "big-pickle"]  # dal più recente
    assert found[0].variants == ["high", "low", "max"] and found[0].context == 200000
    assert free_models({**CATALOG, "connected": []}) == []  # fornitore non collegato
    assert free_models({}) == []
    assert voice_variant(["high", "low", "max"]) == "low" and voice_variant(["minimal", "low"]) == "minimal"
    assert voice_variant(["high"]) is None


def test_privacy_notes_cover_the_documented_free_models():
    # i modelli gratis elencati dalla documentazione di Zen il 28/10/2026 hanno tutti una nota
    for m in ("big-pickle", GLM, "deepseek-v4.1-flash-free", "beam-free", "flint-40b-free", "mimo-v2.6-flash-free",
              "nemotron-3-ultra-free", "nemotron-3.5-lightning-free", "nemotron-3.5-super-vl-free"):
        assert m in PRIVACY
    assert {m for m, (t, _) in PRIVACY.items() if not t} == {GLM, "deepseek-v4.1-flash-free", "beam-free"}


def picker_with(tmp_path, privacy="tutti", fixed="auto", clock=None):
    clock = clock or {"t": 1_800_000_000.0}
    p = ModelPicker(tmp_path / "esame.json", fixed=lambda: fixed, privacy=lambda: privacy, clock=lambda: clock["t"])
    p.set_catalog(CATALOG)
    return p, clock


def test_ranking_privacy_pauses_and_fixed_choice(tmp_path):
    p, clock = picker_with(tmp_path)
    assert p.ranking() == [GLM, "nemotron-3.5-lightning-free", "big-pickle"]  # mai esaminati: dal più recente
    p.record([ExamResult(GLM, True, True, 1.4), ExamResult("big-pickle", True, True, 0.8),
              ExamResult("nemotron-3.5-lightning-free", False, True, 0.3)])
    assert p.ranking() == ["big-pickle", GLM, "nemotron-3.5-lightning-free"]  # promossi per velocità, bocciati ultimi
    assert p.best() == "big-pickle" and p.variant(GLM) == "low" and p.variant("big-pickle") is None
    p.mark_problem("big-pickle", ModelProblem("sparito", gone=True))
    assert p.ranking()[0] == GLM
    clock["t"] += 3601
    assert p.ranking()[0] == "big-pickle"
    p.mark_problem("big-pickle", ModelProblem("occupato"))
    clock["t"] += 301
    assert p.ranking()[0] == "big-pickle"  # sovraccarico: solo cinque minuti
    # privacy: solo chi non addestra sui dati
    private, _ = picker_with(tmp_path / "b", privacy="senza addestramento")
    assert private.ranking() == [GLM]
    assert "esclusi" in private.describe()
    # scelta fissa di Andrea, se esiste; altrimenti la classifica
    fixed, _ = picker_with(tmp_path / "c", fixed="big-pickle")
    assert fixed.ranking()[0] == "big-pickle"
    wrong, _ = picker_with(tmp_path / "d", fixed="non-esiste")
    assert wrong.ranking()[0] == GLM
    # tutto salvato: un Jarvis riavviato ricorda catalogo ed esame
    again = ModelPicker(tmp_path / "esame.json", clock=lambda: clock["t"])
    assert again.best() == "big-pickle" and len(again.catalog) == 3


def test_exam_due(tmp_path):
    p, clock = picker_with(tmp_path)
    assert p.exam_due(72) and not p.exam_due(0)  # mai fatto; 0 = mai da solo
    p.record([ExamResult(m, True, True, 1.0) for m in (GLM, "big-pickle", "nemotron-3.5-lightning-free")])
    assert not p.exam_due(72)
    clock["t"] += 72 * 3600 + 1
    assert p.exam_due(72)
    p.record([])
    assert not p.exam_due(72)
    new = json.loads(json.dumps(CATALOG))
    new["all"][0]["models"]["nuovo-free"] = {"release_date": "2026-10-27", "capabilities": {"toolcall": True},
                                            "status": "active", "cost": {"input": 0, "output": 0}}
    p.set_catalog(new)
    assert p.exam_due(72)  # un modello gratis nuovo va esaminato subito
    empty = ModelPicker(tmp_path / "vuoto.json")
    assert empty.exam_due(72) and not empty.exam_due(0)  # mai fatto: l'elenco arriva accendendo OpenCode


def test_italian_check():
    assert italian_ok("Sono Jarvis, il suo assistente: posso aprire app e cercare per lei.")[0]
    assert not italian_ok("I am Jarvis, your assistant, and I can help you with the tasks.")[0]
    assert not italian_ok("Sono Jarvis, il suo assistente 助手 per lei.")[0]
    assert not italian_ok("Ok.")[0]
    assert not italian_ok("")[0]


async def test_exam_promotes_the_model_that_speaks_italian_and_uses_tools(oc, tmp_path):
    """L'esame vero, contro il finto: lo strumento meteo lo esegue il ponte MCP di Jarvis, come farebbe OpenCode."""
    fake, client = oc
    p, _ = picker_with(tmp_path)
    hub = StubHub(client, p)
    examiner = Examiner(hub, p)
    bridge = McpBridge()
    bridge.mount("esame", examiner.registry)
    await bridge.start()
    fake.mcp = bridge.opencode_config()

    async def behave(turn, text):
        assert turn.body["agent"] == EXAM_AGENT
        weather = "tempo" in text
        if turn.model == GLM:  # bravo: italiano, strumento con Roma, dato riportato
            if weather:
                await turn.tool("esame_meteo", {"citta": "Roma"}, call_mcp=True)
                await turn.say("A Roma ci sono 18 gradi e il cielo è sereno.")
            else:
                await turn.say("Sono Jarvis, il suo assistente: posso aiutarla con il PC e con le sue giornate.")
        elif turn.model == "big-pickle":  # parla inglese
            await turn.say("I am Jarvis, and I can help you with your day." if not weather else
                           "It is 18 degrees in Rome, with clear sky.")
        else:  # inventa il meteo senza lo strumento
            await turn.say("Sono Jarvis, il suo assistente per la casa e il PC." if not weather else
                           "A Roma ci sono 22 gradi, credo.")

    fake.behave = behave
    try:
        results = await examiner.run()
    finally:
        await bridge.stop()
    by = {r.model: r for r in results}
    assert by[GLM].passed and by[GLM].first_word_s is not None
    assert not by["big-pickle"].italian
    assert by["nemotron-3.5-lightning-free"].italian and not by["nemotron-3.5-lightning-free"].tools
    assert p.best() == GLM and p.exam_at > 0
    assert len(fake.deleted) == 3  # le sessioni dell'esame non restano nell'archivio
    assert "promosso" in p.describe() and "bocciato" in p.describe()


async def test_exam_stops_at_the_free_limit_and_keeps_partial_results(oc, tmp_path):
    fake, client = oc
    p, _ = picker_with(tmp_path)
    examiner = Examiner(StubHub(client, p), p)

    async def behave(turn, text):
        if turn.model == GLM:
            await turn.say("Sono Jarvis, il suo assistente: posso aprire le app per lei.")
        else:
            await turn.status("retry", attempt=1, next=0, message="Free usage exceeded",
                              action={"reason": "free_tier_limit"})
            await turn.wait_abort()

    fake.behave = behave
    results = await examiner.run()
    assert [r.model for r in results] == [GLM]  # il primo esaminato, poi il limite: gli altri non si toccano
    assert not results[0].tools and p.results[GLM].error
    # al prossimo esame prima quelli mai esaminati (il bocciato è ora ultimo in classifica)
    assert examiner.candidates() == ["nemotron-3.5-lightning-free", "big-pickle", GLM]

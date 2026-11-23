"""Cervello Claude con un client finto che emette i messaggi veri dell'SDK (nessuna chiamata di rete)."""

import asyncio
import contextlib

import pytest
from claude_agent_sdk.types import RateLimitEvent, RateLimitInfo, ResultMessage, StreamEvent

from jarvis.brain.base import BrainUnavailable, Done, Notice, TextDelta, ToolStart
from jarvis.brain.claude import ClaudeBrain
from jarvis.brain.tools import ToolRegistry


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.queries = []

    async def query(self, message):
        self.queries.append(message)

    async def receive_response(self):
        for m in self.messages:
            yield m


def result(is_error=False, text="ok"):
    return ResultMessage(subtype="error_during_execution" if is_error else "success", duration_ms=1,
                         duration_api_ms=1, is_error=is_error, num_turns=1, session_id="s", result=text)


def brain_with(messages):
    b = ClaudeBrain("prompt", ToolRegistry(), "claude-opus-5-5")
    b._client = FakeClient(messages)
    return b


async def test_session_limit_becomes_provider_wide_unavailability_with_reset_time():
    """La sequenza vista dal vivo: evento di limite "rejected", poi un risultato d'errore."""
    resets = 1791224400
    b = brain_with([
        RateLimitEvent(RateLimitInfo(status="rejected", resets_at=resets, rate_limit_type="five_hour"), "u", "s"),
        result(True, "You've hit your session limit · resets 8:20pm (Europe/Rome)"),
    ])
    evs = []
    with pytest.raises(BrainUnavailable) as ei:
        async for ev in b.respond("ciao"):
            evs.append(ev)
    assert ei.value.scope == "provider" and ei.value.retry_at == resets
    assert isinstance(evs[0], Notice) and evs[0].info["status"] == "rejected" and evs[0].info["resets_at"] == resets
    assert b.provider == "claude"


async def test_warning_level_rate_limit_does_not_block():
    b = brain_with([
        RateLimitEvent(RateLimitInfo(status="allowed_warning", resets_at=1, utilization=0.9), "u", "s"),
        StreamEvent("u", "s", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Subito."}}),
        result(False, "Subito."),
    ])
    evs = [ev async for ev in b.respond("ciao")]
    assert any(isinstance(e, TextDelta) and e.text == "Subito." for e in evs)
    assert isinstance(evs[-1], Done) and evs[-1].error is None


async def test_the_model_error_code_counts_too():
    """Il 23/11 il messaggio del modello aveva error="authentication_failed": anche se il risultato non dice parole
    note, è un accesso scaduto e si passa al cervello dopo (prima: "problema nel formulare la risposta")."""
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    b = brain_with([AssistantMessage([TextBlock("Failed")], "claude-opus-5-5", error="authentication_failed"),
                    result(True, "error_during_execution")])
    with pytest.raises(BrainUnavailable) as ei:
        [ev async for ev in b.respond("ciao")]
    assert ei.value.scope == "provider" and ei.value.kind == "accesso"


async def test_ordinary_error_is_reported_in_done_not_as_unavailability():
    b = brain_with([result(True, "Tool input non valido")])
    evs = [ev async for ev in b.respond("ciao")]
    assert isinstance(evs[-1], Done) and evs[-1].error == "Tool input non valido"


async def test_the_start_of_a_tool_call_is_reported_right_away():
    """Il blocco tool_use si apre subito, gli argomenti arrivano dopo: la conversazione lo sa dal primo istante."""
    start = StreamEvent("u", "s", {"type": "content_block_start", "index": 1,
                                   "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__jarvis__avvia_compito",
                                                     "input": {}}})
    inner = StreamEvent("u", "s", {"type": "content_block_start", "index": 0,
                                   "content_block": {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}},
                        parent_tool_use_id="t9")  # di un sotto-agente: non è Jarvis che parla
    b = brain_with([start, inner, result(False, "")])
    evs = [ev async for ev in b.respond("ciao")]
    assert [e.name for e in evs if isinstance(e, ToolStart)] == ["avvia_compito"]


def delta(text):
    return StreamEvent("u", "s", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


class SessionClient:
    """Come il client vero: un'unica coda di messaggi per tutta la sessione, receive_response legge fino al primo
    risultato. Le risposte arrivano in due pezzi; interrupt() chiude il turno in corso con error_during_execution."""

    def __init__(self, replies, closes_on_interrupt=True):
        self.replies, self.closes = replies, closes_on_interrupt
        self.q = asyncio.Queue()
        self.queries = []
        self._rest = None

    async def query(self, message):
        self.queries.append(message)
        text = self.replies[message]
        await self.q.put(delta(text[:3]))
        self._rest = asyncio.ensure_future(self._finish(text))

    async def _finish(self, text):
        await asyncio.sleep(0.05)
        await self.q.put(delta(text[3:]))
        await self.q.put(result(False, text))

    async def interrupt(self):
        if self._rest is not None and not self._rest.done():
            self._rest.cancel()
            if self.closes:
                await self.q.put(result(True, None))

    async def receive_response(self):
        while True:
            m = await self.q.get()
            yield m
            if isinstance(m, ResultMessage):
                return


async def ask(b, message):
    return [ev async for ev in b.respond(message)]


async def interrupted_turn(b, message):
    """Come fa la conversazione quando l'utente parla sopra: interrupt() e poi si smette di leggere."""
    task = asyncio.ensure_future(ask(b, message))
    await asyncio.sleep(0.01)  # il primo pezzo è arrivato
    await b.interrupt()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


REPLIES = {"prima": "Risposta alla prima.", "seconda": "Risposta alla seconda.", "terza": "Risposta alla terza."}


async def test_after_an_interruption_each_answer_belongs_to_its_own_question():
    """26/10 dal vivo: dopo un'interruzione il risultato vecchio restava in coda. Il turno dopo riceveva quello
    ("Ho avuto un problema", error_during_execution) e da lì ogni risposta arrivava una domanda in ritardo."""
    b = ClaudeBrain("prompt", ToolRegistry(), "claude-opus-5-5")
    b._client = SessionClient(REPLIES)
    await interrupted_turn(b, "prima")
    second = await ask(b, "seconda")
    assert second[-1].error is None and second[-1].text == "Risposta alla seconda."
    third = await ask(b, "terza")
    assert third[-1].text == "Risposta alla terza."


async def test_a_turn_that_never_closes_means_a_fresh_session(monkeypatch):
    b = ClaudeBrain("prompt", ToolRegistry(), "claude-opus-5-5")
    b._client = SessionClient(REPLIES, closes_on_interrupt=False)  # il processo non chiude il turno interrotto
    b.drain_timeout_s = 0.2
    restarted = []

    async def fake_restart():
        restarted.append(True)
        b._client = SessionClient(REPLIES)

    monkeypatch.setattr(b, "restart", fake_restart)
    await interrupted_turn(b, "prima")
    second = await ask(b, "seconda")
    assert restarted == [True] and second[-1].text == "Risposta alla seconda."


async def test_a_new_session_has_no_leftovers_to_wait_for(monkeypatch):
    """Interruzione e poi sessione caduta: nella sessione nuova non c'è un risultato vecchio da aspettare."""
    b = ClaudeBrain("prompt", ToolRegistry(), "claude-opus-5-5")
    b._client = SessionClient(REPLIES)
    restarted = []

    async def fake_restart():
        restarted.append(True)

    monkeypatch.setattr(b, "restart", fake_restart)
    b.drain_timeout_s = 0.5
    await interrupted_turn(b, "prima")
    await b.stop()
    b._client = SessionClient(REPLIES)  # come la ripartenza dopo la caduta
    second = await ask(b, "seconda")
    assert restarted == [] and second[-1].text == "Risposta alla seconda."

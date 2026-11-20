import time

import pytest

from jarvis.brain.base import BrainUnavailable, Done, Notice, TextDelta
from jarvis.brain.claude import classify_error, resets_at
from jarvis.brain.router import DEFAULT_PAUSE_S, BrainRouter
from jarvis.core.conversation import _when, switch_sentence, unavailable_sentence


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class B:
    def __init__(self, name, provider=None, error=None, reply="ok"):
        self.name, self.provider, self.error, self.reply = name, provider or name, error, reply
        self.messages, self.interrupted = [], 0

    async def respond(self, message):
        self.messages.append(message)
        if self.error is not None:
            yield Notice("rate_limit", {"status": "rejected"})  # un avviso non è una risposta iniziata
            raise self.error
        yield TextDelta(self.reply)
        yield Done(self.reply)

    async def interrupt(self):
        self.interrupted += 1


async def collect(router, msg="ciao"):
    return [ev async for ev in router.respond(msg)]


def switches(evs):
    return [e for e in evs if isinstance(e, Notice) and e.kind == "brain_switch"]


async def test_primary_answers_without_notices():
    a, b = B("opus"), B("locale")
    evs = await collect(BrainRouter([a, b]))
    assert [type(e).__name__ for e in evs] == ["TextDelta", "Done"] and b.messages == []


async def test_failure_pauses_until_reset_then_returns_to_primary():
    clock = Clock()
    opus = B("opus", "claude", BrainUnavailable("session limit", retry_at=clock.t + 600, scope="provider"))
    haiku, local = B("haiku", "claude"), B("bonsai", "local", reply="dal locale")
    r = BrainRouter([opus, haiku, local], recap=lambda: "Andrea: prima domanda", clock=clock)
    evs = await collect(r, "domanda")
    sw = switches(evs)
    assert len(sw) == 1 and sw[0].info["to_provider"] == "local" and sw[0].info["until"] == clock.t + 600
    assert haiku.messages == []  # stesso account: saltato
    assert "<riassunto>" in local.messages[0] and "prima domanda" in local.messages[0]
    # turno successivo, ancora in pausa: nessun nuovo annuncio, niente riassunto ripetuto
    evs = await collect(r, "seconda")
    assert switches(evs) == []
    assert local.messages[1] == "seconda" and len(opus.messages) == 1
    # dopo il ripristino si torna al principale, che riceve il riassunto di ciò che ha perso
    clock.t += 601
    opus.error = None
    evs = await collect(r, "terza")
    back = switches(evs)
    assert back and back[0].info["back"] is True
    assert "<riassunto>" in opus.messages[-1] and len(local.messages) == 2


async def test_transient_error_pauses_only_that_brain_for_a_minute():
    clock = Clock()
    opus = B("opus", "claude", BrainUnavailable("overloaded"))
    haiku = B("haiku", "claude", reply="riserva")
    r = BrainRouter([opus, haiku], clock=clock)
    evs = await collect(r)
    assert any(isinstance(e, TextDelta) and e.text == "riserva" for e in evs)
    assert r.paused_until[id(opus)] == clock.t + DEFAULT_PAUSE_S and id(haiku) not in r.paused_until


async def test_everything_down_raises_exhausted_with_reset_time():
    clock = Clock()
    err = BrainUnavailable("session limit", retry_at=clock.t + 300, scope="provider")
    r = BrainRouter([B("opus", "claude", err), B("bonsai", "local", BrainUnavailable("connessione rifiutata"))],
                    clock=clock)
    with pytest.raises(BrainUnavailable) as ei:
        await collect(r)
    assert ei.value.exhausted and ei.value.scope == "provider" and ei.value.retry_at == clock.t + 300


async def test_failure_mid_answer_is_not_retried_elsewhere():
    class Half(B):
        async def respond(self, message):
            yield TextDelta("Sto per")
            raise BrainUnavailable("caduto")

    other = B("altro")
    r = BrainRouter([Half("opus"), other])
    with pytest.raises(BrainUnavailable):
        await collect(r)
    assert other.messages == []  # non ripete la frase con un'altra voce


async def test_interrupt_reaches_the_active_brain():
    r = None

    class Slow(B):
        async def respond(self, message):
            await r.interrupt()
            yield Done("")

    s = Slow("opus")
    r = BrainRouter([s])
    await collect(r)
    assert s.interrupted == 1


def test_classify_error():
    e = classify_error("You've hit your session limit · resets 8:20pm")
    assert e is not None and e.scope == "provider"
    e = classify_error("qualunque cosa", rejected_until=123.0)
    assert e is not None and e.scope == "provider" and e.retry_at == 123.0
    e = classify_error("Overloaded")
    assert e is not None and e.scope == "brain"
    assert classify_error("Tool input non valido") is None


def test_any_hit_your_limit_is_the_account_limit_with_its_reset_time():
    """20/11: "5-hour limit" non era riconosciuto e Jarvis restava su Claude invece di passare a OpenCode."""
    from datetime import datetime

    now = datetime(2026, 11, 20, 9, 15).timestamp()
    for msg in ("You've hit your 5-hour limit · resets 1:40pm (Europe/Rome)", "You've hit your weekly limit",
                "You've hit your limit · resets 1:40pm"):
        e = classify_error(msg)
        assert e is not None and e.scope == "provider", msg
    assert resets_at("You've hit your 5-hour limit · resets 1:40pm (Europe/Rome)", now) == \
        datetime(2026, 11, 20, 13, 40).timestamp()
    assert resets_at("resets 8am", now) == datetime(2026, 11, 21, 8, 0).timestamp()  # già passata: domani
    assert resets_at("resets 12:05am", now) == datetime(2026, 11, 21, 0, 5).timestamp()
    assert resets_at("resets 21:30", now) == datetime(2026, 11, 20, 21, 30).timestamp()
    assert resets_at("You've hit your weekly limit · resets Nov 24", now) is None
    assert classify_error("qualunque cosa", rejected_until=123.0).retry_at == 123.0  # l'ora esatta dell'SDK vince


def test_sentences():
    soon = time.time() + 120
    assert _when(soon).startswith("fino alle ")
    assert _when(time.time() + 86400).startswith("fino a domani alle ")
    assert _when(None) == ""
    s = switch_sentence({"scope": "provider", "until": soon, "to_provider": "local"})
    assert s.startswith("Ho esaurito l'uso di Claude fino alle") and "cervello locale" in s
    assert "riserva" in switch_sentence({"scope": "brain", "to_provider": "claude"})
    e = BrainUnavailable("x", retry_at=soon, scope="provider", exhausted=True)
    assert "non ho un cervello di riserva" in unavailable_sentence(e, "signore")
    assert "signore" in unavailable_sentence(BrainUnavailable("x"), "signore")


def test_sentences_for_opencode():
    soon = time.time() + 120
    s = switch_sentence({"scope": "provider", "until": soon, "to_provider": "opencode", "from_provider": "claude"})
    assert s.startswith("Ho esaurito l'uso di Claude fino alle") and "modello gratuito di OpenCode" in s
    s = switch_sentence({"scope": "provider", "until": soon, "to_provider": "claude", "from_provider": "opencode"})
    assert s.startswith("Ho esaurito l'uso gratuito di OpenCode fino alle")


async def test_provider_exhaustion_is_only_the_account_limit():
    clock = Clock()
    limit = BrainUnavailable("session limit", retry_at=clock.t + 600, scope="provider")
    r = BrainRouter([B("opus", "claude", limit), B("bonsai", "local")], clock=clock)
    assert r.provider_paused_until("claude") is None
    await collect(r)
    assert r.provider_paused_until("claude") == clock.t + 600
    clock.t += 601
    assert r.provider_paused_until("claude") is None
    slow = BrainRouter([B("opus", "claude", BrainUnavailable("overloaded")), B("bonsai", "local")], clock=clock)
    await collect(slow)
    assert slow.provider_paused_until("claude") is None  # solo lento: i sotto-agenti continuano con Claude


async def test_preferred_provider_goes_first_live():
    pref = {"v": "claude"}
    opus, cloud, local = B("opus", "claude"), B("gpt-oss", "ollama-cloud"), B("bonsai", "local")
    r = BrainRouter([opus, cloud, local], preferred=lambda: pref["v"])
    await collect(r)
    assert len(opus.messages) == 1
    pref["v"] = "local"  # "Jarvis, usa il cervello locale": vale dal turno dopo, senza riavvio
    evs = await collect(r)
    assert len(local.messages) == 1 and len(opus.messages) == 1
    assert switches(evs)[0].info["back"] is True  # è il preferito: nessun annuncio di ripiego a voce
    assert r.order() == [local, opus, cloud]


async def test_start_survives_a_missing_preferred_brain():
    class Off(B):
        async def start(self):
            raise BrainUnavailable("server locale non raggiungibile")

    clock = Clock()
    off, opus = Off("bonsai", "local"), B("opus", "claude")
    r = BrainRouter([opus, off], preferred=lambda: "local", clock=clock)
    await r.start()  # non solleva
    assert r.paused_until[id(off)] == clock.t + DEFAULT_PAUSE_S
    evs = await collect(r)
    assert any(isinstance(e, TextDelta) for e in evs) and len(opus.messages) == 1


def test_brain_choice_by_voice_accepts_synonyms():
    from jarvis.tools.front import _provider

    assert _provider("Locale") == "local" and _provider("bonsai") == "local"
    assert _provider("cloud") == "ollama-cloud" and _provider("claude") == "claude"
    with pytest.raises(ValueError):
        _provider("chatgpt")

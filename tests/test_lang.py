"""Jarvis in inglese: frasi fisse, parole di stop, lettura dei numeri, persona, voce e impostazione a voce."""

import time

import pytest
from test_conversation import Harness, ScriptedBrain
from test_voice_output import META, FakeTts

from jarvis import lang
from jarvis.brain.base import BrainUnavailable, ToolCall
from jarvis.brain.persona import guest_prompt, system_prompt
from jarvis.config import Config, ConfigStore
from jarvis.core.bus import Bus
from jarvis.core.conversation import is_stop_command, switch_sentence, unavailable_sentence
from jarvis.core.narration import NarrationFilter
from jarvis.memory.store import Memory
from jarvis.speech.text import speakable
from jarvis.speech.voice import VoiceOutput
from jarvis.tools.front import Services, build_front_tools


def test_unknown_languages_fall_back_to_italian():
    assert lang.norm("en") == "en" and lang.norm("fr") == "it" and lang.norm(None) == "it"
    assert lang.phrase("de", "checking") == "Controllo subito."


def test_address_becomes_sir_only_in_english():
    assert lang.address("en", "signore") == "sir"
    assert lang.address("it", "signore") == "signore"
    assert lang.address("en", "capo") == "capo"  # un nome scelto dall'utente resta com'è


def test_every_phrase_exists_in_both_languages():
    assert set(lang.PHRASES["it"]) == set(lang.PHRASES["en"])
    assert len(lang.COMMON_PHRASES["it"]) == len(lang.COMMON_PHRASES["en"])


@pytest.mark.parametrize("said", ["enough", "Jarvis, stop please", "that's enough", "Hold on.", "be quiet",
                                  "basta", "zitto, grazie"])
def test_stop_words_in_both_languages(said):
    assert is_stop_command(said)


@pytest.mark.parametrize("said", ["enough about the news, what's the weather?", "wait for the download to finish",
                                  "basta con le notizie, che tempo fa?"])
def test_a_real_request_is_not_a_stop(said):
    assert not is_stop_command(said)


def test_brain_switch_sentences_in_english():
    soon = time.time() + 600
    s = switch_sentence({"scope": "provider", "until": soon, "to_provider": "opencode"}, "en")
    assert s.startswith("I've used up my Claude allowance until ") and s.endswith("with a free OpenCode model.")
    assert switch_sentence({"scope": "brain", "to_provider": "claude"}, "en") == \
        "My main brain isn't responding: switching to the backup system."
    e = BrainUnavailable("x", exhausted=True, scope="provider", retry_at=None)
    assert unavailable_sentence(e, "sir", language="en") == \
        "I've used up my Claude allowance for now, and I have no backup brain available."
    assert "sir" in unavailable_sentence(BrainUnavailable("x"), "sir", language="en")
    assert "signore" in unavailable_sentence(BrainUnavailable("x"), "signore")  # l'italiano resta com'era


async def test_web_search_filler_in_english():
    cfg = Config()
    cfg.persona.language = "en"
    h = Harness(ScriptedBrain([[ToolCall("WebSearch", {"query": "weather"}), "Sunshine tomorrow."]]),
                stt_map={1: "Jarvis, what's the weather tomorrow?"}, cfg=cfg)
    h.voice.cached.add("Let me check.")
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.voice.said[0] == "Let me check."
    await h.stop()


async def test_english_narration_is_spoken_in_english():
    """In italiano "Let me check the weather." prima di uno strumento si scarta; in inglese è la frase giusta."""
    cfg = Config()
    cfg.persona.language = "en"
    h = Harness(ScriptedBrain([["Let me check the weather. ", ToolCall("WebSearch", {"query": "weather"}),
                                "Sunshine tomorrow."]]), stt_map={1: "Jarvis, weather?"}, cfg=cfg)
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.events("jarvis_done")[-1]["text"].startswith("Let me check the weather.")
    await h.stop()


def test_narration_filter_can_be_switched_off():
    f = NarrationFilter(active=False)
    assert f.feed("Let me check") == "Let me check"
    f.tool()
    assert f.feed("I'll look") == "I'll look"
    on = NarrationFilter()
    assert on.feed("Let me check") == ""


@pytest.mark.parametrize(("text", "spoken"), [
    ("It costs $5 and weighs 1 kg.", "It costs five dollars and weighs one kilo."),
    ("At 8:30, then 12:00 and 0:00.", "At eight thirty, then noon and midnight."),
    ("At 9:05.", "At nine oh five."),
    ("On 21/11/2026.", "On the twenty-first of November twenty twenty-six."),
    ("4,892 points", "four thousand, eight hundred and ninety-two points"),
    ("3.14 and -3.5 °C", "three point one four and minus three point five degrees"),
    ("75% of the GPU", "seventy-five percent of the G P U"),
    ("e.g. NASA vs the AI", "for example NASA versus the A I"),
    ("see www.example.com/page", "see example dot com"),
    ("1 km away, 2 km/h", "one kilometre away, two kilometres per hour"),
])
def test_english_reading(text, spoken):
    assert speakable(text, "en") == spoken


def test_italian_reading_is_unchanged_by_default():
    assert speakable("Alle 8.30 costava 4.892 € e 3,5 km.") == \
        "Alle otto e mezza costava quattromilaottocentonovantadue euro e tre virgola cinque chilometri."
    assert speakable("3.14", "en") == "three point one four"  # in inglese 3.14 non è un orario


async def test_voice_reads_and_warms_up_in_its_language(tmp_path):
    tts = FakeTts()
    v = VoiceOutput(tts, META, language="English", cache_dir=tmp_path)
    await v.say("It costs $5.")
    assert tts.calls[-1][1] == "It costs five dollars."
    await v.warm_up()
    said = {text for _, text, _ in tts.calls}
    assert "Let me check." in said and "Controllo subito." not in said
    await v.prime()
    assert tts.calls[-1][1] == "Systems online."


def test_persona_speaks_english_with_english_examples():
    cfg = Config()
    cfg.persona.user_name = "Andrea"
    italian = system_prompt(cfg)
    assert "Italiano naturale" in italian and '"Controllo subito."' in italian and '"signore"' in italian
    cfg.persona.language = "en"
    english = system_prompt(cfg)
    assert "parli SEMPRE in inglese" in english and '"sir"' in english
    assert '"Let me check."' in english and '"Controllo subito."' not in english
    assert '"Right away." o "Certainly, sir."' in english
    guest = guest_prompt(cfg)
    assert "parli SEMPRE in inglese" in guest and '"sir"' not in guest  # di un ospite non si sa


def test_an_event_is_not_answered_with_right_away():
    """Provato il 22/11: la presentazione cominciava con "Certamente, signore." senza che nessuno avesse chiesto
    niente. La frase breve d'apertura vale per le richieste, non per gli [EVENTO]."""
    cfg = Config()
    for code, short in (("it", '"Subito." o "Certamente, signore."'), ("en", '"Right away." o "Certainly, sir."')):
        cfg.persona.language = code
        rule = next(line for line in system_prompt(cfg).splitlines() if "La prima frase di ogni risposta" in line)
        assert f"a un [EVENTO] nessuno ti ha chiesto niente: niente {short}" in rule


@pytest.fixture
def tools(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    s = Services(Bus(), store, Memory(tmp_path / "m.db"), None, None, restart=lambda: "riavvio in arrivo",
                 appliers={"persona.language": lambda code: lang.follow_language(store, code)})
    return build_front_tools(s), store


@pytest.mark.parametrize(("said", "code"), [("inglese", "en"), ("English", "en"), ("italiano", "it")])
async def test_language_by_voice(tools, said, code):
    reg, store = tools
    out = await reg.call("impostazione", {"chiave": "lingua", "valore": said})
    assert store.get("persona.language") == code and "riavvio" in out


async def test_unknown_language_is_refused(tools):
    reg, store = tools
    out = await reg.call("impostazione", {"chiave": "lingua", "valore": "francese"})
    assert out.startswith("Errore") and "italiano o inglese" in out and store.get("persona.language") == "it"


async def test_the_included_voice_follows_the_language(tools):
    reg, store = tools
    store.set("speech.tts_voice", "base-it")
    out = await reg.call("impostazione", {"chiave": "lingua", "valore": "inglese"})
    assert store.get("speech.tts_voice") == "base-en" and "base-en" in out
    store.set("speech.tts_voice", "jarvis")  # una voce tua resta
    await reg.call("impostazione", {"chiave": "lingua", "valore": "italiano"})
    assert store.get("speech.tts_voice") == "jarvis"

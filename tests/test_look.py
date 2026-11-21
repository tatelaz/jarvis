"""L'aspetto si cambia a voce e la finestra lo riceve subito; i sottotitoli seguono la voce vera."""

import asyncio

import numpy as np
import pytest

from jarvis.audio.player import Segment
from jarvis.config import Config, ConfigStore, is_live
from jarvis.core.bus import Bus
from jarvis.core.speaker import ResponseSpeaker
from jarvis.memory.store import Memory
from jarvis.tools.front import Services, build_front_tools
from jarvis.ui import look


def test_colors_in_italian_or_hex():
    assert look.color("rosso") == "#ff4d4d" and look.color(" Azzurro ") == "#8fd8ff"
    assert look.color("#ABCDEF") == "#abcdef" and look.color("123456") == "#123456"
    assert look.color("") == "" and look.color("predefinito") == ""
    with pytest.raises(ValueError):
        look.color("pippo")


def test_ui_config_never_sends_an_invalid_color():
    cfg = Config()
    cfg.ui.accent = "pippo"  # scritto a mano nel file
    assert look.ui_config(cfg)["accent"] == ""
    cfg.ui.accent, cfg.ui.text_scale = "#ff4d4d", 9.0
    out = look.ui_config(cfg)
    assert out["accent"] == "#ff4d4d" and out["text_scale"] == 1.8 and out["theme"] == "mark3"


@pytest.fixture
def tools(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    bus = Bus()
    return build_front_tools(Services(bus, store, Memory(tmp_path / "m.db"))), store, bus


@pytest.mark.parametrize("key, said, stored", [
    ("grafica", "cinema", "cinema"), ("grafica", "linea", "cinema"), ("grafica", "Mark 3", "mark3"),
    ("ui.theme", "sottotitoli", "cinema"), ("colore", "viola", "#a98bff"), ("ui.accent", "rosso", "#ff4d4d"),
    ("effetti", "calmi", "sobri"), ("grandezza_testo", "1,3", 1.3), ("grafica", "Mark III", "mark3"),
    ("grandezza_testo", "120%", 1.2), ("ui.text_scale", "1,4", 1.4), ("effetti", "meno", "sobri"),
    ("ui.effects", "spettacolari", "pieni"), ("ui.accent", "nessuno", ""),
])
async def test_appearance_by_voice(tools, key, said, stored):
    reg, store, bus = tools
    out = await reg.call("impostazione", {"chiave": key, "valore": said})
    dotted = {"grafica": "ui.theme", "colore": "ui.accent", "effetti": "ui.effects",
              "grandezza_testo": "ui.text_scale"}.get(key, key)
    assert store.get(dotted) == stored and "vale subito" in out
    assert bus.history[-1]["type"] == "settings_changed" and bus.history[-1]["key"] == dotted


@pytest.mark.parametrize("key, said", [
    ("ui.accent", "pippo"), ("grafica", "barocca"),
    ("grafica", "film"),  # ambiguo (Mark III viene dal film, Cinema ha i sottotitoli da film): meglio chiedere
    ("effetti", "rosso"), ("ui.effects", "boh"),
    ("grandezza_testo", "5"), ("ui.text_scale", "0,2"), ("grandezza_testo", "300%"),  # illeggibile: rifiutato
])
async def test_unknown_appearance_is_refused(tools, key, said):
    reg, store, _ = tools
    assert (await reg.call("impostazione", {"chiave": key, "valore": said})).startswith("Errore")
    assert store.get("ui.accent") == "" and store.get("ui.theme") == "mark3"
    assert store.get("ui.effects") == "pieni" and store.get("ui.text_scale") == 1.0


def test_appearance_settings_are_live():
    assert all(is_live(k) for k in ("ui.theme", "ui.accent", "ui.text_scale", "ui.effects"))


def test_said_chars_follows_playback():
    class NoVoice:
        def is_cached(self, text):
            return False

    sp = ResponseSpeaker(voice=NoVoice(), play=lambda *a, **k: None)
    a, b = Segment(np.zeros(1000, np.float32), "Subito."), Segment(np.zeros(1000, np.float32), "Il volo parte alle sette.")
    sp.segments = [a, b]
    assert sp.said_chars() == 0
    a.pos = 500
    assert sp.said_chars() == 3  # metà di "Subito."
    a.pos, a.finished, b.pos = 1000, True, 500
    assert sp.said_chars() == len("Subito.") + 1 + len("Il volo parte alle sette.") // 2
    b.finished = True
    assert sp.said_chars() == len("Subito.") + 1 + len("Il volo parte alle sette.") + 1


async def test_server_turns_a_settings_change_into_a_redraw():
    from jarvis.ui.server import UiServer

    class FakeWs:
        closed = False

        def __init__(self):
            self.sent = []

        async def send_json(self, data, dumps=None):
            self.sent.append(data)
            if len(self.sent) == 2:
                self.closed = True

    class App:
        cfg = Config()

    App.cfg.ui.theme = "cinema"
    srv = UiServer(App())
    ws, q = FakeWs(), asyncio.Queue()
    q.put_nowait({"type": "settings_changed", "key": "ui.theme"})
    q.put_nowait({"type": "settings_changed", "key": "speech.tts_speed"})  # non riguarda la finestra: passa com'è
    await asyncio.wait_for(srv._pump(ws, q), 2)
    assert ws.sent[0]["type"] == "ui_config" and ws.sent[0]["theme"] == "cinema"
    assert ws.sent[1] == {"type": "settings_changed", "key": "speech.tts_speed"}


def test_window_language_follows_the_persona():
    from jarvis.config import Config
    from jarvis.ui.look import ui_config

    cfg = Config()
    assert ui_config(cfg)["lang"] == "it"
    cfg.persona.language = "en"
    assert ui_config(cfg)["lang"] == "en"
    cfg.persona.language = "fr"  # scritto a mano nel file: si torna all'italiano
    assert ui_config(cfg)["lang"] == "it"

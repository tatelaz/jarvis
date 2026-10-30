"""Le voci incluse in Jarvis (lettori di LibriVox) e come si trova una voce: prima le tue, poi quelle incluse."""

import json

import pytest
import soundfile as sf

from jarvis import paths
from jarvis.audio.voicefx import VoiceFxParams
from jarvis.config import ConfigStore
from jarvis.core.bus import Bus
from jarvis.memory.store import Memory
from jarvis.speech import engine
from jarvis.tools.front import Services, build_front_tools

BASE = ("base-it", "base-en")


@pytest.mark.parametrize("name", BASE)
def test_included_voices_are_complete(name):
    d = engine.BUNDLED_VOICES / name
    meta = json.loads((d / "voice.json").read_text(encoding="utf-8"))
    assert meta["name"] == name and meta["language"] == name.split("-")[1]
    assert meta["ref_text"].strip().endswith(".") and len(meta["ref_text"]) > 80
    assert "dominio pubblico" in meta["source"]["licence"] and meta["source"]["url"].startswith("https://archive.org/")
    VoiceFxParams(**meta["fx"])  # l'effetto ha solo parametri che esistono
    info = sf.info(d / meta["ref"])
    assert (info.samplerate, info.channels, info.subtype) == (24000, 1, "PCM_16")  # come la voce del film
    assert 8 <= info.duration <= 15  # abbastanza per il timbro, non troppo per il modello


def make_voice(base, name):
    (base / name).mkdir(parents=True)
    (base / name / "voice.json").write_text(json.dumps({"name": name, "ref": "ref.wav", "ref_text": "x."}),
                                            encoding="utf-8")


def test_your_voices_come_first(tmp_path):
    mine, included = tmp_path / "data", tmp_path / "incluse"
    make_voice(included, "base-it")
    make_voice(included, "jarvis")
    make_voice(mine, "jarvis")
    assert engine.voice_dir("jarvis", mine, included) == mine / "jarvis"
    assert engine.voice_dir("base-it", mine, included) == included / "base-it"
    assert engine.voice_dir("altra", mine, included) is None
    assert engine.available_voices(mine, included) == ["base-it", "jarvis"]


@pytest.mark.parametrize("name", ["", "../fuori", "..\\fuori", "incluse/../fuori", ".", "..", ".nascosta", "ASSOLUTO"])
def test_a_voice_name_is_never_a_path(tmp_path, name):
    make_voice(tmp_path / "incluse", "jarvis")
    make_voice(tmp_path / "incluse", ".nascosta")
    make_voice(tmp_path, "fuori")  # una voce vera fuori dalle cartelle delle voci: non deve essere raggiungibile
    (tmp_path / "voice.json").write_text("{}", encoding="utf-8")  # e nemmeno la cartella sopra ("..")
    name = str(tmp_path / "fuori") if name == "ASSOLUTO" else name
    assert engine.voice_dir(name, tmp_path / "data", tmp_path / "incluse") is None


def test_load_voice_falls_back_to_the_included_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    meta = engine.load_voice("base-en")
    assert meta["ref_path"].endswith("base-en/ref.wav") and (engine.BUNDLED_VOICES / "base-en" / "ref.wav").exists()
    with pytest.raises(engine.EngineError, match="base-en, base-it"):
        engine.load_voice("jarvis")  # su un PC nuovo la voce del film non c'è: l'errore dice quali ci sono


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    store = ConfigStore(tmp_path / "config.yaml")
    s = Services(Bus(), store, Memory(tmp_path / "m.db"), None, None, restart=lambda: "riavvio in arrivo")
    return build_front_tools(s), store


@pytest.mark.parametrize(("said", "voice"), [("inglese", "base-en"), ("Simon", "base-en"), ("di base", "base-it"),
                                             ("italiana", "base-it"), ("base-en", "base-en")])
async def test_voice_can_be_chosen_by_voice(tools, said, voice):
    reg, store = tools
    out = await reg.call("impostazione", {"chiave": "voce_di_jarvis", "valore": said})
    assert store.get("speech.tts_voice") == voice and "riavvio" in out


async def test_film_voice_only_if_it_is_there(tools, tmp_path):
    reg, store = tools
    out = await reg.call("impostazione", {"chiave": "voce_di_jarvis", "valore": "quella del film"})
    assert out.startswith("Errore") and "base-en, base-it" in out
    assert store.get("speech.tts_voice") == "jarvis"  # niente cambiato (è il predefinito)
    store.set("speech.tts_voice", "base-it")
    make_voice(tmp_path / "voices", "jarvis")
    await reg.call("impostazione", {"chiave": "voce_di_jarvis", "valore": "film"})
    assert store.get("speech.tts_voice") == "jarvis"


async def test_unknown_voice_is_refused_even_by_its_full_key(tools):
    reg, store = tools
    out = await reg.call("impostazione", {"chiave": "speech.tts_voice", "valore": "robot"})
    assert out.startswith("Errore") and "voce sconosciuta" in out
    assert store.get("speech.tts_voice") == "jarvis"

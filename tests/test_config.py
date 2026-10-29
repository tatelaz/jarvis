import pytest

from jarvis.config import ConfigError, ConfigStore


def test_defaults_without_file(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    assert store.config.brain.provider == "claude"
    assert store.get("speech.tts_speed") == 1.0


def test_set_saves_only_differences_and_reloads(tmp_path):
    p = tmp_path / "config.yaml"
    store = ConfigStore(p)
    store.set("speech.tts_speed", 1.1)
    store.set("persona.address_as", "Andrea")
    text = p.read_text(encoding="utf-8")
    assert "tts_speed: 1.1" in text and "provider" not in text
    again = ConfigStore(p)
    assert again.get("speech.tts_speed") == 1.1
    assert again.get("persona.address_as") == "Andrea"


def test_int_is_accepted_for_float(tmp_path):
    store = ConfigStore(tmp_path / "c.yaml")
    store.set("speech.tts_speed", 1)
    assert isinstance(store.get("speech.tts_speed"), float)


@pytest.mark.parametrize("dotted,value", [
    ("speech.tts_sped", 1.0),  # refuso nella chiave
    ("audio.barge_in", "sì"),  # tipo sbagliato
    ("audio.min_speech_ms", True),  # bool non è un int valido
    ("speech", 3),  # manca la chiave
    ("audio.aec_mode", "forse"),  # valore fuori dalle scelte ammesse
    ("brain.provider", "openai"),
])
def test_invalid_settings_are_rejected(tmp_path, dotted, value):
    store = ConfigStore(tmp_path / "c.yaml")
    with pytest.raises(ConfigError):
        store.set(dotted, value)


def test_unknown_key_in_file_is_an_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("audio:\n  vad_treshold: 0.4\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="vad_treshold"):
        ConfigStore(p)


def test_optional_and_list_fields(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("audio:\n  input_device: null\nsafety:\n  workspace_dirs: ['C:/x', 'D:/y']\n", encoding="utf-8")
    store = ConfigStore(p)
    assert store.get("audio.input_device") is None
    assert store.get("safety.workspace_dirs") == ["C:/x", "D:/y"]
    with pytest.raises(ConfigError):
        store.set("safety.workspace_dirs", ["ok", 3])

"""Claude con l'abbonamento o con la chiave API di Anthropic: cassaforte, variabili di ogni sessione, errori di
accesso, scorciatoia a voce. Nessuna chiave vera: quelle qui sono inventate."""

import logging
import time

import pytest

from jarvis.brain import claude_cli
from jarvis.brain.base import BrainUnavailable
from jarvis.brain.claude import ClaudeBrain, classify_error
from jarvis.brain.keys import ApiKeyStore
from jarvis.brain.tools import ToolRegistry
from jarvis.config import CHOICES, ConfigStore, is_live
from jarvis.core.bus import Bus
from jarvis.memory.store import Memory
from jarvis.tools.front import Services, build_front_tools

KEY = "sk-ant-api03-inventata-0123456789abcdef"


def sealer():
    """DPAPI finto: rovescia i byte (basta che il file non contenga la chiave in chiaro)."""
    return (lambda b: b"S" + b[::-1]), (lambda b: b[1:][::-1])


def store_at(folder, environ=None) -> ApiKeyStore:
    protect, unprotect = sealer()
    return ApiKeyStore(folder, environ or {}, protect, unprotect, env_var=claude_cli.KEY_VAR, what="Claude")


@pytest.fixture(autouse=True)
def fresh_account():
    before = claude_cli.ACCOUNT
    yield
    claude_cli.ACCOUNT = before


# --- la cassaforte ---


def test_a_saved_key_is_sealed_and_comes_back(tmp_path, caplog):
    keys = store_at(tmp_path / "claude")
    assert keys.get() is None and not keys.stored()
    with caplog.at_level(logging.DEBUG):
        keys.save(f"  {KEY}\n")
    assert keys.stored() and keys.get() == KEY
    assert KEY.encode() not in keys.sealed.read_bytes() and not keys.plain.exists()
    assert KEY not in caplog.text
    keys.save("sk-ant-api03-un-altra-chiave-000000")  # una chiave nuova prende il posto della vecchia
    assert keys.get() == "sk-ant-api03-un-altra-chiave-000000"
    assert not list(keys.folder.glob("*.tmp"))


def test_a_key_that_cannot_be_sealed_is_not_saved_in_clear(tmp_path):
    def broken(_b):
        raise OSError("DPAPI non disponibile")

    keys = ApiKeyStore(tmp_path / "claude", {}, broken, broken, env_var=claude_cli.KEY_VAR, what="Claude")
    with pytest.raises(OSError):
        keys.save(KEY)
    assert not keys.stored() and not (tmp_path / "claude" / "chiave.bin").exists()


def test_claude_keys_read_the_anthropic_variable_too(tmp_path):
    keys = claude_cli.claude_keys(tmp_path / "claude", environ={"ANTHROPIC_API_KEY": KEY})
    assert keys.get() == KEY and keys.folder == tmp_path / "claude"


# --- il modo di pagare, a ogni sessione ---


def test_subscription_blanks_any_key_left_on_the_pc(tmp_path):
    claude_cli.ACCOUNT = claude_cli.Account(lambda: "abbonamento", store_at(tmp_path, {"ANTHROPIC_API_KEY": KEY}))
    env = claude_cli.session_env()
    assert env["ANTHROPIC_API_KEY"] == ""  # vuota: Claude Code usa il login di claude.ai
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false" and env["DISABLE_AUTOUPDATER"] == "1"


def test_the_api_key_reaches_each_new_session(tmp_path):
    access = ["chiave"]
    keys = store_at(tmp_path)
    keys.save(KEY)
    claude_cli.ACCOUNT = claude_cli.Account(lambda: access[0], keys)
    assert claude_cli.session_env(MAX_MCP_OUTPUT_TOKENS="100000") == {
        **claude_cli.ISOLATED_ENV, "ANTHROPIC_API_KEY": KEY, "MAX_MCP_OUTPUT_TOKENS": "100000"}
    access[0] = "abbonamento"  # cambiato: la sessione dopo torna all'abbonamento
    assert claude_cli.session_env()["ANTHROPIC_API_KEY"] == ""


def test_key_chosen_but_missing_stays_on_the_subscription_and_says_it_once(tmp_path, caplog):
    claude_cli.ACCOUNT = claude_cli.Account(lambda: "chiave", store_at(tmp_path))
    with caplog.at_level(logging.WARNING, logger="jarvis.brain"):
        assert claude_cli.session_env()["ANTHROPIC_API_KEY"] == ""
        assert claude_cli.session_env()["ANTHROPIC_API_KEY"] == ""
    assert caplog.text.count("la chiave non c'è") == 1


def test_the_voice_brain_and_the_workers_use_it(tmp_path, monkeypatch):
    keys = store_at(tmp_path)
    keys.save(KEY)
    claude_cli.ACCOUNT = claude_cli.Account(lambda: "chiave", keys)
    monkeypatch.setattr("jarvis.brain.claude.find_cli", lambda explicit=None: "C:/finto/claude.exe")
    opts = ClaudeBrain("prompt", ToolRegistry(), "claude-opus-5-5")._options()
    assert opts.env["ANTHROPIC_API_KEY"] == KEY and opts.env["MAX_MCP_OUTPUT_TOKENS"]
    from jarvis.tasks import claude_worker
    from jarvis.tasks.manager import Task
    from jarvis.tasks.safety import SafetyPolicy

    monkeypatch.setattr(claude_worker, "find_cli", lambda explicit=None: "C:/finto/claude.exe")
    w = claude_worker.ClaudeWorker(Task(1, "t", "g", tmp_path), "Andrea", "m", "low", SafetyPolicy([tmp_path]),
                                   env_extra={"JARVIS_ENGINE_DIR": "E:/motori"})
    env = w._options().env
    assert env["ANTHROPIC_API_KEY"] == KEY and env["JARVIS_ENGINE_DIR"] == "E:/motori"


# --- senza accesso si passa al cervello dopo ---


@pytest.mark.parametrize("err", [
    "Invalid API key · Please run /login",
    "API Error: 401 {\"type\":\"error\",\"error\":{\"type\":\"authentication_error\",\"message\":\"invalid x-api-key\"}}",
    "OAuth token has expired. Please obtain a new token or refresh your existing token.",
])
def test_no_access_pauses_claude_for_a_while(err):
    before = time.time()
    e = classify_error(err)
    assert isinstance(e, BrainUnavailable) and e.scope == "provider"
    assert before + 290 <= e.retry_at <= time.time() + 310


def test_an_ordinary_error_is_still_ordinary():
    assert classify_error("Tool input non valido") is None


# --- a voce ---


@pytest.fixture
def tools(tmp_path, monkeypatch):
    keys = store_at(tmp_path / "claude")
    monkeypatch.setattr(claude_cli, "claude_keys", lambda *a, **k: keys)
    store = ConfigStore(tmp_path / "config.yaml")
    s = Services(Bus(), store, Memory(tmp_path / "m.db"), None, None, restart=lambda: "riavvio in arrivo")
    return build_front_tools(s), store, keys


async def test_switching_to_the_key_by_voice_needs_a_key_already_saved(tools):
    reg, store, keys = tools
    assert CHOICES["brain.claude_access"] == {"abbonamento", "chiave"}
    assert store.get("brain.claude_access") == "abbonamento" and not is_live("brain.claude_access")
    out = await reg.call("impostazione", {"chiave": "accesso_claude", "valore": "chiave API"})
    assert out.startswith("Errore") and "mai a voce" in out and store.get("brain.claude_access") == "abbonamento"
    keys.save(KEY)
    out = await reg.call("impostazione", {"chiave": "accesso_claude", "valore": "a consumo"})
    assert store.get("brain.claude_access") == "chiave" and "riavvio" in out
    assert KEY not in out
    await reg.call("impostazione", {"chiave": "brain.claude_access", "valore": "l'abbonamento"})
    assert store.get("brain.claude_access") == "abbonamento"
    out = await reg.call("impostazione", {"chiave": "accesso_claude", "valore": "boh"})
    assert out.startswith("Errore") and "abbonamento o chiave" in out


def test_a_new_key_wins_over_an_old_plain_file(tmp_path):
    keys = store_at(tmp_path / "claude")
    keys.folder.mkdir(parents=True)
    keys.plain.write_text("sk-ant-api03-vecchia-in-chiaro-000000", encoding="utf-8")
    keys.save(KEY)
    assert keys.get() == KEY and not keys.plain.exists()


def test_jarvis_ties_the_sessions_to_its_setting(monkeypatch):
    from jarvis import paths
    from jarvis.app import Jarvis
    from jarvis.audio.devices import SimulatedRoom

    j = Jarvis(backend=SimulatedRoom())
    try:
        assert claude_cli.ACCOUNT.keys.folder == paths.DATA_DIR / "claude"
        monkeypatch.setattr(j.cfg.brain, "claude_access", "chiave")
        assert claude_cli.ACCOUNT.access() == "chiave"  # letta a ogni sessione, non fissata all'avvio
        monkeypatch.setattr(j.cfg.brain, "claude_access", "abbonamento")
        assert claude_cli.session_env()["ANTHROPIC_API_KEY"] == ""
    finally:
        j.memory.close()

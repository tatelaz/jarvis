"""La presentazione a voce dopo il primo avvio: quando parte, i passi, lo strumento del cervello."""

import json

import pytest

from jarvis.config import ConfigStore
from jarvis.core.bus import Bus
from jarvis.memory.store import Memory
from jarvis.setup.welcome import STEPS, Welcome
from jarvis.tools.front import Services, build_front_tools


@pytest.fixture
def welcome(tmp_path):
    w = Welcome(tmp_path / "presentazione.json", tmp_path / "fatto.json")
    (tmp_path / "fatto.json").write_text("{}", encoding="utf-8")  # il primo avvio grafico è finito
    return w


def test_only_after_the_graphical_setup(tmp_path):
    w = Welcome(tmp_path / "p.json", tmp_path / "fatto.json")
    assert not w.due()  # una copia installata a mano (o la tua di sempre): niente presentazione
    (tmp_path / "fatto.json").write_text("{}", encoding="utf-8")
    assert w.due()


def test_steps_are_marked_and_the_end_comes_by_itself(welcome):
    assert welcome.remaining() == list(STEPS)
    for step in STEPS[:-1]:
        assert "passi rimasti" in welcome.mark(step, "fatto")
    assert welcome.mark(STEPS[-1], "saltato") == "presentazione finita"
    assert not welcome.due() and welcome.state()["finished"]
    assert "finita" in welcome.listing()


def test_unknown_steps_and_marks_are_refused(welcome):
    with pytest.raises(ValueError, match="passo sconosciuto"):
        welcome.mark("password", "fatto")
    with pytest.raises(ValueError, match="segno sconosciuto"):
        welcome.mark("nome", "forse")


def test_postponed_until_the_next_start(welcome, tmp_path):
    welcome.mark("nome", "fatto")
    welcome.postponed = True
    assert not welcome.due()
    again = Welcome(tmp_path / "presentazione.json", tmp_path / "fatto.json")  # Jarvis riacceso
    assert again.due() and again.remaining() == list(STEPS[1:])


def test_english_skips_the_formal_you(tmp_path):
    w = Welcome(tmp_path / "p.json", tmp_path / "f.json", language=lambda: "en")
    assert "registro" not in w.remaining()
    for step in ("nome", "voce", "app"):
        w.mark(step, "fatto")
    assert w.mark("abitudini", "fatto") == "presentazione finita"


def test_first_event_and_resumed_event(welcome):
    first = welcome.event_text("Andrea")
    assert "prima volta" in first and "Presentati" in first and "Andrea" in first
    assert all(step in first for step in STEPS)
    assert "parola d'ordine" in first and "mai chiederla" in first  # la parola d'ordine non si chiede a voce
    welcome.mark("nome", "fatto")
    resumed = welcome.event_text("Andrea")
    assert "rimasta a metà" in resumed and "nome:" not in resumed and "voce:" in resumed


def test_restart_from_scratch(welcome):
    welcome.mark("nome", "fatto")
    welcome.finish()
    assert not welcome.due()
    welcome.restart()
    assert welcome.due() and welcome.remaining() == list(STEPS)


@pytest.mark.parametrize("content", ["{rotto", "[1, 2]", '{"steps": {"nome": "boh", "x": "fatto"}, "finished": 1}'])
def test_damaged_state_is_read_safely(welcome, content):
    welcome.path.write_text(content, encoding="utf-8")
    st = welcome.state()
    assert st["steps"] == {} and st["finished"] in (False, True)


@pytest.fixture
def tool(welcome, tmp_path):
    s = Services(Bus(), ConfigStore(tmp_path / "c.yaml"), Memory(tmp_path / "m.db"), None, None, welcome=welcome)
    reg = build_front_tools(s)

    async def call(**args):
        return await reg.call("presentazione", args)
    return call


async def test_tool_lists_marks_and_ends(tool, welcome):
    assert "nome:" in await tool(azione="stato")
    assert "passi rimasti" in await tool(azione="fatto", passo="nome")
    assert "passi rimasti" in await tool(azione="salta", passo="registro")
    assert welcome.state()["steps"] == {"nome": "fatto", "registro": "saltato"}
    assert (await tool(azione="fatto", passo="password")).startswith("Errore")
    assert "rimandata" in await tool(azione="rimanda")
    assert not welcome.due()
    assert await tool(azione="fine") == "presentazione finita"
    assert json.loads(welcome.path.read_text(encoding="utf-8"))["finished"] is True
    assert "nome:" in await tool(azione="ricomincia") and welcome.due()


def test_the_tool_is_only_for_the_owner(welcome, tmp_path):
    from jarvis.identity.guard import OWNER_ONLY

    s = Services(Bus(), ConfigStore(tmp_path / "c.yaml"), Memory(tmp_path / "m.db"), None, None, welcome=welcome)
    reg = build_front_tools(s)
    assert {t.group for t in reg if t.name == "presentazione"} <= OWNER_ONLY


def test_event_at_start_only_when_due(tmp_path):
    w = Welcome(tmp_path / "p.json", tmp_path / "fatto.json")
    assert w.start_event("Andrea") is None
    (tmp_path / "fatto.json").write_text("{}", encoding="utf-8")
    assert "prima volta" in w.start_event("Andrea")
    w.finish()
    assert w.start_event("Andrea") is None

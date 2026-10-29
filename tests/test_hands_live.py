"""Le mani dei sotto-agenti con Claude vero: un compito innocuo per ciascuna.

Eseguire con: pytest -m claude tests/test_hands_live.py      (quello del desktop vuole anche -m desktop)
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.claude, pytest.mark.slow]

spec = importlib.util.spec_from_file_location("hands_live", ROOT / "scripts" / "hands_live.py")
hands_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hands_live)


def _ok(r: dict) -> str:
    assert r["errore"] is None, r
    assert r["conferme_chieste"] == [], r  # un compito innocuo non deve chiedere nulla
    return r["risultato"].lower()


async def test_disposable_browser():
    r = await hands_live.run("browser", "medium")
    assert "example domain" in _ok(r)
    assert any(p.startswith("browser:") for p in r["passi"]), r


async def test_user_browser():
    r = await hands_live.run("mio_browser", "medium")
    assert "example domain" in _ok(r)
    assert any(p.startswith("il tuo browser:") for p in r["passi"]), r


@pytest.mark.desktop
async def test_desktop_calculator():
    r = await hands_live.run("desktop", "medium")
    text = _ok(r)
    assert "1591" in text.replace(".", "").replace(" ", ""), r
    assert any(p.startswith("computer:") for p in r["passi"]), r

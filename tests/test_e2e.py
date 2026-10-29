"""Prova completa con motori veri e Claude vero (stanza simulata al posto del microfono).

Esclusa dai controlli rapidi: richiede i motori, la GPU e consuma un po' di abbonamento Claude.
Eseguire con: pytest -m "engine and claude" tests/test_e2e.py
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.engine, pytest.mark.claude, pytest.mark.slow]

spec = importlib.util.spec_from_file_location("e2e", ROOT / "scripts" / "e2e.py")
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


async def test_full_voice_round_trip():
    r = await e2e.main(["Jarvis, quanto fa diciassette per ventitré?"], echo=0.0, gap=12.0)
    assert r["errori"] == []
    assert r["trascritto_utente"], r
    heard_question = r["trascritto_utente"][0].lower()
    assert "17" in heard_question or "diciassette" in heard_question
    answer = (r["jarvis_sentito"] + " " + " ".join(r["risposte_testo"])).lower()
    assert "391" in answer or "novantuno" in answer
    m = r["metriche_turni"][0]
    assert m["first_audio_ms"] is not None and m["first_audio_ms"] < 3500, m


async def test_follow_up_without_name_and_echo_from_speakers():
    r = await e2e.main(["Jarvis, come si chiama la capitale della Francia?", "E quella della Spagna?"],
                       echo=0.5, gap=12.0)
    assert len(r["trascritto_utente"]) == 2, r
    answer = " ".join(r["risposte_testo"]).lower()
    assert "parigi" in answer and "madrid" in answer
    assert r["aec_attivo"] is True

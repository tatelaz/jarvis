"""Il README in italiano e in inglese: si passa dall'uno all'altro dall'inizio, hanno le stesse parti e nessun
collegamento rotto (le ancore come le calcola GitHub)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IT, EN = (ROOT / "README.md").read_text(encoding="utf-8"), (ROOT / "README.en.md").read_text(encoding="utf-8")


def slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_each_one_starts_with_the_way_to_the_other():
    assert IT.splitlines()[2] == "**Italiano** · [English](README.en.md)"
    assert EN.splitlines()[2] == "[Italiano](README.md) · **English**"


def test_same_parts_in_both_languages():
    def parts(text):
        return [len(m) for m in re.findall(r"^(#{2,4}) ", text, re.M)]

    assert parts(IT) == parts(EN) and len(parts(IT)) >= 12
    assert IT.count("```") == EN.count("```") and IT.count("\n- ") == EN.count("\n- ")


def test_no_broken_link():
    for name, text in (("README.md", IT), ("README.en.md", EN)):
        heads = {slug(h) for h in re.findall(r"^#{1,4} (.+)$", text, re.M)}
        assert [a for a in re.findall(r"\]\(#([^)]+)\)", text) if a not in heads] == [], name
        files = re.findall(r"\]\((?!https?:|#)([^)#]+)", text)
        assert files and [f for f in files if not (ROOT / f).exists()] == [], name


def test_never_suggests_attaching_the_raw_log_publicly():
    """Il registro di Jarvis contiene le conversazioni: in una issue pubblica non va (c'è "segnala un problema")."""
    assert "Non allegare a una issue pubblica il registro" in IT and "Do not attach the raw" in EN

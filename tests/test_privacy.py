"""Niente dati personali nel repository: nomi, account e percorsi di chi usa questa copia.

L'elenco delle parole sta in privato/parole.txt (una per riga, # per i commenti), fuori dal repository: l'elenco stesso
sarebbe un dato personale. Senza quel file il controllo del repository si salta; quello della funzione gira sempre.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORDS = ROOT / "privato" / "parole.txt"


def private_words(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [w for w in lines if w and not w.startswith("#")]


def leaks(words: list[str], files: dict[str, str]) -> list[str]:
    """"file:riga: parola" per ogni parola privata trovata (parola intera, maiuscole indifferenti)."""
    if not words:
        return []
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.I)
    found = []
    for name, text in files.items():
        for n, line in enumerate(text.splitlines(), 1):
            if m := pattern.search(line):
                found.append(f"{name}:{n}: {m.group(1)}")
    return found


def tracked_files() -> dict[str, str]:
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    out = {}
    for name in names.decode("utf-8").split("\0"):
        path = ROOT / name
        if not name or not path.is_file():
            continue
        try:
            out[name] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # immagini, icone e altri file binari
            continue
    return out


def test_leaks_finds_whole_words_in_any_case(tmp_path):
    words_file = tmp_path / "parole.txt"
    words_file.write_text("# commento\nMario Rossi\nmrossi\n\n", encoding="utf-8")
    words = private_words(words_file)
    assert words == ["Mario Rossi", "mrossi"]
    files = {"a.py": "x = 1\n# scritto da MARIO ROSSI\n", "b.md": "account: mrossi@example.com", "c.txt": "mrossini"}
    assert leaks(words, files) == ["a.py:2: MARIO ROSSI", "b.md:1: mrossi"]  # "mrossini" è un'altra parola
    assert private_words(tmp_path / "manca.txt") == [] and leaks([], files) == []


@pytest.mark.skipif(not WORDS.exists(), reason="nessun privato/parole.txt in questa copia")
def test_no_private_words_in_the_repository():
    assert leaks(private_words(WORDS), tracked_files()) == []

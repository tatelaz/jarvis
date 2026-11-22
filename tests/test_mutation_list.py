"""Le mutazioni di scripts/mutation_check.py devono trovare ancora il testo che cambiano: dopo un cambio del codice
una mutazione rimasta indietro non controlla più niente, e se ne accorgeva solo il giro completo (il 22/11 erano 7)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mutation_check  # noqa: E402

MUTATIONS = mutation_check.MUTATIONS


@pytest.mark.parametrize("m", MUTATIONS, ids=[m[4] for m in MUTATIONS])
def test_every_mutation_still_finds_its_text(m):
    rel, old, new, test, _desc = m
    src = (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert old in src, f"testo da mutare sparito da {rel}"
    assert old != new and (ROOT / test).is_file()


def test_descriptions_are_unique():
    descs = [m[4] for m in MUTATIONS]
    assert len(descs) == len(set(descs))

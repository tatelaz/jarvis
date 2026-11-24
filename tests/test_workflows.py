"""I flussi di GitHub Actions (.github/workflows): le prove di ogni commit e l'installatore costruito dal repository,
che crea la release con i file che cerca l'aggiornatore. Qui si controllano le regole; il giro vero gira su GitHub."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / ".github" / "workflows"


def read(name: str) -> str:
    return (FLOWS / name).read_text(encoding="utf-8")


def test_every_commit_runs_the_same_checks_as_check_sh():
    flow = read("prove.yml")
    check = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    markers = re.search(r'-m "([^"]+)"', check)[1]
    assert f'-m "{markers}"' in flow and "-W error" in flow and "ruff check jarvis tests scripts" in flow
    assert "uv run python -m pytest" in flow  # "uv run pytest" non trova jarvis: provato su GitHub il 24/11
    assert "windows-latest" in flow and "uv sync --frozen" in flow and "fetch-depth: 0" in flow


def test_the_installer_is_built_from_the_repository_and_released_with_what_the_updater_needs():
    flow = read("installatore.yml")
    assert 'tags: ["v*"]' in flow and "workflow_dispatch" in flow and "fetch-depth: 0" in flow
    assert "uv run python scripts/build_installer.py" in flow and "choco install innosetup" in flow
    # i nomi che cerca jarvis/updates.py, e il tag uguale alla versione del progetto
    assert '"build/Jarvis-Setup-$v.exe" "build/Jarvis-$v.bundle"' in flow and "--verify-tag" in flow
    assert '"v$v" -ne $env:TAG' in flow and "docs/versioni/$v.md" in flow
    assert "contents: write" in flow and "GH_TOKEN: ${{ github.token }}" in flow
    # uv fissato, lo stesso nei due flussi: l'ultima versione (0.13.9) è arrivata da sola nella 0.1.0 e, dentro Inno
    # Setup, non preparava Python sul PC vero (provato il 24/11: la 0.10.0 sì, con e senza RedirectionGuard)
    pins = [re.search(r'setup-uv@v6\n\s+with:\n(?:\s+#.*\n)*\s+version: "(\d+\.\d+\.\d+)"', read(f)) for f in
            ("installatore.yml", "prove.yml")]
    assert all(pins) and pins[0][1] == pins[1][1] == "0.10.0"


def test_no_value_from_github_goes_straight_into_a_script():
    """Il nome del tag passa da una variabile d'ambiente: scritto dentro run: potrebbe diventare un comando."""
    for name in ("prove.yml", "installatore.yml"):
        runs = re.findall(r"run: \|?\n?((?:.*\n)*?)(?=\s*-\s|\Z)", read(name))
        assert not any("${{" in r for r in runs), name


def test_the_current_version_has_its_notes():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    notes = (ROOT / "docs" / "versioni" / f"{version}.md").read_text(encoding="utf-8")
    assert f"Jarvis-Setup-{version}.exe" in notes and "PC protetto" in notes

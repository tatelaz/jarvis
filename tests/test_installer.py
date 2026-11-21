"""L'installatore: cosa ci finisce dentro e le regole scritte in installer/jarvis.iss."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_installer  # noqa: E402

ISS = (ROOT / "installer" / "jarvis.iss").read_text(encoding="utf-8")


def test_only_repository_files_go_in(tmp_path):
    n = build_installer.code_files(tmp_path)
    got = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert n == len(got) > 100
    assert {"avvia_jarvis.pyw", "pyproject.toml", "uv.lock", "jarvis/app.py", "jarvis/ui/jarvis.ico",
            "jarvis/speech/voci/base-it/ref.wav"} <= got
    leaked = {p for p in got if p.startswith(("design/", "privato/", "data/", "models/", "engine/", ".venv/"))}
    assert leaked == set()  # niente dati, modelli, cartelle private o prove di grafica


def test_version_comes_from_the_project():
    assert re.fullmatch(r"\d+\.\d+\.\d+", build_installer.version())


def test_installs_for_the_user_without_administrator_rights():
    assert "PrivilegesRequired=lowest" in ISS
    assert r"DefaultDirName={localappdata}\Programs\Jarvis" in ISS


def test_libraries_are_the_locked_ones_and_live_inside_jarvis():
    sync = ISS[ISS.index("function SyncLibraries"):ISS.index("procedure CurStepChanged")]
    assert "sync --frozen --no-dev" in sync  # le versioni esatte di uv.lock, senza gli strumenti di sviluppo
    assert r"UV_PYTHON_INSTALL_DIR=' + ExpandConstant('{app}\tools\python')" in sync  # si disinstalla con Jarvis
    assert "SW_HIDE" in sync  # niente console nera


def test_shortcuts_start_jarvis_like_the_autostart():
    for line in (ln for ln in ISS.splitlines() if ln.startswith("Name: \"{auto")):
        assert r'Filename: "{app}\.venv\Scripts\pythonw.exe"' in line and "avvia_jarvis.pyw" in line


def test_silent_uninstall_never_deletes_data():
    code = ISS[ISS.index("procedure CurUninstallStepChanged"):]
    assert "and not UninstallSilent" in code
    assert code.index("MsgBox(CustomMessage('KeepData')") < code.index(r"DelTree(ExpandConstant('{app}\data')")


def test_messages_in_both_languages():
    section = ISS[ISS.index("[CustomMessages]"):ISS.index("[Tasks]")]
    keys = {lang: {m.group(1) for m in re.finditer(rf"^{lang}\.(\w+)=", section, re.M)} for lang in ("it", "en")}
    assert keys["it"] == keys["en"] and len(keys["it"]) >= 5
    assert 'Name: "it"' in ISS and 'Name: "en"' in ISS

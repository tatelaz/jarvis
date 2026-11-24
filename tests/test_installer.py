"""L'installatore: cosa ci finisce dentro e le regole scritte in installer/jarvis.iss."""

import re
import sys
from pathlib import Path

import pytest

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
    leaked = {p for p in got if p.startswith(("privato/", "data/", "models/", "engine/", ".venv/"))}
    assert leaked == set()  # niente dati, modelli o cartelle private
    import subprocess

    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
                  .splitlines())
    assert got == {p for p in tracked if (ROOT / p).is_file()}  # né uno in più né uno in meno: è il commit, tale e quale


def test_the_history_goes_in_as_a_bundle(tmp_path):
    """La copia installata diventa un repository dal bundle (jarvis/localrepo.py): deve contenere il commit di oggi."""
    import subprocess

    b = build_installer.history(tmp_path / "jarvis.bundle")
    heads = subprocess.run(["git", "bundle", "list-heads", str(b)], capture_output=True, text=True, check=True).stdout
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert head.strip() in heads and "HEAD" in heads and b.stat().st_size > 100_000
    assert r'Name: "{app}\.git"' in ISS[ISS.index("[UninstallDelete]"):]  # e se ne va con la disinstallazione


def test_the_real_project_installed_becomes_a_clean_repository(tmp_path):
    """Come dopo l'installatore: file del commit + uv + bundle. Al primo avvio deve essere identico al commit (a capo
    compresi), o l'auto-miglioramento e gli aggiornamenti la vedrebbero sempre "modificata"."""
    import subprocess

    from jarvis import localrepo

    app = tmp_path / "Jarvis"
    build_installer.code_files(app)
    build_installer.history(app / build_installer.BUNDLE)
    (app / "tools").mkdir()
    (app / "tools" / "uv.exe").write_bytes(b"uv")
    out = localrepo.ensure_repo(app)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.what == "creato" and out.commit == head.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=app, capture_output=True, text=True, check=True)
    assert status.stdout == ""


def test_the_visual_cpp_runtime_goes_in_and_never_an_old_one(tmp_path):
    """Senza msvcp140.dll onnxruntime e soxr non si caricano (Windows Sandbox, 23/11): l'installatore lo porta in
    tools/vcrt (jarvis/vcrt.py), ma mai uno più vecchio della 14.40, con cui onnxruntime si pianta."""
    from jarvis import vcrt

    src = tmp_path / "System32"
    src.mkdir()
    for n in ("vcruntime140.dll", "msvcp140.dll", "msvcp140_1.dll", "kernel32.dll"):
        (src / n).write_bytes(b"MZ")
    n = build_installer.runtime_files(tmp_path / "vcrt", src, version=lambda p: (14, 51, 36247, 0))
    assert n == 3 and sorted(p.name for p in (tmp_path / "vcrt").iterdir()) == [
        "msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll"]  # solo la famiglia di Visual C++
    for old in ((14, 38, 33135, 0), None):
        with pytest.raises(SystemExit, match=r"14\.40"):
            build_installer.runtime_files(tmp_path / "vecchio", src, version=lambda p, v=old: v)
    assert not (tmp_path / "vecchio").exists()
    assert set(vcrt.FILES) >= {"msvcp140.dll", "msvcp140_1.dll", "vcruntime140_1.dll"}  # quelle che onnxruntime chiede
    files = ISS[ISS.index("[Files]"):ISS.index("[Icons]")]
    assert r'Source: "{#Staging}\tools\vcrt\*"; DestDir: "{app}\tools\vcrt"' in files
    assert r'Name: "{app}\tools"' in ISS[ISS.index("[UninstallDelete]"):]  # e se ne va con la disinstallazione


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


def test_before_installing_it_says_what_leaves_the_pc():
    """L'informativa sulla privacy (la chiede anche la firma gratuita di SignPath): in tutte e due le lingue, con le
    segnalazioni e come spegnerle, e a capo CRLF perché il riquadro di Windows le mostri."""
    langs = ISS[ISS.index("[Languages]"):ISS.index("[CustomMessages]")]
    for lang, words in (("it", ("segnalazioni", "rapporti delle cadute mai", "aggiornamenti spenti", "l'audio non esce")),
                        ("en", ("reports to the developers", "crash reports never", "updates off", "never leaves"))):
        assert f'InfoBeforeFile: "privacy-{lang}.txt"' in langs
        raw = (ROOT / "installer" / f"privacy-{lang}.txt").read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
        text = raw.decode("utf-8-sig")
        assert all(w in text for w in words), lang
    assert "installer/*.txt text eol=crlf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_name_and_version_are_written_in_the_setup_file():
    setup = ISS[ISS.index("[Setup]"):ISS.index("[Languages]")]
    for line in ("VersionInfoVersion={#AppVersion}", "VersionInfoProductName=Jarvis",
                 "VersionInfoProductVersion={#AppVersion}", "AppPublisherURL=https://github.com/tatelaz/jarvis"):
        assert line in setup

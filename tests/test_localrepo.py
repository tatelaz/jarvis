"""La copia installata come repository git (jarvis/localrepo.py), con git vero in cartelle temporanee: un progetto
"di GitHub" che fa le release (bundle) e una cartella "installata" come la lascia l'installatore."""

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from jarvis import localrepo as L

GITIGNORE = ".venv/\ndata/\n/tools/\n/jarvis.bundle\n/unins*.exe\n/unins*.dat\n"


def run(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=Prova", "-c", "user.email=prova@example.com", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


class Project:
    """Il repository di chi sviluppa: ogni release è un commit più il suo bundle."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir()
        run(root, "init", "--quiet", "-b", "main")
        # .gitattributes come quello vero: senza, git archive su Windows (autocrlf) scrive CRLF e la copia installata
        # risulterebbe tutta "modificata"
        self.write({".gitattributes": "* text=auto eol=lf\n*.bat text eol=crlf\n",
                    ".gitignore": GITIGNORE, "avvia_jarvis.pyw": "print('jarvis')\n", "avvia.bat": "@echo off\r\n",
                    "pyproject.toml": '[project]\nversion = "0.1.0"\n', "jarvis/voce.py": "VELOCITA = 1.0\n",
                    "jarvis/memoria.py": "GIORNI = 30\n"})
        self.commit("versione 0.1.0")

    def write(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text, encoding="utf-8", newline="\n")

    def commit(self, msg: str) -> str:
        run(self.root, "add", "-A")
        run(self.root, "commit", "--quiet", "-m", msg)
        return run(self.root, "rev-parse", "HEAD")

    def bundle(self, dest: Path) -> Path:
        run(self.root, "bundle", "create", str(dest), "HEAD")
        return dest

    def install(self, app: Path) -> Path:
        """Come l'installatore: i file del commit, uv in tools/, il bundle, il disinstallatore."""
        data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=self.root, capture_output=True,
                              check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for m in tar.getmembers():
                if m.isfile():
                    (app / m.name).parent.mkdir(parents=True, exist_ok=True)
                    (app / m.name).write_bytes(tar.extractfile(m).read())
        (app / "tools").mkdir(parents=True, exist_ok=True)
        (app / "tools" / "uv.exe").write_bytes(b"uv")
        (app / "unins000.exe").write_bytes(b"x")
        self.bundle(app / "jarvis.bundle")
        return app


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path / "github")


def head(app):
    return run(app, "rev-parse", "HEAD")


def clean(app):
    return run(app, "status", "--porcelain") == ""  # anche i file non tracciati: tools/, disinstallatore, bundle


def test_the_installed_copy_becomes_a_repository_at_the_release_commit(project, tmp_path):
    release = run(project.root, "rev-parse", "HEAD")
    app = project.install(tmp_path / "Jarvis")
    out = L.ensure_repo(app)
    assert (out.what, out.commit) == ("creato", release) and out.said() is None
    assert head(app) == release and run(app, "branch", "--show-current") == "main" and clean(app)
    assert not (app / "jarvis.bundle").exists()  # la storia è nel repository: il file non serve più
    name = subprocess.run(["git", "config", "--local", "user.name"], cwd=app, capture_output=True, text=True).stdout
    assert name.strip() == "Jarvis"  # l'auto-miglioramento fa commit suoi
    assert L.ensure_repo(app).what == ""  # le volte dopo: niente


def test_the_developer_copy_is_never_touched(project, tmp_path):
    app = project.install(tmp_path / "Jarvis")
    (app / "tools" / "uv.exe").unlink()  # senza uv dell'installatore è una copia presa con git da chi sviluppa
    assert not L.installed_copy(app) and L.ensure_repo(app).what == "" and not (app / ".git").exists()


def test_reinstalling_over_an_untouched_copy_just_moves_on(project, tmp_path):
    app = project.install(tmp_path / "Jarvis")
    L.ensure_repo(app)
    project.write({"jarvis/memoria.py": "GIORNI = 60\n", "pyproject.toml": '[project]\nversion = "0.2.0"\n'})
    new = project.commit("versione 0.2.0")
    project.install(app)  # l'installatore nuovo sopra quello vecchio
    out = L.ensure_repo(app)
    assert (out.what, head(app)) == ("allineato", new) and clean(app) and out.said() is None


def test_reinstalling_over_jarvis_own_changes_keeps_them_in_a_branch(project, tmp_path):
    app = project.install(tmp_path / "Jarvis")
    L.ensure_repo(app)
    (app / "jarvis" / "voce.py").write_text("VELOCITA = 1.2  # più veloce, chiesto a voce\n", encoding="utf-8")
    mine = run(app, "commit", "--quiet", "-am", "miglioria: voce più veloce") or head(app)
    project.write({"jarvis/memoria.py": "GIORNI = 60\n"})
    new = project.commit("versione 0.2.0")
    project.install(app)
    out = L.ensure_repo(app, clock=lambda fmt: "20261123-1200")
    assert out.what == "salvate" and out.backup == "mie-modifiche/20261123-1200"
    assert head(app) == new and clean(app)
    assert run(app, "rev-parse", out.backup) == mine  # la modifica di Jarvis non è persa
    assert "mie-modifiche/20261123-1200" in out.said()


def test_what_an_update_needs(project, tmp_path):
    app = project.install(tmp_path / "Jarvis")
    L.ensure_repo(app)
    old = project.bundle(tmp_path / "vecchia.bundle")
    project.write({"jarvis/memoria.py": "GIORNI = 60\n"})
    new_commit = project.commit("versione 0.2.0")
    new = project.bundle(tmp_path / "nuova.bundle")
    assert L.plan_update(app, old).how == "dentro"  # quella che ha già
    plan = L.plan_update(app, new)
    assert (plan.how, plan.commit) == ("avanti", new_commit)  # nessuna modifica sua: si va avanti e basta
    (app / "jarvis" / "voce.py").write_text("VELOCITA = 1.2\n", encoding="utf-8")
    run(app, "commit", "--quiet", "-am", "miglioria")
    assert L.plan_update(app, new).how == "unione"  # con le sue modifiche: va unita
    assert head(app) != new_commit  # pianificare non cambia niente


def test_test_tools_are_downloaded_only_when_missing(project, tmp_path):
    app = project.install(tmp_path / "Jarvis")
    calls = []

    def uv(cmd, **kw):
        calls.append((cmd, kw["env"]["UV_PYTHON_INSTALL_DIR"]))
        scripts = app / ".venv" / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        for exe in ("pytest.exe", "ruff.exe"):
            (scripts / exe).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert L.ensure_test_tools(app, run=uv) == "scaricati"
    cmd, pydir = calls[0]
    assert cmd[0].endswith("uv.exe") and "sync" in cmd and "--frozen" in cmd and "--no-dev" not in cmd
    assert pydir == str(app / "tools" / "python")  # come l'installatore: tutto dentro la cartella di Jarvis
    assert L.ensure_test_tools(app, run=uv) == "" and len(calls) == 1  # già scaricati: niente

    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "rete assente")

    other = project.install(tmp_path / "Altra")
    with pytest.raises(L.RepoError, match="rete assente"):
        L.ensure_test_tools(other, run=broken)
    (other / "tools" / "uv.exe").unlink()
    assert L.ensure_test_tools(other, run=broken) == ""  # la copia di sviluppo ha i suoi (uv sync con il gruppo dev)

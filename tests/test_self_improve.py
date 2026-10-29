"""Auto-miglioramento con un vero repository git e un sotto-agente finto che lavora nella copia."""

import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.tasks.manager import Task, WorkerHooks
from jarvis.tasks.self_improve import SelfImproveWorker

# il "controllo" del progetto finto: verde se i file .txt dicono tutti "ok" e se uno.txt e due.txt non convivono
CHECK = """import pathlib, sys
files = list(pathlib.Path('.').glob('*.txt'))
bad = [f.name for f in files if f.read_text().strip() != 'ok']
both = all(pathlib.Path(n).exists() for n in ('uno.txt', 'due.txt'))
print('rossi:', bad, 'insieme:', both)
sys.exit(1 if bad or both else 0)
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "jarvis"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "test@example.com")
    git(r, "config", "user.name", "test")
    (r / "check.py").write_text(CHECK)
    (r / "base.txt").write_text("ok")
    (r / ".gitignore").write_text("data/\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r


class FakeAgent:
    """Fa nella copia quello che gli si dice, e salva nel ramo."""

    def __init__(self, task, worktree, env, files=None, main_change=None, root=None, commit=True):
        self.worktree, self.files, self.main_change, self.root, self.commit = worktree, files or {}, main_change, root, commit
        self.env = env

    async def run(self, task, hooks):
        assert "migliorando il codice di JARVIS" in task.goal and str(self.worktree) in task.goal
        for name, text in self.files.items():
            (self.worktree / name).write_text(text)
        if self.commit and self.files:
            git(self.worktree, "add", "-A")
            git(self.worktree, "commit", "-qm", "miglioria")
        if self.main_change:  # nel frattempo qualcuno salva un'altra modifica nel codice principale
            name, text = self.main_change
            (self.root / name).write_text(text)
            git(self.root, "add", "-A")
            git(self.root, "commit", "-qm", "altro lavoro")
        return "ho aggiunto uno.txt"

    async def send(self, text):
        pass

    async def cancel(self):
        pass


def run(repo, answer=True, **agent):
    asked = []

    async def confirm(text):
        asked.append(text)
        return answer

    task = Task(1, "aggiungi uno", "aggiungi il file uno", repo / "data" / "compito")
    w = SelfImproveWorker(task, lambda t, wt, env: FakeAgent(t, wt, env, root=repo, **agent), "Andrea", root=repo,
                          check_command=[sys.executable, "check.py"])
    import asyncio

    result = asyncio.run(w.run(task, WorkerHooks(progress=lambda t: None, confirm=confirm)))
    branches = git(repo, "branch", "--list", "miglioria/*")
    return result, asked, branches, w


def test_approved_change_is_merged_and_cleaned_up(repo):
    result, asked, branches, w = run(repo, files={"uno.txt": "ok"})
    assert "applicata" in result and "riavvio" in result and len(asked) == 1
    assert (repo / "uno.txt").read_text() == "ok"  # nel codice principale
    assert not w.worktree.exists() and branches == ""  # niente copie né rami avanzati


def test_declined_change_is_discarded(repo):
    result, asked, branches, _ = run(repo, answer=False, files={"uno.txt": "ok"})
    assert len(asked) == 1 and "scartata" in result and not (repo / "uno.txt").exists() and branches == ""


def test_red_checks_never_reach_the_user_or_the_code(repo):
    result, asked, branches, w = run(repo, files={"uno.txt": "rotto"})
    assert "NON applicata" in result and asked == [] and not (repo / "uno.txt").exists()
    assert "miglioria/" in branches and not w.worktree.exists()  # il lavoro resta nel ramo, da guardare


def test_nothing_saved_means_nothing_to_apply(repo):
    result, asked, branches, _ = run(repo, files={"uno.txt": "ok"}, commit=False)
    assert "Nessuna modifica salvata" in result and asked == [] and branches == ""


def test_unsaved_work_in_the_main_folder_is_never_mixed_in(repo):
    (repo / "base.txt").write_text("ok\n")  # modifica non salvata di qualcun altro
    result, asked, branches, _ = run(repo, files={"uno.txt": "ok"})
    assert "non salvati" in result and asked == [] and not (repo / "uno.txt").exists()
    assert (repo / "base.txt").read_text() == "ok\n"  # il lavoro altrui è intatto
    assert "miglioria/" in branches


def test_merge_that_breaks_the_main_code_is_rolled_back_exactly(repo):
    # la copia aggiunge uno.txt (verde da sola), nel frattempo il principale aggiunge due.txt (verde da solo):
    # insieme non vanno. Dopo l'unione i controlli falliscono e si torna al commit di prima.
    result, asked, branches, _ = run(repo, files={"uno.txt": "ok"}, main_change=("due.txt", "ok"))
    head_files = git(repo, "ls-files")
    assert "tornato com'ero" in result and len(asked) == 1
    assert "due.txt" in head_files and "uno.txt" not in head_files and not (repo / "uno.txt").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert "miglioria/" in branches


def test_checks_run_with_git_bash_not_wsl():
    """Dal vivo: "bash" lanciato da Python apriva quello di WSL, che non riceve le variabili d'ambiente."""
    import os

    from jarvis.tasks.self_improve import git_bash

    bash = git_bash()
    assert Path(bash).exists() and "system32" not in bash.lower()
    r = subprocess.run([bash, "-c", "echo $JARVIS_PROVA"], capture_output=True, text=True,
                       env={**os.environ, "JARVIS_PROVA": "arrivata"})
    assert r.stdout.strip() == "arrivata"


def test_confirmation_summary_ends_at_a_sentence():
    from jarvis.tasks.self_improve import short

    text = "Ho aggiunto la frase pronta. " * 5 + "Si nota quando Jarvis riparte dopo il riavvio completo."
    out = short(text, 120)
    assert out.endswith(".") and len(out) <= 120  # dal vivo finiva con "Si nota quando Jarvis ri"
    assert short("una sola parola lunghissima " * 20, 50).endswith("…")
    assert short("Breve.", 300) == "Breve."

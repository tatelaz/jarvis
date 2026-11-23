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


# ---------------------------------------------------------------------- aggiornamento con modifiche locali


def release_and_local(repo, release: dict, local: dict) -> str:
    """La versione nuova (dalla base, come arriva dal bundle) e una modifica locale su main. -> commit della versione."""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "rilascio")
    for name, text in release.items():
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "versione 0.2.0")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    assert git(repo, "rev-parse", "HEAD") == base
    for name, text in local.items():
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "miglioria chiesta a voce")
    return commit


class MergeAgent:
    """Il sotto-agente che sistema l'unione nella copia (o non ci riesce)."""

    def __init__(self, task, worktree, env, fix=None, close=True):
        self.task, self.worktree, self.fix, self.close = task, worktree, fix or {}, close
        self.seen = task.goal

    async def run(self, task, hooks):
        assert "aggiornando il codice di JARVIS" in task.goal and str(self.worktree) in task.goal
        for name, text in self.fix.items():
            (self.worktree / name).write_text(text)
        if self.close:
            git(self.worktree, "add", "-A")
            git(self.worktree, "commit", "-qm", "unione sistemata")
        return "ho tenuto la velocità chiesta e la novità della versione"

    async def send(self, text):
        pass

    async def cancel(self):
        pass


def merge(repo, commit, answer=True, tools=None, **agent):
    import asyncio

    from jarvis.tasks.self_improve import UpdateMergeWorker

    asked, agents, failed = [], [], []

    async def confirm(text):
        asked.append(text)
        return answer

    def make(t, wt, env):
        agents.append(MergeAgent(t, wt, env, **agent))
        return agents[-1]

    task = Task(7, "aggiornamento alla 0.2.0", f"unisci la versione 0.2.0 (commit {commit}) alle modifiche locali",
                repo / "data" / "compito")
    w = UpdateMergeWorker(task, make, "Andrea", root=repo, check_command=[sys.executable, "check.py"],
                          test_tools=tools or (lambda root: ""), failed=failed.append)
    result = asyncio.run(w.run(task, WorkerHooks(progress=lambda t: None, confirm=confirm)))
    return result, asked, agents, failed, w


def settings_file(repo, text):
    (repo / "voce.py").write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "impostazioni")


def test_a_clean_update_merges_by_itself_and_keeps_the_local_change(repo):
    commit = release_and_local(repo, {"versione.txt": "ok"}, {"locale.txt": "ok"})
    result, asked, agents, failed, w = merge(repo, commit)
    assert "unita alle modifiche" in result and "senza conflitti" in result and "riavvio" in result
    assert asked == [] and agents == [] and failed == []  # da sola e verde: né sotto-agente né domande
    assert (repo / "versione.txt").exists() and (repo / "locale.txt").exists()  # tutte e due
    contains = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=repo).returncode == 0
    assert contains and git(repo, "status", "--porcelain") == ""
    assert not w.worktree.exists() and git(repo, "branch", "--list", "aggiornamento/*") == ""


def test_a_conflict_is_fixed_by_the_agent_and_applied_after_a_yes(repo):
    settings_file(repo, "VELOCITA = 1\nGIORNI = 30\n")
    commit = release_and_local(repo, {"voce.py": "VELOCITA = 1\nGIORNI = 60\n"},
                               {"voce.py": "VELOCITA = 3\nGIORNI = 30\n"})
    result, asked, agents, failed, _ = merge(repo, commit, fix={"voce.py": "VELOCITA = 3\nGIORNI = 60\n"})
    assert len(agents) == 1 and "conflitti in: voce.py" in agents[0].seen
    assert len(asked) == 1 and "0.2.0" in asked[0] and failed == []
    assert (repo / "voce.py").read_text() == "VELOCITA = 3\nGIORNI = 60\n"  # la sua velocità e la novità insieme
    assert "Entra in funzione al riavvio" in result


def test_an_unresolved_conflict_leaves_everything_as_it_was(repo):
    settings_file(repo, "VELOCITA = 1\n")
    commit = release_and_local(repo, {"voce.py": "VELOCITA = 2\n"}, {"voce.py": "VELOCITA = 3\n"})
    mine = git(repo, "rev-parse", "HEAD")
    result, asked, _agents, failed, _ = merge(repo, commit, close=False)
    assert "NON applicato" in result and "non sono stati risolti" in result and asked == [] and failed == ["0.2.0"]
    assert git(repo, "rev-parse", "HEAD") == mine and (repo / "voce.py").read_text() == "VELOCITA = 3\n"
    assert git(repo, "status", "--porcelain") == ""
    assert "aggiornamento/" in git(repo, "branch", "--list", "aggiornamento/*")  # il tentativo resta da guardare


def test_red_checks_after_the_merge_are_never_applied(repo):
    commit = release_and_local(repo, {"due.txt": "ok"}, {"uno.txt": "ok"})  # da soli verdi, insieme rossi
    mine = git(repo, "rev-parse", "HEAD")
    result, _asked, agents, failed, _ = merge(repo, commit, close=False)
    assert len(agents) == 1 and "le prove non passano" in agents[0].seen  # prima ci prova il sotto-agente
    assert "NON applicato" in result and git(repo, "rev-parse", "HEAD") == mine and failed == ["0.2.0"]


def test_declined_means_nothing_changes(repo):
    settings_file(repo, "VELOCITA = 1\n")
    commit = release_and_local(repo, {"voce.py": "VELOCITA = 2\n"}, {"voce.py": "VELOCITA = 3\n"})
    mine = git(repo, "rev-parse", "HEAD")
    result, asked, _, failed, _ = merge(repo, commit, answer=False, fix={"voce.py": "VELOCITA = 3\n"})
    assert len(asked) == 1 and "scartato" in result and git(repo, "rev-parse", "HEAD") == mine and failed == ["0.2.0"]


def test_without_test_tools_nothing_is_merged(repo):
    from jarvis.localrepo import RepoError

    commit = release_and_local(repo, {"versione.txt": "ok"}, {"locale.txt": "ok"})
    mine = git(repo, "rev-parse", "HEAD")

    def no_tools(root):
        raise RepoError("rete assente")

    result, _, agents, failed, _ = merge(repo, commit, tools=no_tools)
    assert "rete assente" in result and agents == [] and git(repo, "rev-parse", "HEAD") == mine
    assert failed == ["0.2.0"]


def test_the_merge_task_reads_version_and_commit():
    from jarvis.tasks.self_improve import parse_merge_goal

    assert parse_merge_goal(f"unisci la versione 1.2.3 (commit {'a' * 40}) alle modifiche") == ("1.2.3", "a" * 40)
    with pytest.raises(ValueError):
        parse_merge_goal("unisci la versione nuova")


def test_self_improve_needs_git_and_gets_its_test_tools(repo, tmp_path):
    import asyncio

    from jarvis.localrepo import RepoError

    async def yes(text):
        return True

    got = []
    hooks = WorkerHooks(progress=lambda t: None, confirm=yes)
    task = Task(2, "x", "fai x", repo / "data" / "c")
    w = SelfImproveWorker(task, lambda t, wt, env: FakeAgent(t, wt, env, root=repo, files={"uno.txt": "ok"}),
                          "Andrea", root=repo, check_command=[sys.executable, "check.py"], test_tools=got.append)
    asyncio.run(w.run(task, hooks))
    assert got == [repo]  # gli strumenti per le prove prima di tutto

    def no_tools(root):
        raise RepoError("rete assente")

    w2 = SelfImproveWorker(Task(3, "y", "fai y", repo / "data" / "d"), lambda *a: None, "Andrea", root=repo,
                           test_tools=no_tools)
    assert "rete assente" in asyncio.run(w2.run(w2.task, hooks))
    no_git = tmp_path / "senza_git"
    no_git.mkdir()
    w3 = SelfImproveWorker(Task(4, "z", "fai z", no_git), lambda *a: None, "Andrea", root=no_git)
    assert "serve Git" in asyncio.run(w3.run(w3.task, hooks))

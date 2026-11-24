"""Il guardiano con processi figli veri (piccoli script al posto di Jarvis) e un vero repository git."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import health, paths, supervisor
from jarvis.supervisor import Policy, Supervisor, decide, prepare_known_good

FAST = Policy(backoff_s=(0.01,), crash_window_s=600, crashes_for_rollback=3, heartbeat_timeout_s=1.0,
              startup_grace_s=5.0, poll_s=0.05)


def child(script: str):
    """Un 'Jarvis' finto: lo script riceve la cartella di stato come primo argomento."""
    return lambda code_root: [sys.executable, "-c", script]


def make(tmp_path, script, policy=FAST) -> Supervisor:
    state, logs = tmp_path / "state", tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "jarvis.log").write_text("riga 1\nTraceback: errore finto\n", encoding="utf-8")
    sup = Supervisor([], root=tmp_path, state_dir=state, logs_dir=logs, policy=policy, child_cmd=child(script))
    return sup


COUNTER = '''
import os, sys, pathlib
p = pathlib.Path(r"{dir}") / "giri.txt"
n = int(p.read_text()) if p.exists() else 0
p.write_text(str(n + 1))
'''


def test_decide():
    p = Policy(crash_window_s=600, crashes_for_rollback=3)
    assert decide([0, 10], 20, p, "nuovo", "vecchio", False) == "restart"
    assert decide([0, 10, 20], 30, p, "nuovo", "vecchio", False) == "rollback"
    assert decide([0, 10, 20], 30, p, "nuovo", "vecchio", True) == "give_up"  # cade anche la versione sana
    assert decide([0, 10, 20], 30, p, "stesso", "stesso", False) == "give_up"  # niente a cui tornare
    assert decide([0, 10, 20], 30, p, "nuovo", None, False) == "give_up"
    assert decide([0, 10, 700], 710, p, "nuovo", "vecchio", False) == "restart"  # cadute lontane nel tempo


def test_crash_is_reported_and_restarted(tmp_path):
    script = COUNTER.format(dir=tmp_path) + "sys.exit(3 if n == 0 else 0)\n"
    sup = make(tmp_path, script)
    assert sup.run() == 0
    assert sup.restarts == 1 and sup.outcome == "chiuso"
    reports = list((tmp_path / "logs" / "cadute").glob("*.txt"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "codice d'uscita: 3" in text and "errore finto" in text
    note = json.loads((tmp_path / "state" / health.RECOVERY).read_text(encoding="utf-8"))
    assert note["reason"] == "crash" and note["rollback"] is False


def test_frozen_child_is_killed_and_restarted(tmp_path):
    state = tmp_path / "state"
    script = COUNTER.format(dir=tmp_path) + f'''
import json, time
if n == 0:
    (pathlib.Path(r"{state}")).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(r"{state}") / "heartbeat.json").write_text(
        json.dumps({{"pid": os.getpid(), "run": os.environ["JARVIS_RUN_ID"], "ts": time.time()}}))
    time.sleep(60)  # bloccato: il battito non si aggiorna più
'''
    sup = make(tmp_path, script)
    assert sup.run() == 0
    note = json.loads((state / health.RECOVERY).read_text(encoding="utf-8"))
    assert note["reason"] == "hang" and sup.restarts == 1


def test_repeated_crashes_switch_to_the_known_good_version(tmp_path, monkeypatch):
    for var in ("JARVIS_ENGINE_DIR", "JARVIS_MODELS_DIR", "JARVIS_DATA_DIR"):  # li deve passare il guardiano, non
        monkeypatch.delenv(var, raising=False)  # l'ambiente di chi lancia i test (le copie separate li hanno già)
    good_tree = tmp_path / "versione_sana"
    good_tree.mkdir()
    monkeypatch.setattr(health, "current_commit", lambda root=None: "nuovo")
    monkeypatch.setattr(supervisor, "prepare_known_good", lambda root, commit, dest: good_tree)
    health.write_json(tmp_path / "state" / health.KNOWN_GOOD, {"commit": "vecchio"})
    # il codice nuovo cade sempre; quello sano (cartella diversa) parte e si chiude bene
    script = f'''
import os, sys, pathlib
if pathlib.Path.cwd() == pathlib.Path(r"{good_tree}"):
    pathlib.Path(r"{tmp_path}/env.txt").write_text("|".join(os.environ.get(v, "") for v in
                                                         ("JARVIS_ENGINE_DIR", "JARVIS_MODELS_DIR", "JARVIS_DATA_DIR")))
    sys.exit(0)
sys.exit(1)
'''
    sup = make(tmp_path, script)
    assert sup.run() == 0
    assert sup.rolled_back and sup.code_root == good_tree
    # motori, modelli e dati restano quelli veri
    assert (tmp_path / "env.txt").read_text() == f"{paths.ENGINE_DIR}|{paths.MODELS_DIR}|{paths.DATA_DIR}"
    assert json.loads((tmp_path / "state" / health.RECOVERY).read_text(encoding="utf-8"))["rollback"] is True


def test_gives_up_instead_of_looping_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "current_commit", lambda root=None: "stesso")
    health.write_json(tmp_path / "state" / health.KNOWN_GOOD, {"commit": "stesso"})
    sup = make(tmp_path, "import sys; sys.exit(2)")
    assert sup.run() == 1 and sup.outcome == "arreso" and sup.restarts == 2


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")
    (r / "codice.py").write_text("versione = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "uno")
    first = _git(r, "rev-parse", "HEAD")
    (r / "codice.py").write_text("versione = 2\n")
    _git(r, "commit", "-qam", "due")
    return r, first, _git(r, "rev-parse", "HEAD")


def test_known_good_tree_is_a_separate_checkout(repo, tmp_path):
    r, first, second = repo
    dest = tmp_path / "sana"
    assert prepare_known_good(r, first, dest) == dest
    assert (dest / "codice.py").read_text() == "versione = 1\n"
    assert (r / "codice.py").read_text() == "versione = 2\n"  # i file di lavoro non si toccano
    prepare_known_good(r, first, dest)  # già pronta: si riusa
    prepare_known_good(r, second, dest)  # un'altra versione sana: si rifà
    assert (dest / "codice.py").read_text() == "versione = 2\n"


def test_clean_commit_only_for_saved_code(repo):
    r, _, second = repo
    assert health.clean_commit(r) == second
    (r / "codice.py").write_text("versione = 3, a metà\n")
    assert health.clean_commit(r) is None  # una prova a metà non diventa mai "versione sana"


def test_heartbeat_through_a_launcher_is_recognised(tmp_path):
    r""".venv\Scripts\pythonw.exe è un lanciatore: Jarvis gira in un processo figlio con un altro pid.
    Successo davvero il 7/10: il guardiano aspettava il battito dal pid del lanciatore e uccideva un Jarvis
    sano ogni 3 minuti. Qui un lanciatore in mezzo e il battito vero di Jarvis, più lungo della tolleranza."""
    launcher = f"import subprocess, sys; sys.exit(subprocess.call([sys.executable, '-c', {beating(tmp_path)!r}]))"
    sup = make(tmp_path, launcher, BEAT_POLICY)
    assert sup.run() == 0
    assert sup.restarts == 0 and (tmp_path / "giri.txt").read_text() == "1"  # mai ucciso: batteva


def test_a_failed_read_of_the_heartbeat_does_not_kill_a_healthy_jarvis(tmp_path, monkeypatch):
    """Il file del battito sostituito proprio mentre il guardiano lo legge (o l'antivirus che lo tiene aperto):
    prima una sola lettura fallita faceva contare l'età dall'avvio e un Jarvis sano veniva ucciso."""
    real, calls = health.read_json, []

    def flaky(path):
        calls.append(path)
        return None if len(calls) % 3 == 0 else real(path)

    monkeypatch.setattr(health, "read_json", flaky)
    sup = make(tmp_path, beating(tmp_path), BEAT_POLICY)
    assert sup.run() == 0
    assert sup.restarts == 0 and len(calls) > 30


def test_jarvis_gets_a_hidden_console_when_the_guardian_has_none(tmp_path, monkeypatch):
    """26/10: con l'avvio automatico (pythonw, nessuna console) ogni sessione di Claude apriva una finestra di
    Terminale "claude". Jarvis parte con python.exe e una console nascosta che i suoi programmi ereditano."""
    monkeypatch.setattr(supervisor.sys, "executable", str(tmp_path / "base" / "pythonw.exe"))
    sup = Supervisor([], root=tmp_path, state_dir=tmp_path / "state", logs_dir=tmp_path)
    assert Path(sup._python()).name == "python.exe"  # mai pythonw: senza console i figli ne aprono una a testa
    monkeypatch.setattr(supervisor, "has_console", lambda: False)
    assert sup._creationflags() == subprocess.CREATE_NO_WINDOW
    monkeypatch.setattr(supervisor, "has_console", lambda: True)  # da "Avvia Jarvis.bat": usa quella che c'è
    assert sup._creationflags() == 0


# il battito vero di Jarvis (Heartbeat.run) ogni 0,1 s per 3 s, più a lungo delle tolleranze del guardiano
BEAT_POLICY = Policy(backoff_s=(0.01,), crashes_for_rollback=3, heartbeat_timeout_s=1.0, startup_grace_s=2.0,
                     poll_s=0.05)


def beating(tmp_path) -> str:
    root = Path(__file__).resolve().parents[1]
    return COUNTER.format(dir=tmp_path) + f'''
import asyncio
sys.path.insert(0, r"{root}")
from jarvis import health
hb = health.Heartbeat(state_dir=pathlib.Path(r"{tmp_path / "state"}"), interval_s=0.1)

async def main():
    try:
        await asyncio.wait_for(hb.run(lambda: "in attesa"), 3.0)
    except TimeoutError:
        pass

asyncio.run(main())
'''


FIRST_START = Policy(backoff_s=(0.01,), crashes_for_rollback=3, heartbeat_timeout_s=1.0, startup_grace_s=1.0,
                     poll_s=0.05)


def test_a_long_first_start_keeps_beating_and_is_not_killed(tmp_path):
    """Il primo avvio dura minuti (download dei modelli): il guardiano lo credeva bloccato, lo chiudeva dopo 3 minuti e
    alla terza volta si arrendeva (24/11, la prima installazione vera). Qui "scarica" il triplo dell'attesa."""
    root = Path(__file__).resolve().parents[1]
    script = f'''
import asyncio, os, sys, pathlib
sys.path.insert(0, r"{root}")
from jarvis.__main__ import early_heartbeat
async def main():
    beat = early_heartbeat(pathlib.Path(r"{tmp_path / 'state'}"), interval_s=0.2)
    await asyncio.sleep(3.0)
    beat.cancel()
asyncio.run(main())
'''
    sup = make(tmp_path, script, FIRST_START)
    assert sup.run() == 0 and sup.restarts == 0 and sup.outcome == "chiuso"
    assert json.loads((tmp_path / "state" / health.HEARTBEAT).read_text(encoding="utf-8"))["status"] == "avvio"


def test_without_the_guardian_there_is_no_early_beat(monkeypatch):
    import asyncio

    from jarvis import __main__ as main_module

    monkeypatch.delenv("JARVIS_SUPERVISED", raising=False)
    assert asyncio.run(asyncio.to_thread(main_module.early_heartbeat)) is None


def test_the_early_beat_starts_before_the_first_start_and_stops_after_jarvis_beats():
    import inspect

    from jarvis import __main__ as main_module

    main, run = inspect.getsource(main_module.main), inspect.getsource(main_module._run)
    assert main.index("early_heartbeat()") < main.index("_run(")
    assert run.index("run_setup(") < run.index("await app.start()") < run.index("early.cancel()")


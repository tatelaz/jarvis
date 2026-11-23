"""Aggiornamento automatico: quale release vale, quando si installa, come si riparte. Niente GitHub vero: le release
sono inventate, l'installatore non parte mai (si guarda solo il comando che partirebbe)."""

import json
import os
import shutil
import subprocess

import pytest

from jarvis import updates as U

SHA = "a" * 64


def release(tag="v0.2.0", name=None, digest=f"sha256:{SHA}", size=17_000_000, **kw):
    v = tag.removeprefix("v")
    name = name or f"Jarvis-Setup-{v}.exe"
    asset = {"name": name, "digest": digest, "size": size,
             "browser_download_url": f"{U.DOWNLOADS}{tag}/{name}"}
    return {"tag_name": tag, "draft": False, "prerelease": False, "body": "novità", "assets": [asset], **kw}


@pytest.fixture
def app_root(tmp_path):
    root = tmp_path / "Jarvis"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "jarvis"\nversion = "0.1.0"\n', encoding="utf-8")
    (root / "avvia_jarvis.pyw").write_text("", encoding="utf-8")
    (root / "tools").mkdir()
    (root / "tools" / "uv.exe").write_bytes(b"uv")  # l'installatore porta uv: è così che si riconosce la copia installata
    return root


class Harness:
    def __init__(self, root, tmp_path, mode="automatici", releases=None, repo=False):
        self.mode = [mode]
        self.releases = [release()] if releases is None else releases
        self.notes, self.launched, self.downloads, self.exits = [], [], [], []
        self.now = [1_000_000.0]
        self.fail_fetch = None
        self.u = U.Updater(lambda: self.mode[0], self.notes.append, lambda: self.exits.append(1), root=root,
                           state_dir=tmp_path / "state", fetch=self.fetch, download=self.download,
                           launch=self.launched.append, clock=lambda: self.now[0], repo=lambda: repo)

    async def fetch(self):
        if self.fail_fetch:
            raise self.fail_fetch
        return self.releases

    async def download(self, rel):
        self.downloads.append(rel.name)
        path = self.u.state_dir.parent / f"Jarvis-Setup-{rel.name}.exe"
        path.write_bytes(b"MZ")
        return path


@pytest.fixture
def h(app_root, tmp_path):
    return Harness(app_root, tmp_path)


# --- quale release vale ---


def test_versions():
    assert U.parse_version("v1.2.3") == (1, 2, 3) == U.parse_version("1.2.3")
    assert U.parse_version("modelli-1") is None and U.parse_version("v1.2") is None


def test_only_a_newer_well_made_release_counts():
    current = (0, 1, 0)
    assert U.pick([release("v0.1.0")], current) is None  # la stessa
    assert U.pick([release("v0.0.9")], current) is None  # più vecchia
    assert U.pick([{"tag_name": "modelli-1", "assets": []}], current) is None  # la release dei modelli
    assert U.pick([release(draft=True)], current) is None
    assert U.pick([release(prerelease=True)], current) is None
    assert U.pick([release(digest="")], current) is None  # senza impronta non si installa niente
    assert U.pick([release(digest="sha256:corta")], current) is None
    assert U.pick([release(name="Jarvis-Setup-0.2.0.zip")], current) is None
    assert U.pick([release(size=0)], current) is None
    bad = release()
    bad["assets"][0]["browser_download_url"] = "https://altro.example/Jarvis-Setup-0.2.0.exe"
    assert U.pick([bad], current) is None  # solo dalle release del progetto
    assert U.pick("non è un elenco", current) is None and U.pick([None, 3], current) is None
    best = U.pick([release("v0.2.0"), release("v0.10.1"), release("v0.3.0")], current)
    assert best.name == "0.10.1" and best.sha256 == SHA and best.notes == "novità"  # 10 > 3: numeri, non testo


def developer_copy(root):
    (root / "tools" / "uv.exe").unlink()  # presa con git da chi sviluppa: niente uv dell'installatore


def test_installed_copy_is_the_one_with_the_installer_uv(app_root):
    assert U.installed_copy(app_root)
    (app_root / ".git").mkdir()
    assert U.installed_copy(app_root)  # dal 23/11 anche la copia installata ha git (jarvis/localrepo.py)
    developer_copy(app_root)
    assert not U.installed_copy(app_root)
    assert U.current_version(app_root) == (0, 1, 0)


# --- quando si installa ---


async def test_automatic_update_waits_for_quiet_then_installs_and_exits(h):
    await h.u.tick(quiet_for=60, busy=False)
    assert h.downloads == ["0.2.0"] and h.launched == [] and h.exits == []  # scaricato, ma Jarvis non è fermo
    await h.u.tick(quiet_for=U.QUIET_S, busy=True)
    assert h.launched == []  # compiti, conversazione o riposo: si aspetta
    await h.u.tick(quiet_for=U.QUIET_S, busy=False)
    assert len(h.launched) == 1 and h.exits == [1] and h.downloads == ["0.2.0"]  # non si riscarica
    mark = json.loads(h.u.mark.read_text(encoding="utf-8"))
    assert mark["from"] == "0.1.0" and mark["to"] == "0.2.0"
    await h.u.tick(quiet_for=U.QUIET_S, busy=False)
    assert len(h.launched) == 1  # una volta sola


async def test_ask_mode_says_it_once_and_installs_on_request(h):
    h.mode[0] = "chiedi"
    await h.u.tick(quiet_for=U.QUIET_S * 4, busy=False)
    await h.u.tick(quiet_for=U.QUIET_S * 4, busy=False)
    assert h.launched == [] and len(h.notes) == 1 and "la 0.2.0" in h.notes[0] and "aggiornamenti" in h.notes[0]
    assert "pronta la 0.2.0" in h.u.describe()
    assert h.u.apply().startswith("installo la versione 0.2.0") and len(h.launched) == 1


async def test_off_means_not_even_asking_github(h):
    h.mode[0] = "spenti"
    h.fail_fetch = AssertionError("non doveva chiedere")
    await h.u.tick(quiet_for=U.QUIET_S, busy=False)
    assert h.launched == [] and h.u.error == ""


async def test_the_developer_copy_never_updates_by_itself(h, app_root):
    developer_copy(app_root)
    h.fail_fetch = AssertionError("non doveva chiedere")
    await h.u.tick(quiet_for=U.QUIET_S, busy=False)
    assert h.u.error == "" and h.downloads == []  # GitHub non è stato nemmeno interpellato
    assert h.launched == [] and "git" in h.u.apply() and "git" in h.u.describe()


async def test_github_is_asked_once_a_day_and_sooner_after_an_error(h):
    await h.u.tick(0, False)
    h.releases = [release("v0.3.0")]
    h.now[0] += U.CHECK_EVERY_S - 1
    await h.u.tick(0, False)
    assert h.downloads == ["0.2.0"]  # non ancora il giorno dopo
    h.now[0] += 1
    await h.u.tick(0, False)
    assert h.downloads == ["0.2.0", "0.3.0"] and h.u.available.name == "0.3.0"
    h.fail_fetch = OSError("rete giù")
    h.now[0] += U.CHECK_EVERY_S
    await h.u.tick(0, False)
    assert "rete giù" in h.u.error and h.u.next_check == h.now[0] + U.RETRY_S
    assert h.u.installer is not None  # quello già scaricato resta buono


async def test_a_private_repository_finds_nothing_and_says_nothing(h):
    h.releases = None  # GitHub risponde 404
    await h.u.tick(U.QUIET_S, False)
    assert h.u.available is None and h.u.error == "" and h.notes == [] and h.launched == []
    assert "nessuna versione nuova" in h.u.describe()


async def test_a_failed_download_is_retried_and_never_installed(h):
    async def broken(rel):
        raise RuntimeError("impronta diversa")

    h.u.download = broken
    await h.u.tick(U.QUIET_S, False)
    assert h.launched == [] and "impronta" in h.u.error and h.u.installer is None
    assert h.u.apply() == "non c'è un aggiornamento pronto da installare"


# --- il processo a parte e la ripartenza ---


def test_the_helper_waits_installs_silently_and_relaunches(tmp_path):
    root = tmp_path / "Jar'vis"  # anche un apostrofo nel percorso resta dentro le virgolette
    cmd = U.helper_command([111, 222], root / "Setup.exe", root / "data" / "agg.log",
                           root / ".venv" / "Scripts" / "pythonw.exe", root)
    assert cmd[:6] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command"]
    script = cmd[6]
    assert script.index("Wait-Process -Id 111") < script.index("Wait-Process -Id 222") < script.index("Setup.exe")
    assert "'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'" in script and "-Wait;" in script
    assert script.index("Setup.exe") < script.index("pythonw.exe")
    assert "Jar''vis" in script and "Jar'vis" not in script.replace("Jar''vis", "")
    assert '"' + str(root / "avvia_jarvis.pyw") + '"' in script.replace("''", "'")


def test_the_helper_survives_jarvis(monkeypatch):
    calls = []

    def popen(cmd, creationflags, close_fds):
        calls.append(creationflags)
        if creationflags & U.BREAKAWAY:
            raise PermissionError("il job non lo permette")

    monkeypatch.setattr(subprocess, "Popen", popen)
    U._launch(["powershell.exe"])
    assert calls[0] & U.BREAKAWAY and calls[0] & U.NO_WINDOW and not calls[1] & U.BREAKAWAY
    assert not calls[0] & 0x00000008  # DETACHED_PROCESS: senza console PowerShell non parte


async def test_after_the_restart_it_says_how_it_went_once(h, app_root, tmp_path):
    await h.u.tick(U.QUIET_S, False)
    assert h.u.mark.exists()
    (app_root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
    again = Harness(app_root, tmp_path).u
    assert "si è aggiornato alla versione 0.2.0" in again.after_restart()
    assert again.after_restart() is None and not again.mark.exists()


async def test_a_failed_install_is_said_too(h, app_root, tmp_path):
    await h.u.tick(U.QUIET_S, False)
    again = Harness(app_root, tmp_path).u  # ripartito, ma è ancora la 0.1.0
    said = again.after_restart()
    assert "non è riuscito" in said and "aggiornamento.log" in said and "0.1.0" in said
    h.u.mark.write_text("rovinato", encoding="utf-8")
    assert again.after_restart() is None


async def test_github_404_means_none(monkeypatch):
    from aiohttp import web
    from test_setup_flow import free_port

    async def releases(req):
        return web.json_response([release()]) if req.path.endswith("/pubblico") else web.Response(status=404)

    app = web.Application()
    app.router.add_get("/{p}", releases)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    try:
        assert await U.fetch_releases(f"http://127.0.0.1:{port}/privato") is None
        assert (await U.fetch_releases(f"http://127.0.0.1:{port}/pubblico"))[0]["tag_name"] == "v0.2.0"
    finally:
        await runner.cleanup()


# --- a voce: lo strumento e l'impostazione ---


@pytest.fixture
def voice(h, tmp_path):
    from jarvis.config import ConfigStore, is_live
    from jarvis.core.bus import Bus
    from jarvis.memory.store import Memory
    from jarvis.tools.front import TOOL_GROUPS, Services, build_front_tools

    assert TOOL_GROUPS["aggiornamenti"] == "sistema" and is_live("system.updates")  # solo l'utente; vale subito
    store = ConfigStore(tmp_path / "config.yaml")
    mem = Memory(tmp_path / "m.db")
    yield build_front_tools(Services(Bus(), store, mem, updates=h.u)), store
    mem.close()


async def test_the_tool_checks_tells_and_installs(h, voice):
    reg, _ = voice
    assert "versione 0.1.0" in await reg.call("aggiornamenti", {"azione": "stato"})
    out = await reg.call("aggiornamenti", {"azione": "controlla"})
    assert "pronta la 0.2.0" in out and h.downloads == ["0.2.0"]
    out = await reg.call("aggiornamenti", {"azione": "installa"})
    assert out.startswith("installo la versione 0.2.0") and len(h.launched) == 1 and h.exits == [1]


async def test_the_tool_on_the_developer_copy_does_not_ask_github(h, voice, app_root):
    reg, _ = voice
    developer_copy(app_root)
    h.fail_fetch = AssertionError("non doveva chiedere")
    assert "copia di sviluppo" in await reg.call("aggiornamenti", {"azione": "controlla"})


async def test_the_setting_in_plain_words(voice):
    reg, store = voice
    assert store.get("system.updates") == "automatici"
    out = await reg.call("impostazione", {"chiave": "aggiornamenti", "valore": "prima chiedi"})
    assert store.get("system.updates") == "chiedi" and "vale subito" in out
    await reg.call("impostazione", {"chiave": "system.updates", "valore": "mai"})
    assert store.get("system.updates") == "spenti"
    out = await reg.call("impostazione", {"chiave": "aggiornamenti", "valore": "boh"})
    assert out.startswith("Errore") and "automatici, chiedi o spenti" in out and store.get("system.updates") == "spenti"


# --- quando Jarvis è occupato, il guardiano, lo scaricamento vero ---


def test_busy_means_resting_talking_thinking_or_working():
    from types import SimpleNamespace as NS

    idle = NS(jarvis_active=False, thinking=False)
    tasks = NS(tasks={1: NS(status="finito"), 2: NS(status="annullato")})
    assert not U.busy_now(False, idle, tasks) and not U.busy_now(False, idle, None)
    assert U.busy_now(True, idle, tasks)  # a riposo: forse sta giocando
    assert U.busy_now(False, NS(jarvis_active=True, thinking=False), tasks)
    assert U.busy_now(False, NS(jarvis_active=False, thinking=True), tasks)
    assert U.busy_now(False, None, tasks)  # conversazione non ancora pronta
    for status in ("in coda", "in corso", "attende conferma"):
        assert U.busy_now(False, idle, NS(tasks={3: NS(status=status)}))


async def test_under_the_guardian_both_are_waited_for(h, monkeypatch):
    monkeypatch.setenv("JARVIS_SUPERVISED", "1")
    monkeypatch.setattr(U.os, "getppid", lambda: 4242)
    await h.u.tick(U.QUIET_S, False)
    script = h.launched[0][-1]
    assert f"Wait-Process -Id {U.os.getpid()} " in script and "Wait-Process -Id 4242 " in script
    monkeypatch.delenv("JARVIS_SUPERVISED")
    h.u.applying = False
    h.u.apply()
    assert "4242" not in h.launched[1][-1]  # senza guardiano si aspetta solo Jarvis


async def test_the_installer_is_downloaded_and_checked_like_the_models(tmp_path, monkeypatch, app_root):
    import hashlib

    from aiohttp import web
    from test_setup_flow import free_port

    from jarvis import paths
    from jarvis.setup.download import DownloadError

    body = b"MZ" + bytes(range(256)) * 40
    app = web.Application()
    async def installer(req):
        return web.Response(body=body)

    app.router.add_get("/Jarvis-Setup-0.2.0.exe", installer)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    monkeypatch.setattr(paths, "ENGINE_DIR", tmp_path / "engine")
    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path / "models")
    u = U.Updater(lambda: "automatici", print, print, root=app_root, state_dir=tmp_path / "state")
    url = f"http://127.0.0.1:{port}/Jarvis-Setup-0.2.0.exe"
    try:
        good = U.Release((0, 2, 0), url, hashlib.sha256(body).hexdigest(), len(body))
        path = await u.download(good)
        assert path == tmp_path / "engine" / "aggiornamenti" / "Jarvis-Setup-0.2.0.exe" and path.read_bytes() == body
        path.unlink()
        with pytest.raises(DownloadError, match="impronta"):
            await u.download(U.Release((0, 2, 0), url, "b" * 64, len(body)))
        assert not path.exists()  # un installatore diverso da quello pubblicato non resta sul disco
    finally:
        await runner.cleanup()


# --- copie che sono repository (jarvis/localrepo.py): il bundle al posto dell'installatore ---

COMMIT = "c" * 40


def with_bundle(tag="v0.2.0", digest=f"sha256:{'b' * 64}"):
    r = release(tag)
    v = tag.removeprefix("v")
    r["assets"].append({"name": f"Jarvis-{v}.bundle", "digest": digest, "size": 3_500_000,
                        "browser_download_url": f"{U.DOWNLOADS}{tag}/Jarvis-{v}.bundle"})
    return r


class Plan:
    def __init__(self, how, commit=COMMIT):
        self.how, self.commit = how, commit


@pytest.fixture
def g(app_root, tmp_path):
    """Una copia installata che è già un repository, con la release che porta anche il bundle."""
    h = Harness(app_root, tmp_path, releases=[with_bundle()], repo=True)
    h.plans, h.merges = [Plan("avanti")], []
    h.u.plan = lambda bundle: h.plans[0]
    h.u.merge = lambda version, commit: h.merges.append((version, commit))
    return h


def test_the_bundle_counts_only_if_made_properly():
    rel = U.pick([with_bundle()], (0, 1, 0))
    assert rel.bundle.url.endswith("/v0.2.0/Jarvis-0.2.0.bundle") and rel.bundle.sha256 == "b" * 64
    assert U.pick([with_bundle(digest="")], (0, 1, 0)).bundle is None  # senza impronta: solo l'installatore
    assert U.pick([release()], (0, 1, 0)).bundle is None


async def test_without_local_changes_it_moves_on_with_git(g):
    await g.u.tick(U.QUIET_S, False)
    assert g.u.bundled and g.exits == [1] and len(g.launched) == 1
    script = g.launched[0][-1]
    assert f"merge --ff-only {COMMIT}" in script and "sync --frozen --no-dev" in script
    assert "avvia_jarvis.pyw" in script and "Jarvis-Setup" not in script  # niente installatore
    assert script.index("merge --ff-only") < script.index("sync --frozen") < script.index("avvia_jarvis.pyw")


async def test_with_local_changes_a_task_merges_and_jarvis_stays_on(g):
    g.plans[0] = Plan("unione")
    await g.u.tick(U.QUIET_S, False)
    assert g.merges == [("0.2.0", COMMIT)] and g.launched == [] and g.exits == []
    assert "unione con le modifiche locali in corso" in g.u.describe()
    await g.u.tick(U.QUIET_S, False)
    assert len(g.merges) == 1  # un compito solo per versione
    g.u.merge_failed("0.2.0")
    assert g.u.merging is None


async def test_once_merged_only_a_restart_is_missing(g, app_root):
    g.plans[0] = Plan("unione")
    await g.u.tick(U.QUIET_S, False)
    (app_root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")  # unione entrata
    assert "manca il riavvio" in g.u.describe()
    await g.u.tick(U.QUIET_S, busy=True)
    assert g.launched == []  # occupato: si aspetta
    await g.u.tick(U.QUIET_S, False)
    script = g.launched[0][-1]
    assert "--ff-only" not in script and "sync --frozen --no-dev" in script and g.exits == [1]  # il codice c'è già


async def test_ask_mode_asks_before_restarting_into_the_merged_code(g, app_root):
    g.mode[0] = "chiedi"
    (app_root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
    await g.u.tick(U.QUIET_S, False)
    await g.u.tick(U.QUIET_S, False)
    assert g.launched == [] and len(g.notes) == 1 and "riavvio" in g.notes[0]
    assert g.u.apply().startswith("passo alla versione 0.2.0") and len(g.launched) == 1


async def test_a_version_already_inside_is_not_installed_again(g):
    g.plans[0] = Plan("dentro")
    g.mode[0] = "chiedi"
    await g.u.tick(U.QUIET_S, False)
    assert g.u.apply() == "la versione 0.2.0 è già nel mio codice" and g.launched == [] and g.u.available is None


def test_the_git_helper(tmp_path):
    root = tmp_path / "Jarvis dell'utente"
    cmd = U.git_helper_command([11, 22], root, COMMIT, r"C:\Git\cmd\git.exe", True, tmp_path / "agg.log",
                               tmp_path / "pythonw.exe")
    script = cmd[-1]
    assert cmd[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle"]
    assert "Wait-Process -Id 11 " in script and "Wait-Process -Id 22 " in script
    assert "dell''utente" in script  # l'apostrofo nel percorso non rompe il comando
    assert "sync --frozen --python-preference" in script  # con gli strumenti per le prove (c'erano): niente --no-dev
    assert "UV_PYTHON_INSTALL_DIR" in script and str(root / "tools" / "python").replace("'", "''") in script
    with pytest.raises(ValueError):
        U.git_helper_command([1], root, "HEAD; Remove-Item", "git", False, tmp_path / "a.log", tmp_path / "p.exe")


@pytest.mark.skipif(os.name != "nt" or not shutil.which("git"), reason="serve Windows con Git")
def test_the_git_helper_log_is_readable_and_has_the_git_errors(tmp_path):
    """Davvero, con PowerShell 5.1: nella Sandbox il registro usciva in UTF-16 (con *>>) e non si leggeva."""
    root = tmp_path / "Jarvis è qui"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    log_file = tmp_path / "aggiornamento.log"
    cmd = U.git_helper_command([], root, COMMIT, shutil.which("git"), False, log_file, tmp_path / "manca.exe")
    subprocess.run(cmd, capture_output=True, timeout=120)
    raw = log_file.read_bytes()
    assert b"\x00" not in raw  # niente UTF-16
    text = raw.decode("utf-8-sig")
    assert "--- aggiornamento" in text and COMMIT in text  # l'errore di git (commit che non c'è) è nel registro

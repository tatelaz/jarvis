"""Il primo avvio come procedura e il suo server, con un PC, uno scaricatore e un Claude Code inventati."""

import asyncio
import json
import socket
import subprocess
from pathlib import Path

import aiohttp
import pytest

from jarvis import paths
from jarvis.config import ConfigStore
from jarvis.setup import brains, catalog, flow, pc
from jarvis.setup.download import DownloadError
from jarvis.setup.server import SetupServer

RTX = pc.Gpu("NVIDIA GeForce RTX 4060", "nvidia", 8188, "610.88")


def facts(*gpus, **kw) -> pc.Facts:
    base = {"ram_mb": 32768, "free_mb": 500_000, "mics": 1, "speakers": 2, "online": True}
    return pc.Facts(tuple(gpus), **{**base, **kw})


class FakeDownloader:
    """Come quello vero visto da fuori: dice cosa manca, scarica avvisando dell'avanzamento, si può fermare."""

    def __init__(self, root: Path) -> None:
        self.roots = {"engine": root / "engine", "models": root / "models"}
        self.runs: list[list[str]] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail: Exception | None = None

    def target(self, dest: str) -> Path:
        r, _, rest = dest.partition("/")
        return self.roots[r] / rest

    async def missing(self, files):
        return [f for f in files if not self.target(f.dest).exists()]

    async def run(self, files, report):
        files = list(files)
        self.runs.append([f.dest for f in files])
        total, done = sum(f.size for f in files), 0
        for f in files:
            report(done, total, f.dest)
            await self.gate.wait()
            if self.fail:
                raise self.fail
            done += f.size
            report(done, total, f.dest)


class FakeClaude(brains.ClaudeCode):
    def __init__(self, installed=True, logged_in=False) -> None:
        self.installed, self.logged_in = installed, logged_in
        self.calls: list[str] = []
        super().__init__(find=self._find, run=self._run, popen=self._popen)

    def _find(self) -> str:
        if not self.installed:
            raise FileNotFoundError
        return "C:/fake/claude.exe"

    def _run(self, cmd, **kw):
        out = {"loggedIn": self.logged_in, "authMethod": "claude.ai", "subscriptionType": "pro",
               "email": "persona@example.com", "orgName": "Ditta di prova", "orgId": "123"}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    def _popen(self, cmd, **kw):
        self.calls.append(" ".join(cmd))
        return None


@pytest.fixture
def setup(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    s = flow.Setup(store, FakeDownloader(tmp_path), gather=lambda: facts(RTX), claude=FakeClaude(),
                   voices_dir=tmp_path / "voices", done_path=tmp_path / "setup" / "fatto.json",
                   bundled_voices=tmp_path / "incluse",  # di base senza voci: si prova anche la voce mancante
                   missing_tools=lambda: list(catalog.TOOLS))  # un PC senza OpenCode, Node e Git
    return s


def give_voice(s: flow.Setup) -> None:
    d = s.voices_dir / s.store.config.speech.tts_voice
    d.mkdir(parents=True)
    (d / "voice.json").write_text("{}", encoding="utf-8")


async def download_all(s: flow.Setup) -> dict:
    await s.check_pc()
    await s.start_download()
    await s._task
    return s.download_status()


# --- cosa manca per partire ---


def test_missing_files_follow_the_configuration(tmp_path, monkeypatch):
    from jarvis.identity import model as speaker
    from jarvis.memory import embed

    monkeypatch.setattr(paths, "AUDIOCPP_SERVER", tmp_path / "engine" / "audiocpp_server.exe")
    monkeypatch.setattr(paths, "AUDIOCPP_MODELS", tmp_path / "models")
    monkeypatch.setattr(paths, "SILERO_VAD", tmp_path / "onnx" / "silero.onnx")
    monkeypatch.setattr(paths, "SMART_TURN", tmp_path / "onnx" / "turn.onnx")
    monkeypatch.setattr(speaker, "MODEL", tmp_path / "speaker.onnx")
    monkeypatch.setattr(embed, "MODEL_DIR", tmp_path / "gemma")
    cfg = ConfigStore(tmp_path / "c.yaml").config
    for p in flow.required_files(cfg):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert flow.missing_files(cfg) == []
    paths.SMART_TURN.unlink()
    assert flow.missing_files(cfg) == [paths.SMART_TURN]
    cfg.speech.tts_model = "qwen3-0.6b"  # la voce leggera ha il suo file
    assert any("0.6B" in str(p) for p in flow.missing_files(cfg))


def test_names_are_cleaned():
    assert flow.clean_name("  Andrea\n\t Rossi ") == "Andrea Rossi"
    assert flow.clean_name("\x00\x1b[31mX") == "[31mX"
    assert len(flow.clean_name("a" * 200)) == flow.MAX_NAME
    assert flow.clean_name("   ") == ""


# --- scelte ---


def test_start_info_has_no_placeholder_name(setup):
    assert setup.start_info() == {"lang": "it", "name": "", "autostart": False, "provider": "claude"}
    setup.store.set("persona.user_name", "Andrea")
    setup.store.set("brain.provider", "local")  # non offerto al primo avvio: si propone Claude
    assert setup.start_info()["name"] == "Andrea" and setup.start_info()["provider"] == "claude"


def test_language_and_brain_are_saved_and_checked(setup, tmp_path):
    setup.set_language("en")
    setup.set_brain("opencode")
    again = ConfigStore(tmp_path / "config.yaml").config
    assert again.persona.language == "en" and again.brain.provider == "opencode"
    with pytest.raises(flow.SetupError, match="lingua_sconosciuta"):
        setup.set_language("fr")
    with pytest.raises(flow.SetupError, match="cervello_sconosciuto"):
        setup.set_brain("local")


async def test_pc_check_reports_facts_and_profile(setup):
    setup.gather = lambda: facts(pc.Gpu("Intel UHD", "intel", 512), RTX, mics=0)
    data = await setup.check_pc()
    assert data["facts"]["gpu"] == {"name": RTX.name, "vram_mb": 8188}  # la NVIDIA, non la prima della lista
    assert data["profile"]["key"] == "completo" and "niente_microfono" in data["profile"]["notes"]
    json.dumps(data)


async def test_files_already_there_shrink_the_download(setup):
    whole = (await setup.check_pc())["profile"]["download_mb"]
    silero = catalog.PACKAGES["turns"].files[0]
    out = setup.downloader.target(silero.dest)
    out.parent.mkdir(parents=True)
    out.write_bytes(bytes(silero.size))  # stessa misura: per la stima c'è già
    less = (await setup.check_pc())["profile"]["download_mb"]
    assert whole - less == pytest.approx(silero.size / 2**20, abs=1) and less < whole
    out.write_bytes(b"corto")  # misura diversa: va scaricato
    assert (await setup.check_pc())["profile"]["download_mb"] == whole


async def test_brain_status_never_carries_email_or_organisation(setup):
    data = await setup.brain_status()
    assert data["claude"] == {"installed": True, "logged_in": False, "plan": "pro", "method": "claude.ai"}
    assert "persona@example.com" not in json.dumps(data) and "Ditta" not in json.dumps(data)


async def test_claude_login_and_install_use_the_official_commands(setup):
    await setup.claude_login()
    await setup.claude_install()
    assert setup.claude.calls == ["C:/fake/claude.exe auth login --claudeai",
                                  "powershell -NoProfile -Command irm https://claude.ai/install.ps1 | iex"]
    setup.claude.installed = False
    with pytest.raises(flow.SetupError, match="claude_mancante"):
        await setup.claude_login()


# --- download ---


async def test_download_needs_a_pc_check_and_a_usable_profile(setup):
    with pytest.raises(flow.SetupError, match="controllo_pc"):
        await setup.start_download()
    setup.gather = lambda: facts()  # niente NVIDIA
    await setup.check_pc()
    with pytest.raises(flow.SetupError, match="voce_solo_nvidia"):
        await setup.start_download()


async def test_download_reports_groups_in_page_order_and_finishes(setup):
    status = await download_all(setup)
    assert status["state"] == "done"
    assert [g["key"] for g in status["groups"]] == list(flow.GROUPS)
    assert all(g["done"] == g["total"] > 0 for g in status["groups"])
    assert status["done"] == status["total"] == sum(f.size for f in setup.plan())


async def test_progress_while_downloading(setup):
    await setup.check_pc()
    setup.downloader.gate.clear()
    await setup.start_download()
    await asyncio.sleep(0.01)
    s = setup.download_status()
    assert s["state"] == "running" and s["done"] == 0 < s["total"]
    again = await setup.start_download()  # un secondo clic non fa partire un secondo download
    assert again["state"] == "running"
    setup.downloader.gate.set()
    await setup._task
    assert len(setup.downloader.runs) == 1


async def test_files_already_present_count_as_done_from_the_start(setup):
    await setup.check_pc()
    first = setup.plan()[0]
    out = setup.downloader.target(first.dest)
    out.parent.mkdir(parents=True)
    out.write_bytes(b"x")
    setup.downloader.gate.clear()
    await setup.start_download()
    await asyncio.sleep(0.01)
    assert setup.download_status()["done"] == first.size
    assert first.dest not in setup.downloader.runs[0]
    setup.downloader.gate.set()
    await setup._task


async def test_speed_is_measured_over_the_last_seconds(setup):
    now = [100.0]
    setup.clock = lambda: now[0]
    await setup.check_pc()
    setup._files = setup.plan()
    setup._bytes = {f.dest: 0 for f in setup._files}
    name = setup._files[0].dest
    setup._report(0, 10**9, name)
    now[0] += 2
    setup._report(4 * 2**20, 10**9, name)
    assert setup.download_status()["speed"] == 2 * 2**20


@pytest.mark.parametrize("permanent", [True, False])
async def test_download_error_is_reported_and_can_be_retried(setup, permanent):
    setup.downloader.fail = DownloadError("voce: impronta diversa", permanent=permanent)
    status = await download_all(setup)
    assert status["state"] == "error" and status["permanent"] is permanent
    assert "impronta" in status["error"]
    setup.downloader.fail = None
    await setup.start_download()
    await setup._task
    assert setup.download_status()["state"] == "done"


async def test_unexpected_error_does_not_crash_the_setup(setup):
    setup.downloader.fail = PermissionError("accesso negato")
    status = await download_all(setup)
    assert status["state"] == "error" and not status["permanent"]


async def test_cancel_leaves_the_download_idle(setup):
    await setup.check_pc()
    setup.downloader.gate.clear()
    await setup.start_download()
    await asyncio.sleep(0.01)
    await setup.cancel_download()
    assert setup.download_status()["state"] == "idle"


# --- la fine ---


async def test_finish_needs_the_download(setup):
    with pytest.raises(flow.SetupError, match="controllo_pc"):
        setup.finish("Andrea", True)
    await setup.check_pc()
    with pytest.raises(flow.SetupError, match="download_incompleto"):
        setup.finish("Andrea", True)


async def test_finish_saves_everything_and_says_the_voice_is_missing(setup, tmp_path):
    await download_all(setup)
    r = setup.finish("  Andrea \n", True)
    assert r == {"ok": False, "problem": "voce_mancante"}
    assert not setup.finished.is_set() and not setup.done_path.exists()
    c = ConfigStore(tmp_path / "config.yaml").config
    assert (c.persona.user_name, c.system.autostart, c.speech.tts_model) == ("Andrea", True, "qwen3-1.7b")


async def test_finish_with_a_voice_starts_jarvis(setup):
    give_voice(setup)
    await download_all(setup)
    assert setup.finish("", False) == {"ok": True}
    assert setup.finished.is_set()
    assert json.loads(setup.done_path.read_text(encoding="utf-8"))["profile"] == "completo"
    assert setup.store.config.persona.user_name == "Utente"  # nome vuoto: resta quello di prima


@pytest.mark.parametrize(("lang", "voice"), [("it", "base-it"), ("en", "base-en")])
async def test_a_new_pc_gets_the_included_voice_of_its_language(setup, lang, voice):
    from jarvis.speech.engine import BUNDLED_VOICES

    setup.bundled_voices = BUNDLED_VOICES
    setup.set_language(lang)
    await download_all(setup)
    assert setup.finish("", False) == {"ok": True}
    assert setup.store.config.speech.tts_voice == voice


async def test_your_own_voice_is_kept(setup):
    from jarvis.speech.engine import BUNDLED_VOICES

    setup.bundled_voices = BUNDLED_VOICES
    give_voice(setup)  # la voce del film, nei tuoi dati
    await download_all(setup)
    setup.finish("", False)
    assert setup.store.config.speech.tts_voice == "jarvis"


async def test_light_profile_picks_the_small_voice(setup):
    give_voice(setup)
    setup.gather = lambda: facts(pc.Gpu("RTX 2060", "nvidia", 6144, "610.0"))
    await download_all(setup)
    setup.finish("", False)
    assert setup.store.config.speech.tts_model == "qwen3-0.6b"


# --- Claude Code ---


@pytest.mark.parametrize(("text", "logged", "plan"), [
    ('{"loggedIn": true, "subscriptionType": "max", "authMethod": "claude.ai"}', True, "max"),
    ('{"loggedIn": "true"}', False, ""),
    ('{"loggedIn": false}', False, ""),
    ("[1, 2]", False, ""), ("non json", False, ""), ("", False, ""),
])
def test_status_parsing(text, logged, plan):
    s = brains.parse_status(text)
    assert (s.installed, s.logged_in, s.plan) == (True, logged, plan)


def test_status_without_claude_code():
    assert FakeClaude(installed=False).status() == brains.ClaudeStatus(False)


def test_status_that_times_out_means_not_logged_in():
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    c = brains.ClaudeCode(find=lambda: "claude.exe", run=slow, popen=lambda *a, **k: None)
    assert c.status() == brains.ClaudeStatus(True)


def test_login_and_install_open_a_visible_window():
    seen = []
    c = brains.ClaudeCode(find=lambda: "claude.exe", run=None, popen=lambda cmd, **kw: seen.append(kw["creationflags"]))
    c.login()
    c.install()
    assert seen == [brains.NEW_CONSOLE, brains.NEW_CONSOLE]


# --- il server ---


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server(setup):
    srv = SetupServer(setup, port=free_port())
    await srv.start()
    yield srv
    await srv.stop()


async def call(srv, method, path, body=None, origin="mine", host=None, ctype="application/json", raw=None):
    headers = {"Host": host or f"127.0.0.1:{srv.port}"}
    if origin == "mine":
        headers["Origin"] = f"http://127.0.0.1:{srv.port}"
    elif origin:
        headers["Origin"] = origin
    data = raw if raw is not None else (json.dumps(body) if body is not None else None)
    if data is not None and ctype:
        headers["Content-Type"] = ctype
    async with aiohttp.ClientSession() as s, s.request(method, f"http://127.0.0.1:{srv.port}{path}",
                                                       data=data, headers=headers) as r:
        text = await r.text()
        return r.status, (json.loads(text) if r.content_type == "application/json" else text)


async def test_page_and_its_files_are_served(server):
    status, html = await call(server, "GET", "/")
    assert status == 200 and "/static/setup.js?v=" in html and "/static/setup.css?v=" in html
    assert (await call(server, "GET", "/static/setup.js"))[0] == 200


async def test_a_whole_run_through_the_server(server, setup):
    give_voice(setup)
    assert (await call(server, "GET", "/api/start"))[1]["provider"] == "claude"
    assert (await call(server, "POST", "/api/lang", {"lang": "en"}))[0] == 200
    status, data = await call(server, "GET", "/api/pc")
    assert data["profile"]["ok"]
    assert (await call(server, "POST", "/api/brain", {"provider": "opencode"}))[0] == 200
    status, data = await call(server, "POST", "/api/download", {})
    assert status == 200
    await setup._task
    assert (await call(server, "GET", "/api/download"))[1]["state"] == "done"
    status, data = await call(server, "POST", "/api/finish", {"name": "Andrea", "autostart": "sì"})
    assert data == {"ok": True}
    assert setup.store.config.system.autostart is False  # solo un vero "true" accende l'avvio con Windows
    assert setup.store.config.brain.provider == "opencode" and setup.store.config.persona.language == "en"


async def test_setup_errors_come_back_as_codes(server):
    status, data = await call(server, "POST", "/api/download", {})
    assert (status, data) == (409, {"error": "controllo_pc"})
    status, data = await call(server, "POST", "/api/lang", {"lang": "fr"})
    assert (status, data) == (409, {"error": "lingua_sconosciuta"})


@pytest.mark.parametrize("raw", ["{rotto", "[1, 2]", "\"testo\""])
async def test_bad_bodies_are_refused(server, raw):
    status, data = await call(server, "POST", "/api/lang", raw=raw)
    assert status == 400 and data == {"error": "richiesta_non_valida"}


async def test_a_web_page_cannot_drive_the_setup(server, setup):
    # un sito aperto nel browser: Origin suo
    assert (await call(server, "POST", "/api/brain", {"provider": "opencode"}, origin="https://sito.example"))[0] == 403
    # un modulo HTML semplice (niente JSON): nemmeno dalla nostra origine
    assert (await call(server, "POST", "/api/quit", raw="x=1", ctype="application/x-www-form-urlencoded"))[0] == 403
    # DNS rebinding: un nome qualsiasi che punta a 127.0.0.1
    assert (await call(server, "GET", "/api/start", host="evil.example:80"))[0] == 403
    assert server.refused == 3
    assert setup.store.config.brain.provider == "claude" and not setup.quit.is_set()


async def test_local_programs_without_origin_are_accepted(server, setup):
    assert (await call(server, "POST", "/api/quit", {}, origin=None))[0] == 200
    assert setup.quit.is_set()


async def test_run_without_window_ends_when_closed(tmp_path, monkeypatch):
    from jarvis.setup import run as run_mod

    store = ConfigStore(tmp_path / "config.yaml")
    store.set("ui.port", free_port())
    made = {}

    def fake_setup(st):
        made["s"] = flow.Setup(st, FakeDownloader(tmp_path), gather=lambda: facts(RTX), claude=FakeClaude(),
                               voices_dir=tmp_path / "voices", done_path=tmp_path / "fatto.json",
                               missing_tools=lambda: [])
        return made["s"]

    monkeypatch.setattr(run_mod, "make_setup", fake_setup)
    task = asyncio.ensure_future(run_mod.run(store, window=False))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if "s" in made:
            break
    port = store.config.ui.port
    async with aiohttp.ClientSession() as s, s.post(f"http://127.0.0.1:{port}/api/quit", json={}) as r:
        assert r.status == 200
    assert await asyncio.wait_for(task, 5) is False
    with socket.socket() as sock:  # il server si è spento con la procedura: la porta è di nuovo libera
        sock.bind(("127.0.0.1", port))


async def test_main_without_window_does_not_start_a_half_installed_jarvis(monkeypatch):
    from jarvis import __main__ as entry

    monkeypatch.setattr(flow, "missing_files", lambda cfg: [Path("audiocpp_server.exe")])
    assert await entry.main(console=False, window=False) == 2


# --- microfono e casse ---


@pytest.fixture
def heard(setup):
    from test_setup_audio import FakeSD, find

    from jarvis.setup.audio_check import AudioCheck

    setup._audio = AudioCheck(sd=FakeSD(), find=find)
    return setup


def test_devices_come_with_the_current_choice(heard):
    heard.store.set("audio.output_device", "Cuffie (Bluetooth)")
    d = heard.audio_devices()
    assert d["outputs"] == ["Altoparlanti (Realtek)", "Cuffie (Bluetooth)"]
    assert (d["input"], d["output"]) == ("", "Cuffie (Bluetooth)")


def test_chosen_devices_are_saved_and_checked(heard, tmp_path):
    heard.set_audio("Microfono (USB)", "")
    c = ConfigStore(tmp_path / "config.yaml").config
    assert (c.audio.input_device, c.audio.output_device) == ("Microfono (USB)", None)  # vuoto = predefinito
    with pytest.raises(flow.SetupError, match="dispositivo_sconosciuto"):
        heard.set_audio("Microfono che non c'è", "")
    assert heard.store.config.audio.input_device == "Microfono (USB)"


def test_microphone_test_opens_the_chosen_microphone(heard):
    heard.store.set("audio.input_device", "usb")
    m = heard.start_mic()
    assert m["running"] and heard.audio.sd.streams[-1].kw["device"] == 0
    heard.stop_mic()
    assert heard.audio.sd.streams[-1].closed


async def test_speaker_test_plays_the_included_voice_of_the_language(heard):
    from jarvis.speech.engine import BUNDLED_VOICES

    heard.bundled_voices = BUNDLED_VOICES
    heard.set_language("en")
    heard.store.set("audio.output_device", "cuffie")
    r = heard.play_test()
    assert r["line"].startswith("Good evening") and r["seconds"] > 2
    assert heard.audio.sd.played[-1][2] == 2


def test_speakers_that_do_not_work_are_reported(heard):
    from jarvis.speech.engine import BUNDLED_VOICES

    heard.bundled_voices = BUNDLED_VOICES
    heard.store.set("audio.output_device", "non c'è")
    with pytest.raises(flow.SetupError, match="casse_non_disponibili"):
        heard.play_test()


async def test_audio_through_the_server(server, heard):
    status, data = await call(server, "GET", "/api/audio")
    assert status == 200 and data["default_input"] == "Microfono (USB)"
    assert (await call(server, "POST", "/api/audio", {"input": "", "output": "Cuffie (Bluetooth)"}))[0] == 200
    status, m = await call(server, "POST", "/api/mic", {})
    assert m["running"]
    assert (await call(server, "GET", "/api/mic"))[1]["running"]
    assert (await call(server, "POST", "/api/mic/stop", {}))[0] == 200
    assert (await call(server, "POST", "/api/audio", {"input": "x", "output": ""})) == \
           (409, {"error": "dispositivo_sconosciuto"})


def test_choosing_english_in_the_setup_switches_the_included_voice(setup):
    setup.store.set("speech.tts_voice", "base-it")
    setup.set_language("en")
    assert setup.store.config.speech.tts_voice == "base-en"


# --- gli strumenti (OpenCode, Node.js, Git) ---


async def test_tools_missing_on_the_pc_come_with_the_download_on_their_own_row(setup):
    await setup.check_pc()
    assert setup.tools == list(catalog.TOOLS)
    tool_files = {f.dest for k in catalog.TOOLS for f in catalog.PACKAGES[k].files}
    assert tool_files <= {f.dest for f in setup.plan()}
    status = await download_all(setup)
    assert status["groups"][-1]["key"] == "tools"
    assert status["groups"][-1]["total"] == sum(catalog.PACKAGES[k].size for k in catalog.TOOLS)


async def test_a_pc_that_has_its_tools_downloads_none_of_them(setup):
    setup.missing_tools = lambda: []
    status = await download_all(setup)
    assert "tools" not in [g["key"] for g in status["groups"]]
    assert not any(f.dest.startswith("engine/tools/") for f in setup.plan())


async def test_only_tool_packages_can_be_added_to_the_plan(setup):
    setup.missing_tools = lambda: ["tool-git", "voice-small", "nulla"]  # un controllo sbagliato non cambia la voce
    await setup.check_pc()
    assert setup.tools == ["tool-git"]
    assert [f.dest for f in setup.plan()][-1] == catalog.PACKAGES["tool-git"].files[0].dest
    assert catalog.PACKAGES["voice-small"].files[0].dest not in {f.dest for f in setup.plan()}


def test_every_row_of_the_download_has_its_name_in_both_languages():
    js = (Path(flow.__file__).parent / "web" / "setup.js").read_text(encoding="utf-8")
    for g in flow.GROUPS:
        assert js.count(f"dl_{g}:") == 2, g  # italiano e inglese

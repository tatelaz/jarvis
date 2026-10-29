"""Sotto-agenti su OpenCode quando Claude è esaurito: stessa politica di sicurezza, stesse conferme a voce, compiti
in parallelo, passaggio da Claude a OpenCode prima o durante un compito. Contro il finto `opencode serve`."""

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

from jarvis.brain.mcp_bridge import McpBridge
from jarvis.brain.opencode import WORKER_AGENT, OpenCodeClient
from jarvis.brain.zen import ExamResult, ModelPicker
from jarvis.config import ConfigStore, HandsConfig
from jarvis.core.bus import Bus
from jarvis.opencode_kit import OpenCodeKit
from jarvis.tasks import hands, opencode_worker
from jarvis.tasks.hands import DesktopLease
from jarvis.tasks.manager import Task, TaskManager, WorkerHooks
from jarvis.tasks.opencode_worker import (
    NOT_APPROVED,
    OPENCODE_PROMPT,
    RESUME_NOTE,
    ClaudeQuota,
    ConfirmDesk,
    FallbackWorker,
    OpenCodeWorker,
    claude_args,
    claude_name,
    opencode_hands,
    worker_tools,
)
from jarvis.tasks.safety import SafetyPolicy

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from fake_opencode import CATALOG, FakeOpenCode

GLM = "glm-5.3-flash-free"


class StubHub:
    def __init__(self, client, directory="C:/jarvis/voce"):
        self.client, self.directory, self.picker = client, directory, None

    async def ensure(self):
        return self.client

    @contextlib.asynccontextmanager
    async def using(self):
        yield self.client


class Hooks:
    """Il gestore dei compiti finto: annota i passi e risponde alle conferme come gli si dice."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.asked, self.steps = [], []

    def hooks(self):
        return WorkerHooks(progress=self.steps.append, confirm=self.confirm)

    async def confirm(self, text):
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else False


@pytest.fixture
async def env(tmp_path):
    fake = FakeOpenCode()
    url = await fake.start()
    client = OpenCodeClient(url, fake.password)
    await client.start()
    desk = ConfirmDesk(wait_s=1.0)
    bridge = McpBridge()
    bridge.mount("lavoro", OpenCodeKit._worker_registry(), desk.intercept)
    await bridge.start()
    fake.mcp = bridge.opencode_config()
    work = tmp_path / "workspace"
    work.mkdir()
    yield {"fake": fake, "client": client, "hub": StubHub(client), "desk": desk, "work": work, "tmp": tmp_path}
    await bridge.stop()
    await client.close()
    await fake.stop()


def make_worker(env, tid=1, answers=(), desktop=None, guide=None):
    cwd = env["work"] / f"compito_{tid:03d}"
    task = Task(tid, f"compito {tid}", "prepara un riepilogo", cwd)
    w = OpenCodeWorker(task, "Andrea", env["hub"], SafetyPolicy([env["work"]]), lambda: [GLM], env["desk"],
                       guide=guide, desktop=desktop)
    h = Hooks(answers)
    return task, w, h


# ---------------------------------------------------------------------- il lavoro


async def test_task_runs_in_its_own_session_and_returns_the_summary(env):
    fake = env["fake"]

    async def behave(turn, text):
        await turn.tool("bash", {"command": "dir", "description": "elenco"}, output="file.txt", ask="bash")
        await turn.tool("lavoro_riferisci", {"testo": "metà fatto"}, output="ricevuto", ask="lavoro_riferisci")
        await turn.tool("read", {"filePath": "C:/x.txt"}, output="ciao")  # leggere non chiede niente
        await turn.say("Ho creato il riepilogo in riepilogo.txt.")

    fake.behave = behave
    task, w, h = make_worker(env)
    out = await w.run(task, h.hooks())
    assert out == "Ho creato il riepilogo in riepilogo.txt."
    assert h.asked == []  # niente di pericoloso: nessuna domanda all'utente
    assert h.steps[0] == f"lavoro con OpenCode ({GLM})" and "esegue: dir" in h.steps and "metà fatto" in h.steps
    assert "legge x.txt" in h.steps
    body = fake.prompts[0]["body"]
    assert body["agent"] == WORKER_AGENT and body["model"]["modelID"] == GLM
    assert "Compito: prepara un riepilogo" in body["parts"][0]["text"]
    assert "#1" in body["system"] and "lavoro_chiedi_conferma" in body["system"]
    assert fake.prompts[0]["directory"] == str(task.cwd) and task.cwd.is_dir()
    assert all(r["reply"] == "once" for r in fake.replies.values())


async def test_dangerous_commands_use_the_same_policy_and_voice_confirmation(env):
    fake = env["fake"]

    async def behave(turn, text):
        await turn.tool("bash", {"command": "rm -rf build"}, output="", ask="bash")
        await turn.tool("bash", {"command": "Remove-Item -Recurse vecchi"}, output="", ask="bash")
        await turn.tool("bash", {"command": "Remove-Item -Recurse vecchi"}, output="", ask="bash")  # già approvato
        await turn.say("Fatto.")

    fake.behave = behave
    task, w, h = make_worker(env, answers=[False, True])
    assert await w.run(task, h.hooks()) == "Fatto."
    assert len(h.asked) == 2 and all("cancellazione ricorsiva" in a for a in h.asked)
    got = [fake.replies[a["id"]] for a in fake.asked]
    assert got[0] == {"reply": "reject", "message": NOT_APPROVED}
    assert got[1] == {"reply": "once"} and got[2] == {"reply": "once"}  # niente doppia domanda


async def test_file_edits_inside_are_free_outside_are_asked(env):
    fake = env["fake"]
    inside = env["work"] / "compito_001" / "nota.txt"
    outside = env["tmp"] / "fuori.txt"

    async def behave(turn, text):
        await turn.tool("write", {"filePath": str(inside), "content": "x"}, output="ok", ask="edit")
        await turn.tool("edit", {"filePath": str(outside), "oldString": "a", "newString": "b"}, output="ok",
                        ask="edit")
        await turn.tool("write", {"filePath": "relativo.txt", "content": "x"}, output="ok", ask="edit")
        # una richiesta senza la parte dello strumento: il percorso relativo parte da "/", mai dal compito
        await turn.fake.ask(turn.sid, "edit", ["Users/Andrea/Desktop/segreto.txt"], {}, None)
        await turn.say("Fatto.")

    fake.behave = behave
    task, w, h = make_worker(env, answers=[True, False])
    await w.run(task, h.hooks())
    assert len(h.asked) == 2
    assert str(outside) in h.asked[0] and "scrivere fuori dalle cartelle di lavoro" in h.asked[0]
    assert "segreto.txt" in h.asked[1]
    got = [fake.replies[a["id"]]["reply"] for a in fake.asked]
    assert got == ["once", "once", "once", "reject"]  # dentro, fuori approvato, relativo al compito, fuori negato


async def test_browser_purchases_ask_and_unsafe_code_is_blocked(env):
    fake = env["fake"]

    async def behave(turn, text):
        await turn.tool("browser_browser_navigate", {"url": "https://example.com"}, output="ok", ask="x")
        await turn.tool("browser_browser_click", {"element": "Acquista ora", "ref": "e1"}, output="ok", ask="x")
        await turn.tool("browser_browser_run_code_unsafe", {"code": "1"}, output="ok", ask="x")
        await turn.tool("jarvis_impostazione", {"chiave": "a", "valore": "b"}, output="ok", ask="x")
        await turn.say("Fatto.")

    fake.behave = behave
    task, w, h = make_worker(env, answers=[False])
    await w.run(task, h.hooks())
    assert h.asked == ["nel browser: Acquista ora"]  # il codice bloccato non si chiede nemmeno
    got = [fake.replies[a["id"]]["reply"] for a in fake.asked]
    assert got == ["once", "reject", "reject", "reject"]
    assert "bloccato" in fake.replies[fake.asked[2]["id"]]["message"]


async def test_desktop_is_one_task_at_a_time_and_keys_never_hit_jarvis(env, monkeypatch):
    fake = env["fake"]
    lease = DesktopLease()
    lease.acquire(99)
    focus = {"v": ("Blocco note", "notepad.exe")}
    monkeypatch.setattr(hands, "foreground_window", lambda: focus["v"])

    async def behave(turn, text):
        await turn.tool("windows_Snapshot", {}, output="albero", ask="x")  # guardare non serve il desktop
        await turn.tool("windows_Click", {"loc": [1, 2]}, output="ok", ask="x")  # occupato dal compito 99
        lease.release(99)
        focus["v"] = ("Jarvis", "python.exe")
        await turn.tool("windows_Shortcut", {"shortcut": "ctrl+w"}, output="ok", ask="x")
        await turn.say("Fatto.")

    fake.behave = behave
    task, w, h = make_worker(env, desktop=lease)
    await w.run(task, h.hooks())
    msgs = [fake.replies[a["id"]] for a in fake.asked]
    assert msgs[0] == {"reply": "once"}
    assert msgs[1]["reply"] == "reject" and "occupato dal compito #99" in msgs[1]["message"]
    assert msgs[2]["reply"] == "reject" and "finestra di Jarvis" in msgs[2]["message"]
    assert lease.owner is None  # a fine compito il desktop torna libero


async def test_doom_loops_outside_reads_and_errors(env):
    fake = env["fake"]

    async def behave(turn, text):
        await turn.fake.ask(turn.sid, "doom_loop", ["*"], {}, None)
        await turn.fake.ask(turn.sid, "external_directory", ["C:/Users/Andrea/Documenti/*"], {}, None)
        turn.error = None

    fake.behave = behave
    task, w, h = make_worker(env)
    await w.run(task, h.hooks())
    got = [fake.replies[a["id"]] for a in fake.asked]
    assert got[0]["reply"] == "reject" and "ripetendo" in got[0]["message"]
    assert got[1] == {"reply": "once"}  # guardare fuori è libero; scrivere fuori chiede il suo permesso


async def test_session_error_fails_the_task(env):
    fake = env["fake"]

    async def behave(turn, text):
        await turn.emit("session.error", error={"name": "APIError", "data": {"message": "server esploso",
                                                                              "isRetryable": False}})

    fake.behave = behave
    task, w, h = make_worker(env)
    with pytest.raises(RuntimeError, match="server esploso"):
        await w.run(task, h.hooks())


async def test_free_limit_stops_the_task_with_the_reset_time(env):
    fake = env["fake"]

    async def behave(turn, text):
        await turn.status("retry", attempt=1, message="Free usage exceeded", next=1793232000881,
                          action={"reason": "free_tier_limit"})
        await turn.wait_abort()

    fake.behave = behave
    task, w, h = make_worker(env)
    with pytest.raises(RuntimeError, match="uso gratuito di OpenCode esaurito per ora"):
        await asyncio.wait_for(w.run(task, h.hooks()), 5)
    assert fake.prompts[0]["sid"] in fake.aborted


async def test_messages_during_work_and_cancel(env):
    fake = env["fake"]
    started = asyncio.Event()

    async def behave(turn, text):
        started.set()
        await turn.wait_abort(timeout=10)

    fake.behave = behave
    task, w, h = make_worker(env)
    run = asyncio.ensure_future(w.run(task, h.hooks()))
    await asyncio.wait_for(started.wait(), 5)
    await w.send("aggiungi anche la parte B")
    assert fake.prompts[-1]["async"] and "aggiungi anche la parte B" in fake.prompts[-1]["body"]["parts"][0]["text"]
    assert fake.prompts[-1]["sid"] == fake.prompts[0]["sid"]  # stessa sessione: il modello lo legge al passo dopo
    await w.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 5)
    assert fake.prompts[0]["sid"] in fake.aborted


# ---------------------------------------------------------------------- conferme chieste dal modello


async def test_chiedi_conferma_asks_the_user_and_the_tool_obeys(env):
    """Il modello chiede conferma: la domanda arriva a voce, e lo strumento dice APPROVATA solo dopo un sì vero."""
    fake = env["fake"]
    outputs = []

    async def behave(turn, text):
        for azione in ("inviare la mail a Marco", "comprare il biglietto"):
            await turn.tool("lavoro_chiedi_conferma", {"azione": azione}, ask="lavoro_chiedi_conferma",
                            call_mcp=True)
            outputs.append(turn.parts[-1]["state"])
        # se il permesso non passasse da Jarvis: la conferma la chiede lo stesso, dal compito giusto
        await turn.tool("lavoro_chiedi_conferma", {"azione": "pubblicare il post"}, call_mcp=True)
        outputs.append(turn.parts[-1]["state"])
        await turn.say("Fatto.")

    fake.behave = behave
    task, w, h = make_worker(env, answers=[True, False, True])
    await w.run(task, h.hooks())
    assert h.asked == ["inviare la mail a Marco", "comprare il biglietto", "pubblicare il post"]
    assert outputs[0]["output"] == "APPROVATA"
    assert outputs[1]["status"] == "error" and "NEGATA" in outputs[1]["error"]
    assert outputs[2]["output"] == "APPROVATA"
    # nessun compito sta facendo quella chiamata: mai indovinare, sempre NEGATA
    assert (await env["desk"].intercept("chiedi_conferma", {"azione": "a caso"})).startswith("NEGATA")
    assert await env["desk"].intercept("riferisci", {"testo": "x"}) == "ricevuto"
    assert await env["desk"].intercept("altro", {}) is None


def test_desk_approval_is_single_use():
    desk = ConfirmDesk()
    desk.approve({"azione": "x"})
    assert desk.take({"azione": "x"}) and not desk.take({"azione": "x"})


# ---------------------------------------------------------------------- compiti in parallelo e ripiego


class FakeClaude:
    def __init__(self, error=None):
        self.error, self.ran, self.cancelled, self.sent = error, False, False, []

    async def run(self, task, hooks):
        self.ran = True
        if self.error:
            raise self.error
        return "fatto da Claude"

    async def send(self, text):
        self.sent.append(text)

    async def cancel(self):
        self.cancelled = True


class FakeOpen(FakeClaude):
    def __init__(self, note):
        super().__init__()
        self.note = note

    async def run(self, task, hooks):
        self.ran = True
        return "fatto da OpenCode"


async def test_parallel_tasks_each_get_their_own_session(env):
    fake = env["fake"]
    now, most = set(), [0]

    async def behave(turn, text):
        now.add(turn.sid)
        most[0] = max(most[0], len(now))
        await asyncio.sleep(0.3)
        now.discard(turn.sid)
        await turn.say(f"Fatto: {text[:20]}")

    fake.behave = behave
    quota = ClaudeQuota(lambda: 4_000_000_000.0)  # Claude esaurito

    def factory(task):
        return FallbackWorker(task, lambda: FakeClaude(), lambda note: OpenCodeWorker(
            task, "Andrea", env["hub"], SafetyPolicy([env["work"]]), lambda: [GLM], env["desk"], note=note),
            quota, enabled=lambda: True)

    notes = []
    tm = TaskManager(Bus(), notes.append, factory, env["work"], max_parallel=3)
    ts = [tm.spawn(f"compito numero {i}") for i in range(3)]
    for _ in range(200):
        if all(t.status == "completato" for t in ts):
            break
        await asyncio.sleep(0.05)
    assert [t.status for t in ts] == ["completato"] * 3
    assert len({p["sid"] for p in fake.prompts}) == 3 and most[0] == 3  # tre sessioni, tutte insieme
    assert all(t.result.startswith("Fatto: Compito: compito") for t in ts)


async def test_fallback_uses_claude_normally_and_opencode_only_when_exhausted():
    made = []

    def opencode(note):
        made.append(FakeOpen(note))
        return made[-1]

    task = Task(1, "t", "g", Path("."))
    h = Hooks()
    clock = {"t": 1_800_000_000.0}
    quota = ClaudeQuota(lambda: None, clock=lambda: clock["t"])
    claude = FakeClaude()
    w = FallbackWorker(task, lambda: claude, opencode, quota, enabled=lambda: True)
    assert await w.run(task, h.hooks()) == "fatto da Claude" and made == []  # di norma sempre Claude
    # Claude esaurito prima di cominciare: dritto a OpenCode
    quota.mark(clock["t"] + 600)
    w = FallbackWorker(task, lambda: FakeClaude(), opencode, quota, enabled=lambda: True)
    assert await w.run(task, h.hooks()) == "fatto da OpenCode" and made[-1].note == ""
    clock["t"] += 601
    assert quota.exhausted_until() is None  # passato il ripristino si torna a Claude


async def test_fallback_switches_mid_task_when_claude_hits_the_limit():
    made = []
    task = Task(1, "t", "g", Path("."))
    h = Hooks()
    quota = ClaudeQuota()
    limit = RuntimeError("You've hit your session limit · resets 8:20pm")
    w = FallbackWorker(task, lambda: FakeClaude(limit), lambda note: made.append(FakeOpen(note)) or made[-1], quota,
                       enabled=lambda: True)
    assert await w.run(task, h.hooks()) == "fatto da OpenCode"
    assert made[0].note == RESUME_NOTE and "non ripetere" in RESUME_NOTE
    assert any("OpenCode" in s for s in h.steps) and quota.exhausted_until() is not None
    # un errore qualsiasi di Claude resta un errore (OpenCode non è la toppa per tutto)
    with pytest.raises(RuntimeError, match="disco pieno"):
        await FallbackWorker(task, lambda: FakeClaude(RuntimeError("disco pieno")), lambda n: FakeOpen(n), ClaudeQuota(),
                             enabled=lambda: True).run(task, h.hooks())
    # sotto-agenti OpenCode spenti a voce: il compito si ferma come prima
    with pytest.raises(RuntimeError, match="session limit"):
        await FallbackWorker(task, lambda: FakeClaude(limit), lambda n: FakeOpen(n), ClaudeQuota(),
                             enabled=lambda: False).run(task, h.hooks())
    # messaggi e annullamento vanno a chi sta lavorando
    claude = FakeClaude()
    w = FallbackWorker(task, lambda: claude, lambda n: FakeOpen(n), ClaudeQuota(), enabled=lambda: True)
    await w.run(task, h.hooks())
    await w.send("ciao")
    await w.cancel()
    assert claude.sent == ["ciao"] and claude.cancelled


def test_claude_quota():
    clock = {"t": 1_000.0}
    router = {"v": None}
    q = ClaudeQuota(lambda: router["v"], clock=lambda: clock["t"])
    assert q.exhausted_until() is None
    router["v"] = 2_000.0
    assert q.exhausted_until() == 2_000.0
    q.mark(None)  # senza ora di ripristino: un'ora
    assert q.exhausted_until() == 1_000.0 + 3600
    q.mark(500.0)  # un'ora già passata non accorcia niente
    assert q.exhausted_until() == 1_000.0 + 3600
    clock["t"] = 10_000.0
    assert q.exhausted_until() is None


# ---------------------------------------------------------------------- nomi, mani, prompt


def test_names_and_args_as_in_claude_code():
    assert claude_name("bash") == "Bash" and claude_name("write") == "Write" and claude_name("apply_patch") == "Edit"
    assert claude_name("browser_browser_click") == "mcp__browser__browser_click"
    assert claude_name("windows_Click") == "mcp__windows__Click"
    assert claude_name("lavoro_riferisci") == "mcp__lavoro__riferisci"
    assert claude_name("strano_strumento") == "strano_strumento"
    assert claude_args({"filePath": "C:/a.txt", "content": "x"}) == {"filePath": "C:/a.txt", "content": "x",
                                                                    "file_path": "C:/a.txt"}
    assert claude_args({"command": "dir"}) == {"command": "dir"}


def test_hands_for_opencode(tmp_path, monkeypatch):
    play, win = tmp_path / "cli.js", tmp_path / "windows-mcp.exe"
    play.write_text("")
    win.write_text("")
    monkeypatch.setattr(opencode_worker, "PLAYWRIGHT_MCP", play)
    monkeypatch.setattr(opencode_worker, "WINDOWS_MCP", win)
    monkeypatch.setattr(opencode_worker.shutil, "which", lambda name: "C:/node/node.exe")
    mcp, guide = opencode_hands(HandsConfig(), "Andrea")
    assert mcp["browser"]["type"] == "local" and mcp["browser"]["command"][:2] == ["C:/node/node.exe", str(play)]
    assert "--isolated" in mcp["browser"]["command"] and "--headless" in mcp["browser"]["command"]
    tools = mcp["windows"]["command"][mcp["windows"]["command"].index("--tools") + 1].split(",")
    assert "PowerShell" not in tools and "Click" in tools  # solo interfaccia, come per Claude
    assert set(mcp) == {"browser", "windows"} and len(guide) == 2  # il browser di Andrea c'è solo in Claude Code
    assert all("Andrea" in g or "{user}" not in g for g in guide)
    mcp, guide = opencode_hands(HandsConfig(disposable_browser=False, desktop=False), "Andrea")
    assert mcp == {} and guide == []
    assert worker_tools() == {"browser_browser_run_code_unsafe": False}


def test_worker_prompt_names_the_opencode_tools(env):
    task, w, _ = make_worker(env, tid=7, guide=["- Computer (strumenti windows): prova"])
    text = w.prompt()
    assert "Hai ricevuto il compito #7" in text and "`lavoro_riferisci`" in text and "`lavoro_chiedi_conferma`" in text
    assert "`riferisci`" not in text and "strumenti windows" in text and str(task.cwd) in text
    assert "{" not in OPENCODE_PROMPT.replace("{user}", "").replace("{cwd}", "").replace("{hands}", "").replace(
        "{task}", "")


# ---------------------------------------------------------------------- il kit (impostazioni e manutenzione)


class KitHub:
    def __init__(self):
        self.mounted, self.idle_checks, self.running, self.keys = {}, 0, False, None

    def mount(self, name, registry, intercept=None):
        self.mounted[name] = (registry, intercept)

    async def stop_if_idle(self):
        self.idle_checks += 1
        return False

    async def stop(self):
        pass

    @contextlib.asynccontextmanager
    async def using(self):
        yield None


async def test_kit_maintenance_exam_only_in_quiet_and_settings_live(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.yaml")
    hub = KitHub()
    kit = OpenCodeKit(store, base=tmp_path / "oc", hub=hub)
    assert set(hub.mounted) == {"esame", "lavoro"}
    started = []
    monkeypatch.setattr(kit, "start_exam", lambda: started.append(1) or "avviato")
    await kit.tick(quiet_for=600, claude_exhausted=False)
    assert started == [1] and hub.idle_checks == 1  # mai fatto: si accende OpenCode, che dà l'elenco, e si esamina
    await kit.tick(quiet_for=600, claude_exhausted=False)
    assert started == [1]  # al più un tentativo all'ora (magari OpenCode non va)
    kit._last_try = float("-inf")
    kit.picker.set_catalog(CATALOG)
    await kit.tick(quiet_for=30, claude_exhausted=False)
    assert started == [1]  # mai mentre si parla con Jarvis
    await kit.tick(quiet_for=600, claude_exhausted=True)
    assert started == [1, 1]  # mai fatto: anche con Claude esaurito (serve sapere chi usare)
    kit._last_try = float("-inf")
    kit.picker.record([ExamResult(m.id, True, True, 1.0) for m in kit.picker.catalog])
    kit.picker.exam_at -= 100 * 3600
    await kit.tick(quiet_for=600, claude_exhausted=True)
    assert started == [1, 1]  # con Claude esaurito l'uso gratuito serve alle risposte, non all'esame
    await kit.tick(quiet_for=600, claude_exhausted=False)
    assert started == [1, 1, 1]
    kit._last_try = float("-inf")
    store.set("brain.opencode", False)
    await kit.tick(quiet_for=600, claude_exhausted=False)
    assert started == [1, 1, 1]  # spento a voce: niente esami
    store.set("brain.opencode", True)
    hub.installed = lambda: False
    await kit.tick(quiet_for=600, claude_exhausted=False)
    assert started == [1, 1, 1]  # OpenCode non installato: niente tentativi a vuoto
    # impostazioni lette al momento: modello dei sotto-agenti, privacy
    store.set("brain.opencode_worker_model", "big-pickle")
    w = kit.worker(Task(1, "t", "g", tmp_path / "w"), SafetyPolicy([tmp_path]), None)
    assert w.models()[0] == "big-pickle"
    store.set("brain.opencode_privacy", "senza addestramento")
    assert kit.picker.ranking() == [GLM]
    assert "senza chiave" in kit.describe() and "OpenCode acceso" in kit.describe()
    assert kit.brain("p").enabled() and not kit.text_brain("s").prefix.startswith("jarvis")


async def test_kit_start_exam_runs_once_at_a_time(tmp_path, monkeypatch):
    kit = OpenCodeKit(ConfigStore(tmp_path / "config.yaml"), base=tmp_path / "oc", hub=KitHub())
    gate = asyncio.Event()

    async def slow_run(limit=5):
        await gate.wait()
        return []

    monkeypatch.setattr(kit.examiner, "run", slow_run)
    assert "avviato" in kit.start_exam()
    assert "già in corso" in kit.start_exam()
    gate.set()
    await asyncio.sleep(0.05)
    assert "avviato" in kit.start_exam()
    await kit.stop()


def test_picker_survives_a_broken_file(tmp_path):
    (tmp_path / "esame.json").write_text("{rotto", encoding="utf-8")
    p = ModelPicker(tmp_path / "esame.json")
    assert p.catalog == [] and p.best() is None and "Non ho ancora" in p.describe()

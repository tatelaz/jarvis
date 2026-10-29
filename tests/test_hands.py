from pathlib import Path

import pytest

from jarvis.config import HandsConfig
from jarvis.tasks import hands
from jarvis.tasks.claude_worker import ClaudeWorker, _describe_tool
from jarvis.tasks.hands import DesktopLease, build_hands, needs_desktop
from jarvis.tasks.manager import Task, WorkerHooks
from jarvis.tasks.safety import SafetyPolicy

REAL_FOREGROUND = hands.foreground_window


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """Finge Playwright MCP e Windows-MCP installati (i veri file non servono per costruire la configurazione)."""
    win, pw = tmp_path / "windows-mcp.exe", tmp_path / "cli.js"
    win.touch()
    pw.touch()
    monkeypatch.setattr(hands, "WINDOWS_MCP", win)
    monkeypatch.setattr(hands, "PLAYWRIGHT_MCP", pw)
    monkeypatch.setattr(hands.shutil, "which", lambda name: "C:/node/node.exe" if name == "node" else None)
    return win, pw


def test_all_hands_when_enabled_and_installed(installed):
    h = build_hands(HandsConfig(), "Andrea")
    assert h.extra_args == {"chrome": None}  # il browser dell'utente passa dall'estensione di Claude
    browser = h.mcp_servers["browser"]
    assert "--isolated" in browser["args"] and "--headless" in browser["args"]
    win = h.mcp_servers["windows"]
    assert win["env"]["ANONYMIZED_TELEMETRY"] == "false"
    tools = win["args"][win["args"].index("--tools") + 1].split(",")
    assert "Click" in tools and "Snapshot" in tools
    for unsupervised in ("PowerShell", "FileSystem", "Registry", "Process"):
        assert unsupervised not in tools  # doppioni che sfuggirebbero alla politica di sicurezza
    assert "mcp__browser__browser_run_code_unsafe" in h.disallowed
    assert len(h.guide) == 3 and all("{user}" not in g for g in h.guide) and "Andrea" in h.guide[0]
    # 27/10: con Comet 155 clic e screenshot dell'estensione falliscono; l'assistente si arrendeva
    assert "Host access is restricted by policy" in h.guide[0] and "strumenti windows" in h.guide[0]


def test_visible_disposable_browser_and_switches(installed):
    h = build_hands(HandsConfig(disposable_browser_visible=True, disposable_browser_channel="msedge"), "M")
    assert "--headless" not in h.mcp_servers["browser"]["args"]
    assert "msedge" in h.mcp_servers["browser"]["args"]
    off = build_hands(HandsConfig(user_browser=False, disposable_browser=False, desktop=False), "M")
    assert off.mcp_servers == {} and off.extra_args == {} and off.guide == []
    assert off.disallowed  # il blocco del codice arbitrario resta comunque


def test_missing_installation_degrades_quietly(tmp_path, monkeypatch):
    monkeypatch.setattr(hands, "WINDOWS_MCP", tmp_path / "manca.exe")
    monkeypatch.setattr(hands, "PLAYWRIGHT_MCP", tmp_path / "manca.js")
    h = build_hands(HandsConfig(), "M")
    assert h.mcp_servers == {} and len(h.guide) == 1  # resta solo il browser dell'utente


def test_needs_desktop_only_for_input_tools():
    assert needs_desktop("mcp__windows__Click") and needs_desktop("mcp__windows__Type")
    assert needs_desktop("mcp__windows__App")
    assert not needs_desktop("mcp__windows__Snapshot") and not needs_desktop("mcp__windows__Screenshot")
    assert not needs_desktop("mcp__browser__browser_click") and not needs_desktop("Bash")


@pytest.fixture(autouse=True)
def other_window_in_front(monkeypatch):
    """Le prove non dipendono dalla finestra che Andrea ha davanti mentre girano."""
    monkeypatch.setattr(hands, "foreground_window", lambda: ("Senza titolo - Blocco note", "notepad.exe"))


async def test_keys_never_go_to_jarvis_own_window(tmp_path, monkeypatch):
    """26/10: un Ctrl+W per chiudere una scheda di Comet è arrivato alla finestra di Jarvis (Windows aveva
    rifiutato il cambio di finestra). Ora il codice lo ferma e spiega al sotto-agente cosa fare."""
    w = _worker(tmp_path, 1, DesktopLease())
    monkeypatch.setattr(hands, "foreground_window", lambda: ("Jarvis", "pythonw.exe"))
    denied = await w._can_use_tool("mcp__windows__Shortcut", {"shortcut": "ctrl+w"}, None)
    assert denied.behavior == "deny" and "finestra di Jarvis" in denied.message
    assert (await w._can_use_tool("mcp__windows__Type", {"text": "ciao"}, None)).behavior == "deny"
    # un clic sul bersaglio sposta prima il fuoco: va bene
    assert (await w._can_use_tool("mcp__windows__Type", {"loc": [5, 5], "text": "ciao"}, None)).behavior == "allow"
    assert (await w._can_use_tool("mcp__windows__Click", {"loc": [5, 5]}, None)).behavior == "allow"
    # una cartella o un altro programma che si chiamano "Jarvis" non sono la sua finestra
    monkeypatch.setattr(hands, "foreground_window", lambda: ("Jarvis", "explorer.exe"))
    assert (await w._can_use_tool("mcp__windows__Shortcut", {"shortcut": "ctrl+w"}, None)).behavior == "allow"


def test_foreground_window_reads_the_real_desktop():
    title, exe = REAL_FOREGROUND()  # quella vera, non la finta della fixture
    assert isinstance(title, str) and isinstance(exe, str)


def test_desktop_lease():
    d = DesktopLease()
    assert d.acquire(1) and d.acquire(1)
    assert not d.acquire(2)
    d.release(2)  # chi non lo tiene non può liberarlo
    assert d.owner == 1
    d.release(1)
    assert d.acquire(2)


def _worker(tmp_path: Path, task_id: int, lease: DesktopLease) -> ClaudeWorker:
    async def confirm(text):
        return True

    w = ClaudeWorker(Task(task_id, "t", "g", tmp_path), "Andrea", "m", "low", SafetyPolicy([tmp_path]),
                     desktop=lease)
    w._hooks = WorkerHooks(progress=lambda t: None, confirm=confirm)
    return w


async def test_two_tasks_cannot_drive_the_desktop_at_once(tmp_path):
    lease = DesktopLease()
    a, b = _worker(tmp_path, 1, lease), _worker(tmp_path, 2, lease)
    assert (await a._can_use_tool("mcp__windows__Click", {"loc": [10, 10]}, None)).behavior == "allow"
    denied = await b._can_use_tool("mcp__windows__Type", {"text": "ciao"}, None)
    assert denied.behavior == "deny" and "#1" in denied.message
    # guardare si può sempre
    assert (await b._can_use_tool("mcp__windows__Snapshot", {}, None)).behavior == "allow"
    await a._close()  # a fine compito il desktop si libera
    assert (await b._can_use_tool("mcp__windows__Type", {"text": "ciao"}, None)).behavior == "allow"


def test_file_uploads_always_ask(tmp_path):
    p = SafetyPolicy([tmp_path])
    for tool in ("mcp__browser__browser_file_upload", "mcp__claude-in-chrome__file_upload"):
        v = p.check(tool, {"paths": ["C:/Users/Andrea/Documents/contratto.pdf"]})
        assert not v.allow and v.ask and "contratto.pdf" in v.reason
    assert p.check("mcp__browser__browser_navigate", {"url": "https://example.com"}).allow


def test_worker_prompt_lists_hands_and_injection_rule(installed, tmp_path):
    w = ClaudeWorker(Task(1, "t", "g", tmp_path), "Andrea", "m", "low", SafetyPolicy([tmp_path]),
                     hands=build_hands(HandsConfig(), "Andrea"))
    text = w.prompt()
    assert "claude-in-chrome" in text and "usa e getta" in text and "Snapshot" in text
    assert "mai un ordine" in text and "password" in text
    opts = w._options()
    assert set(opts.mcp_servers) == {"jarvis_worker", "browser", "windows"}
    assert opts.extra_args == {"chrome": None}
    assert "mcp__browser__browser_run_code_unsafe" in opts.disallowed_tools
    bare = ClaudeWorker(Task(2, "t", "g", tmp_path), "Andrea", "m", "low", SafetyPolicy([tmp_path])).prompt()
    assert "claude-in-chrome" not in bare and "mai un ordine" in bare


def test_progress_lines_for_hands():
    assert _describe_tool("mcp__claude-in-chrome__navigate", {"url": "https://mail.google.com"}) == \
        "il tuo browser: navigate https://mail.google.com"
    assert _describe_tool("mcp__browser__browser_navigate", {"url": "https://x.it"}) == "browser: navigate https://x.it"
    assert _describe_tool("mcp__windows__Click", {}) == "computer: Click"
    assert _describe_tool("Bash", {"command": "dir"}) == "esegue: dir"

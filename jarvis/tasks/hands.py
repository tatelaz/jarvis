"""Le "mani" dei sotto-agenti: il browser dell'utente, un browser usa e getta, il desktop di Windows.

- Browser dell'utente: Claude in Chrome, l'estensione già installata nel suo browser (Comet o Chrome),
  collegata al CLI con --chrome. Lavora con i suoi accessi, in un gruppo di schede tutto suo.
- Browser usa e getta: Playwright MCP con il profilo in memoria; si apre pulito e sparisce a fine compito.
- Desktop: Windows-MCP (UI Automation) ridotto agli strumenti d'interfaccia. Shell, file e registro
  restano quelli nativi di Claude Code, che passano dalla politica di sicurezza.

Il desktop ha un solo mouse e una sola tastiera: lo usa un compito alla volta (DesktopLease).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from ..config import HandsConfig

WINDOWS_MCP = paths.ENGINE_DIR / "windows-mcp" / ".venv" / "Scripts" / "windows-mcp.exe"
PLAYWRIGHT_MCP = paths.ENGINE_DIR / "playwright-mcp" / "node_modules" / "@playwright" / "mcp" / "cli.js"

# solo interfaccia: PowerShell, FileSystem, Registry e Process restano fuori (doppioni non sorvegliati)
WINDOWS_TOOLS = ["App", "Click", "Type", "Scroll", "Move", "Shortcut", "Wait", "WaitFor", "Snapshot", "Screenshot",
                 "Scrape", "Clipboard", "MultiSelect", "MultiEdit", "DisplayInventory"]
# strumenti che guardano senza toccare: non serve avere il desktop in esclusiva
WINDOWS_PASSIVE = {"Snapshot", "Screenshot", "Scrape", "Wait", "WaitFor", "DisplayInventory"}
# esegue codice arbitrario sul PC fuori da ogni controllo
BLOCKED_TOOLS = ["mcp__browser__browser_run_code_unsafe"]

GUIDE = {
    "user_browser": "- Browser di {user} (strumenti claude-in-chrome): usalo quando servono i suoi accessi (posta, "
                    "account, siti dove è già entrato) o quando deve vedere il risultato. Lavora nel tuo gruppo di "
                    "schede e non chiudere le sue. Se non risponde, probabilmente il browser è chiuso: dillo. "
                    "Se clic, screenshot o script rispondono \"Host access is restricted by policy\" è un difetto noto "
                    "di Comet 155 (non un sito escluso apposta): aprire pagine e leggerle funziona ancora; per "
                    "cliccare o scrivere usa mouse e tastiera sulla finestra del browser (strumenti windows) se servono "
                    "i suoi accessi, altrimenti il browser usa e getta. Non arrenderti e non insistere con gli stessi "
                    "strumenti.",
    "disposable_browser": "- Browser usa e getta (strumenti browser): pulito, senza i suoi accessi, invisibile. "
                          "Per ricerche, consultare siti, scaricare dati o provare pagine senza toccare i suoi account.",
    "desktop": "- Computer (strumenti windows): mouse e tastiera sulle app di Windows. Prima Snapshot per vedere, "
               "poi agisci. {user} potrebbe star usando il PC: usalo solo quando non c'è una via da riga di "
               "comando, da file o dal browser. Windows a volte rifiuta il cambio di finestra: prima di tasti che "
               "vanno a chi ha il fuoco (Shortcut, Type senza un punto) controlla nello Snapshot quale finestra "
               "è in primo piano; quando puoi, preferisci un clic sul bersaglio.",
}

# tasti senza un punto da cliccare: arrivano alla finestra in primo piano, qualunque sia
KEYBOARD_TOOLS = {"Shortcut", "Type"}
JARVIS_WINDOW = "Jarvis"  # titolo della finestra di Jarvis (ui/window.py)


@dataclass
class Hands:
    mcp_servers: dict = field(default_factory=dict)
    extra_args: dict = field(default_factory=dict)
    disallowed: list[str] = field(default_factory=list)
    guide: list[str] = field(default_factory=list)


def build_hands(cfg: HandsConfig, user: str) -> Hands:
    """Le mani disponibili davvero: ciò che è attivo nelle impostazioni e installato sul PC."""
    h = Hands(disallowed=list(BLOCKED_TOOLS))
    if cfg.user_browser:
        h.extra_args["chrome"] = None
        h.guide.append(GUIDE["user_browser"])
    node = shutil.which("node")
    if cfg.disposable_browser and node and PLAYWRIGHT_MCP.exists():
        args = [str(PLAYWRIGHT_MCP), "--isolated", "--browser", cfg.disposable_browser_channel]
        if not cfg.disposable_browser_visible:
            args.append("--headless")
        h.mcp_servers["browser"] = {"type": "stdio", "command": node, "args": args}
        h.guide.append(GUIDE["disposable_browser"])
    if cfg.desktop and WINDOWS_MCP.exists():
        h.mcp_servers["windows"] = {"type": "stdio", "command": str(WINDOWS_MCP),
                                    "args": ["serve", "--transport", "stdio", "--tools", ",".join(WINDOWS_TOOLS)],
                                    # 0,8 = 1536 px di larghezza su 1920: sotto il limite oltre cui l'API
                                    # rimpicciolisce le immagini di nascosto (e le coordinate sballerebbero);
                                    # Windows-MCP comunica la scala al modello
                                    "env": {"ANONYMIZED_TELEMETRY": "false", "WINDOWS_MCP_SCREENSHOT_SCALE": "0.8"}}
        h.guide.append(GUIDE["desktop"])
    h.guide = [g.replace("{user}", user) for g in h.guide]
    return h


def needs_desktop(tool: str) -> bool:
    """Vero per gli strumenti che muovono mouse, tastiera o finestre."""
    return tool.startswith("mcp__windows__") and tool.removeprefix("mcp__windows__") not in WINDOWS_PASSIVE


def foreground_window() -> tuple[str, str]:
    """(titolo, eseguibile) della finestra in primo piano."""
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                exe = Path(buf.value).name
        finally:
            kernel32.CloseHandle(handle)
    return title.value, exe


def keys_would_hit_jarvis(tool: str, args: dict) -> bool:
    """Tasti diretti alla finestra di Jarvis. Il 26/10 un Ctrl+W per chiudere una scheda di Comet è finito lì:
    Windows aveva rifiutato il cambio di finestra e il sotto-agente non l'ha visto. Type con un punto clicca prima
    il bersaglio (e sposta il fuoco): quello va bene."""
    name = tool.removeprefix("mcp__windows__")
    if not tool.startswith("mcp__windows__") or name not in KEYBOARD_TOOLS or (name == "Type" and args.get("loc")):
        return False
    try:
        title, exe = foreground_window()
    except (AttributeError, OSError):
        return False
    return title == JARVIS_WINDOW and exe.lower() in ("python.exe", "pythonw.exe")


class DesktopLease:
    """Chi sta usando mouse e tastiera. Un compito lo tiene fino alla fine, gli altri trovano occupato."""

    def __init__(self) -> None:
        self.owner: int | None = None

    def acquire(self, task_id: int) -> bool:
        if self.owner in (None, task_id):
            self.owner = task_id
            return True
        return False

    def release(self, task_id: int) -> None:
        if self.owner == task_id:
            self.owner = None


def describe(name: str, args: dict) -> str | None:
    """Una riga leggibile per il diario del compito, per gli strumenti delle mani."""
    target = str(args.get("url") or args.get("element") or args.get("name") or args.get("text") or "")[:60]
    for prefix, label in (("mcp__claude-in-chrome__", "il tuo browser"), ("mcp__browser__browser_", "browser"),
                          ("mcp__windows__", "computer")):
        if name.startswith(prefix):
            return f"{label}: {name.removeprefix(prefix)} {target}".rstrip()
    return None

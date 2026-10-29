"""Trova un eseguibile nativo di Claude Code e prepara le opzioni isolate per il Claude Agent SDK.

Due trappole misurate su questo PC:
- l'SDK rifiuta i wrapper .cmd di npm (cmd.exe non permette un escaping sicuro degli argomenti):
  serve il claude.exe nativo, che l'installazione npm contiene comunque;
- senza isolamento la sessione eredita tutti i connettori claude.ai e i plugin dell'utente:
  399 strumenti e ~282.000 token di contesto, cioè "Prompt is too long" già al secondo messaggio.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ISOLATED_ENV = {
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",  # niente connettori claude.ai (Gmail, Notion, ...)
    "DISABLE_AUTOUPDATER": "1",
}


def find_cli(explicit: str | None = None) -> str:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    try:
        import claude_agent_sdk

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
        candidates += [bundled / "claude.exe", bundled / "claude"]
    except ImportError:
        pass
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates += [
        home / ".local" / "bin" / "claude.exe",  # installer nativo
        appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
    ]
    on_path = shutil.which("claude")
    if on_path:
        p = Path(on_path)
        if p.suffix.lower() == ".exe":
            candidates.append(p)
        else:  # wrapper npm: il binario vero sta nel pacchetto accanto
            candidates.append(p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    for c in candidates:
        if c.is_file() and c.suffix.lower() in (".exe", ""):
            return str(c)
    raise FileNotFoundError(
        "Claude Code nativo non trovato. Installalo con: irm https://claude.ai/install.ps1 | iex "
        "(oppure npm i -g @anthropic-ai/claude-code) e fai il login con `claude`."
    )

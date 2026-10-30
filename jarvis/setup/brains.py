"""Il cervello al primo avvio: Claude Code con l'abbonamento di chi installa, oppure OpenCode gratis.

Per Claude si usano solo i comandi ufficiali: l'installatore di Anthropic (`irm https://claude.ai/install.ps1 |
iex`), `claude auth login --claudeai` (apre claude.ai nel browser: password e codici restano lì) e
`claude auth status --json` per sapere se l'accesso è fatto. Installazione e accesso partono in una finestra a
parte, visibile: chi installa vede cosa succede e la pagina del primo avvio aspetta, chiedendo lo stato ogni tanto.
Dello stato si tengono solo tre cose (accesso fatto, tipo di abbonamento, metodo): email e organizzazione non
escono da qui.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from ..brain.claude_cli import ISOLATED_ENV, find_cli

log = logging.getLogger("jarvis.setup")

INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex"  # installatore ufficiale di Claude Code per Windows
NEW_CONSOLE = 0x00000010  # CREATE_NEW_CONSOLE: una finestra a parte, che chi installa vede
NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class ClaudeStatus:
    installed: bool
    logged_in: bool = False
    plan: str = ""  # "pro", "max"... come lo dice Claude Code; vuoto se non si sa
    method: str = ""  # "claude.ai" (abbonamento) o "console" (a consumo)

    def public(self) -> dict:
        return {"installed": self.installed, "logged_in": self.logged_in, "plan": self.plan, "method": self.method}


def parse_status(text: str) -> ClaudeStatus:
    """L'uscita di `claude auth status --json`. Qualsiasi cosa illeggibile vale "accesso non fatto"."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ClaudeStatus(True)
    if not isinstance(data, dict):
        return ClaudeStatus(True)
    plan = data.get("subscriptionType")
    method = data.get("authMethod")
    return ClaudeStatus(True, data.get("loggedIn") is True, plan if isinstance(plan, str) else "",
                        method if isinstance(method, str) else "")


class ClaudeCode:
    def __init__(self, find: Callable[[], str] = find_cli, run: Callable = subprocess.run,
                 popen: Callable = subprocess.Popen) -> None:
        self.find, self.run, self.popen = find, run, popen

    def cli(self) -> str | None:
        try:
            return self.find()
        except FileNotFoundError:
            return None

    def status(self) -> ClaudeStatus:
        exe = self.cli()
        if exe is None:
            return ClaudeStatus(False)
        try:
            r = self.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=30,
                         env={**os.environ, **ISOLATED_ENV}, creationflags=NO_WINDOW, encoding="utf-8",
                         errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            log.info("primo avvio: stato di Claude Code illeggibile (%s)", e)
            return ClaudeStatus(True)
        return parse_status(r.stdout)

    def login(self) -> bool:
        """Apre l'accesso ufficiale (browser su claude.ai). False se Claude Code non c'è."""
        exe = self.cli()
        if exe is None:
            return False
        self.popen([exe, "auth", "login", "--claudeai"], creationflags=NEW_CONSOLE,
                   env={**os.environ, **ISOLATED_ENV})
        return True

    def install(self) -> None:
        """Avvia l'installatore ufficiale di Anthropic, in una finestra a parte."""
        self.popen(["powershell", "-NoProfile", "-Command", INSTALL_COMMAND], creationflags=NEW_CONSOLE)

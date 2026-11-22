"""Trova un eseguibile nativo di Claude Code e prepara le opzioni isolate per il Claude Agent SDK.

Due trappole misurate su questo PC:
- l'SDK rifiuta i wrapper .cmd di npm (cmd.exe non permette un escaping sicuro degli argomenti):
  serve il claude.exe nativo, che l'installazione npm contiene comunque;
- senza isolamento la sessione eredita tutti i connettori claude.ai e i plugin dell'utente:
  399 strumenti e ~282.000 token di contesto, cioè "Prompt is too long" già al secondo messaggio.

Claude Code si paga in due modi: con l'abbonamento (il login di claude.ai, di norma) o con la chiave API di
Anthropic, a consumo (`brain.claude_access`). La chiave arriva a Claude Code solo nella sua variabile, sessione per
sessione (`session_env`); provato il 22/11 con `claude auth status`: con la variabile vuota usa il login, con una
chiave usa la chiave (`apiKeySource: ANTHROPIC_API_KEY`).
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from .. import paths
from .keys import ApiKeyStore

log = logging.getLogger("jarvis.brain")

ISOLATED_ENV = {
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",  # niente connettori claude.ai (Gmail, Notion, ...)
    "DISABLE_AUTOUPDATER": "1",
}
KEY_VAR = "ANTHROPIC_API_KEY"
ACCESS = ("abbonamento", "chiave")


def claude_keys(folder: Path | None = None, environ=None) -> ApiKeyStore:
    """La chiave API di Anthropic: salvata cifrata dal primo avvio in data/claude, oppure data/claude/chiave.txt
    (cifrata al primo uso), oppure la variabile ANTHROPIC_API_KEY del PC."""
    return ApiKeyStore(folder or paths.DATA_DIR / "claude", environ, env_var=KEY_VAR, what="Claude")


class Account:
    """Come paga Claude Code, deciso a ogni sessione nuova. Con l'abbonamento la variabile della chiave arriva vuota:
    una ANTHROPIC_API_KEY rimasta nel PC non fa pagare a consumo senza volerlo. Con "chiave" ma senza una chiave si
    resta sull'abbonamento, e lo si dice nel registro (mai la chiave)."""

    def __init__(self, access: Callable[[], str] = lambda: "abbonamento", keys: ApiKeyStore | None = None) -> None:
        self.access, self.keys = access, keys
        self._warned = False

    def env(self) -> dict[str, str]:
        if self.access() != "chiave":
            return {KEY_VAR: ""}
        key = self.keys.get() if self.keys is not None else None
        if not key:
            if not self._warned:
                log.warning("Claude con la chiave API, ma la chiave non c'è: uso l'abbonamento")
                self._warned = True
            return {KEY_VAR: ""}
        return {KEY_VAR: key}


ACCOUNT = Account()  # app.py la lega alle impostazioni (brain.claude_access) e a data/claude


def session_env(**extra: str) -> dict[str, str]:
    """Le variabili di ogni sessione di Claude Code: isolamento e modo di pagare."""
    return {**ISOLATED_ENV, **ACCOUNT.env(), **extra}


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

"""Registri: su file (ruotati, sempre dettagliati) e a console (essenziali).

Mai segreti nei registri: il 27/10 il collegamento di Gmail ha scritto in chiaro nel registro il codice di
autorizzazione, il token di accesso e il refresh token (DEBUG di requests_oauthlib, INFO di google_auth_oauthlib),
annullando la cifratura del permesso. Le librerie di autenticazione scrivono solo gli avvisi, e ogni riga passa
comunque da redact() prima di arrivare al file o alla console.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys

from . import paths

_SECRETS = [
    (re.compile(r"ya29\.[\w.\-]+"), "ya29.[REDATTO]"),  # token di accesso di Google
    (re.compile(r"1//[\w\-]{10,}"), "1//[REDATTO]"),  # refresh token di Google
    (re.compile(r"GOCSPX-[\w\-]+"), "GOCSPX-[REDATTO]"),  # segreto del client OAuth
    (re.compile(r"\b(code=)[^&\s\"']+"), r"\1[REDATTO]"),  # codice di autorizzazione nell'indirizzo di ritorno
    (re.compile(r"""(["']?(?:access_token|refresh_token|id_token|client_secret)["']?\s*[:=]\s*["']?)[^"'\s,&}]+"""),
     r"\1[REDATTO]"),
    # OpenCode (28/10): la chiave di Zen (le guide la mostrano come "sk-..."), la parola d'ordine del server e il
    # gettone del ponte MCP, in qualunque forma arrivino (variabile, campo JSON, intestazione HTTP)
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-[REDATTO]"),
    (re.compile(r"""((?:OPENCODE_API_KEY|OPENCODE_SERVER_PASSWORD)["']?\s*[:=]\s*["']?)[^"'\s,&}]+"""), r"\1[REDATTO]"),
    (re.compile(r"""(["']?apiKey["']?\s*[:=]\s*["']?)[^"'\s,&}]+"""), r"\1[REDATTO]"),
    (re.compile(r"\b(Bearer\s+)[\w.~+/=\-]{16,}"), r"\1[REDATTO]"),
    (re.compile(r"\b(Basic\s+)[A-Za-z0-9+/=]{12,}"), r"\1[REDATTO]"),
]

QUIET = ("asyncio", "urllib3", "comtypes", "httpx", "httpcore", "aiohttp.access",
         "oauthlib", "requests_oauthlib", "google_auth_oauthlib", "google.auth")


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup(console_level: int = logging.INFO, filename: str = "jarvis.log") -> None:
    """filename: ogni processo il suo file (due processi che ruotano lo stesso file su Windows si bloccano)."""
    root = logging.getLogger()
    if getattr(root, "_jarvis_configured", False):
        return
    root.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(paths.logs_dir() / filename, maxBytes=5_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:  # con pythonw (avvio automatico) non c'è nessuna console
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(console_level)
        ch.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(ch)
    for noisy in QUIET:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._jarvis_configured = True

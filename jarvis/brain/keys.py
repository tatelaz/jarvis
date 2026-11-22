"""Le chiavi API di chi usa Jarvis (OpenCode Zen, Anthropic): cifrate con DPAPI, mai nei registri né nelle impostazioni.

Una chiave arriva dalla sua variabile d'ambiente oppure dal file `chiave.txt` nella cartella del servizio, che al
primo uso viene cifrato con DPAPI (`chiave.bin`, leggibile solo dal suo utente di Windows) e cancellato. Il primo
avvio la salva direttamente cifrata (`save`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path

log = logging.getLogger("jarvis.brain")


def _protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, "jarvis", None, None, None, 0x1)


def _unprotect(blob: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptUnprotectData(blob, None, None, None, 0x1)[1]


class ApiKeyStore:
    """La chiave API di un servizio. Senza chiave `get` dice None e chi la usa fa senza (o lo dice)."""

    def __init__(self, folder: Path, environ: Mapping[str, str] | None = None,
                 protect: Callable[[bytes], bytes] = _protect, unprotect: Callable[[bytes], bytes] = _unprotect,
                 env_var: str = "OPENCODE_API_KEY", what: str = "OpenCode") -> None:
        self.folder = folder
        self.environ = os.environ if environ is None else environ
        self.protect, self.unprotect = protect, unprotect
        self.env_var, self.what = env_var, what
        self.plain, self.sealed = folder / "chiave.txt", folder / "chiave.bin"

    def get(self) -> str | None:
        key = (self.environ.get(self.env_var) or "").strip()
        if key:
            return key
        if self.plain.is_file():
            key = self.plain.read_text(encoding="utf-8-sig").strip()
            if key:
                try:
                    self.sealed.write_bytes(self.protect(key.encode()))
                    self.plain.unlink()
                    log.info("chiave di %s cifrata (chiave.bin) e tolta dal file in chiaro", self.what)
                except Exception as e:  # meglio una chiave in chiaro che nessuna: lo si dice nel registro
                    log.warning("non sono riuscito a cifrare la chiave di %s: %s", self.what, type(e).__name__)
                return key
        if self.sealed.is_file():
            try:
                return self.unprotect(self.sealed.read_bytes()).decode().strip() or None
            except Exception as e:
                log.warning("chiave di %s illeggibile (%s): faccio senza", self.what, type(e).__name__)
        return None

    def save(self, key: str) -> None:
        """Salva una chiave nuova, già cifrata (mai in chiaro su disco). Se non si può cifrare, non la salva."""
        sealed = self.protect(key.strip().encode())
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.sealed.with_suffix(".tmp")
        tmp.write_bytes(sealed)
        os.replace(tmp, self.sealed)
        self.plain.unlink(missing_ok=True)
        log.info("chiave di %s salvata, cifrata", self.what)

    def stored(self) -> bool:
        """C'è una chiave salvata da Jarvis (senza leggerla)?"""
        return self.sealed.is_file() or self.plain.is_file()

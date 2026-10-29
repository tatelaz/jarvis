"""Avvio automatico con Windows: una voce in HKCU\\...\\Run (solo per questo utente, niente permessi di sistema).

Il comando avvia il guardiano con pythonw (nessuna finestra di console): il guardiano fa partire Jarvis e lo
tiene in vita. All'avvio Jarvis ricontrolla la voce e la corregge se il comando non è più quello giusto
(per esempio se la cartella è stata spostata).

Windows ha un secondo interruttore: le "App di avvio" (Gestione attività o Impostazioni) non toccano la voce in Run
ma scrivono in StartupApproved\\Run (primo byte dispari = spento). L'8/10 l'utente aveva spento Jarvis da lì e Jarvis
continuava a credersi "attivo": ora lo legge, e riaccendendolo a voce si riaccende anche quello.
Finché la voce in Run c'è, quell'interruttore ha l'ultima parola: la voce non va mai tolta per allinearsi (il 26/10
Jarvis l'aveva tolta e così spariva anche dall'elenco delle App di avvio, da cui l'utente non poteva più riaccenderlo).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from . import paths

log = logging.getLogger("jarvis.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
ENABLED = bytes([2]) + bytes(11)  # come lo scrive Windows quando si riattiva un'app di avvio
NAME = "Jarvis"
LAUNCHER = "avvia_jarvis.pyw"


def command(root: Path = paths.ROOT) -> str:
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{root / LAUNCHER}"'


def current(key: str = RUN_KEY, name: str = NAME) -> str | None:
    """Il comando registrato oggi, o None se l'avvio automatico è spento."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, name)
            return str(value)
    except FileNotFoundError:
        return None


def blocked(approved_key: str = APPROVED_KEY, name: str = NAME) -> bool:
    """Spento dalle "App di avvio" di Windows (la voce in Run c'è, ma Windows non la esegue)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_key) as k:
            value, _ = winreg.QueryValueEx(k, name)
    except FileNotFoundError:
        return False
    return bool(value) and bytes(value)[0] % 2 == 1


def _unblock(approved_key: str, name: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, approved_key) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, ENABLED)


def apply(enabled: bool, key: str = RUN_KEY, name: str = NAME, root: Path = paths.ROOT,
          approved_key: str = APPROVED_KEY) -> str:
    """Accende o spegne l'avvio automatico. Restituisce una frase su cosa è cambiato."""
    import winreg

    if enabled:
        cmd = command(root)
        was_blocked = blocked(approved_key, name)
        if was_blocked:  # spento dalle App di avvio di Windows: chi lo chiede a Jarvis lo vuole acceso davvero
            _unblock(approved_key, name)
            log.info("avvio automatico riattivato anche nelle App di avvio di Windows")
        if current(key, name) == cmd and not was_blocked:
            return "l'avvio automatico era già attivo"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)
        log.info("avvio automatico attivato: %s", cmd)
        return "avvio automatico attivato: partirò all'accesso a Windows"
    if current(key, name) is None:
        return "l'avvio automatico era già spento"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, name)
    log.info("avvio automatico disattivato")
    return "avvio automatico disattivato"


def reconcile(enabled: bool, key: str = RUN_KEY, name: str = NAME, root: Path = paths.ROOT,
              approved_key: str = APPROVED_KEY) -> tuple[bool, str | None]:
    """All'avvio: allinea impostazione e registro. Restituisce (acceso davvero, cosa è cambiato o None).

    Con la voce di Jarvis in Run decide l'interruttore delle App di avvio, in entrambi i sensi: è lì che l'utente lo
    spegne o lo riaccende senza passare da Jarvis. Senza la voce decide l'impostazione (la voce si ricrea).
    """
    registered = current(key, name)
    if registered is not None and LAUNCHER in registered:
        if blocked(approved_key, name):
            return False, "spento dalle App di avvio di Windows" if enabled else None
        if registered != command(root):  # per esempio la cartella è stata spostata
            return True, apply(True, key, name, root, approved_key)
        return True, None if enabled else "acceso dalle App di avvio di Windows"
    if enabled:
        return True, apply(True, key, name, root, approved_key)
    return False, None

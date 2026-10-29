"""Un solo Jarvis acceso alla volta (mutex di Windows con nome).

Con l'avvio automatico, un doppio clic su "Avvia Jarvis" farebbe partire una seconda copia: le due si
contenderebbero le porte dei motori, la seconda cadrebbe in continuazione e il guardiano finirebbe per
credere guasta la versione sana. Con il mutex la seconda si accorge della prima, apre la sua finestra ed esce.
Il mutex si libera da solo quando il processo termina (anche se cade o viene chiuso a forza).
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
from pathlib import Path

from . import paths

log = logging.getLogger("jarvis.single")

ERROR_ALREADY_EXISTS = 183
GUARDIAN = "Local\\JarvisGuardiano"
APP = "Local\\JarvisApp"
_held: dict[str, int] = {}  # i mutex presi restano aperti per tutta la vita del processo (o fino a release)


def acquire(name: str) -> bool:
    """Vero se questo processo è il primo con questo nome; il mutex resta suo fino all'uscita."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateMutexW fallita")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _held[name] = handle
    return True


def release(name: str) -> None:
    """Restituisce un mutex preso da questo processo. Serve alle prove complete, che girano più volte nello stesso
    processo: senza, dalla seconda in poi credevano Jarvis acceso (26/10)."""
    handle = _held.pop(name, None)
    if handle is not None:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))


def show_running_window(port: int) -> None:
    """Jarvis è già acceso: si apre la sua finestra invece di avviarne un altro."""
    exe = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(exe if exe.exists() else sys.executable), "-m", "jarvis.ui.window", "--port", str(port)],
                     cwd=paths.ROOT)
    log.info("Jarvis era già acceso: apro la sua finestra")

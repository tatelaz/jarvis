"""Il runtime di Visual C++ portato da Jarvis (tools/vcrt), per i PC che non ce l'hanno.

onnxruntime (voce, fine del turno, chi parla, memoria) e soxr (ricampionamento) chiedono msvcp140.dll, che Windows di
suo non ha: arriva con il "Visual C++ Redistributable", che quasi sempre qualche altro programma ha già installato, ma
non sempre (un Windows appena installato, Windows Sandbox). Senza, l'import fallisce con "DLL load failed ...
Impossibile trovare il modulo specificato" e Jarvis non sente niente.

L'installatore mette le librerie in tools/vcrt (scripts/build_installer.py); qui le si carica col percorso completo
prima di ogni altro import, così chi le cerca per nome trova quelle già caricate. Si usano solo se quelle di sistema
mancano o sono più vecchie: quelle di sistema le aggiorna Windows Update. Chiamato da jarvis/__init__.py, quindi vale
per ogni processo che importa Jarvis (anche le prove nelle copie separate).
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

# prima vcruntime (le altre dipendono da lei), poi il resto della famiglia: tutto quello che il Redistributable mette
FILES = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
         "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll")
# chi è compilato con Visual Studio 2022 17.10 o dopo (onnxruntime) si pianta con un msvcp140 più vecchio della 14.40
# (cambio di std::mutex): l'installatore non accetta copie più vecchie
MIN_VERSION = (14, 40)
CHECKED = False  # vero quando use() è passato: lo guardano le prove
LOADED: list[str] = []  # quelle caricate da tools/vcrt, per il registro

Version = tuple[int, int, int, int]


def file_version(path: Path) -> Version | None:
    """La versione scritta nel file (VS_FIXEDFILEINFO), o None se il file manca o non ne ha una."""
    if os.name != "nt" or not path.is_file():
        return None
    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p)
    ver.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    ver.GetFileVersionInfoW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    ver.VerQueryValueW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(ctypes.c_uint))
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    info, n = ctypes.c_void_p(), ctypes.c_uint()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(info), ctypes.byref(n)) or n.value < 16 or not info.value:
        return None
    fixed = ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32 * 4)).contents  # firma, struttura, versione MS e LS
    ms, ls = fixed[2], fixed[3]
    return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF


def system_dir() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def places() -> list[Path]:
    """Dove cercare tools/vcrt: accanto al codice (la copia installata) e accanto al Python della copia installata,
    che sta in tools/python/<versione> (così lo trovano anche le copie separate in data/, che tools/ non l'hanno)."""
    return [Path(__file__).resolve().parents[1] / "tools" / "vcrt", Path(sys.base_prefix).parents[1] / "vcrt"]


def needed(system: Path, ours: Path, version: Callable[[Path], Version | None] = file_version) -> bool:
    """Vero se servono le nostre: quella di sistema manca o è più vecchia della nostra."""
    mine = version(ours / "msvcp140.dll")
    if mine is None:
        return False
    theirs = version(system / "msvcp140.dll")
    return theirs is None or theirs < mine


def _already_loaded(name: str) -> bool:
    k32 = ctypes.WinDLL("kernel32")
    k32.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    return bool(k32.GetModuleHandleW(name))


def use(dirs: Iterable[Path] | None = None, system: Path | None = None,
        version: Callable[[Path], Version | None] = file_version, load: Callable[[str], object] | None = None,
        loaded: Callable[[str], bool] | None = None) -> list[str]:
    """Carica le librerie di tools/vcrt se servono; mai un errore (al peggio resta l'errore dell'import di dopo)."""
    global CHECKED
    CHECKED = True
    if os.name != "nt" and load is None:
        return []
    load = load or ctypes.WinDLL
    loaded = loaded or _already_loaded
    system = system or system_dir()
    done: list[str] = []
    try:
        ours = next((d for d in (places() if dirs is None else dirs) if (d / "msvcp140.dll").is_file()), None)
        if ours is None or not needed(system, ours, version):
            return done
        for name in FILES:
            if (ours / name).is_file() and not loaded(name):
                load(str(ours / name))
                done.append(name)
    except OSError:
        pass
    LOADED.extend(done)
    return done

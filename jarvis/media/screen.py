"""Guardare lo schermo: uno screenshot di ciò che l'utente vede adesso, per il cervello (strumento guarda_schermo).

- "schermo" (di base): il monitor dove sta la finestra in primo piano; "finestra": solo quella finestra; "tutti": tutti
  i monitor insieme.
- Il lato lungo si riduce a media.screen_px (1568 di base: oltre, Claude riduce comunque e i token crescono); JPEG in
  memoria, mai salvato su disco.
- Solo l'utente: lo schermo può mostrare chat e posta (gruppo "schermo", jarvis/identity/guard.py).
- Le coordinate sono in pixel veri anche con lo zoom di Windows al 125-150 %: si leggono con la consapevolezza DPI
  per monitor (come fa Pillow quando cattura), altrimenti la finestra verrebbe ritagliata nel punto sbagliato.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

WHERE = ("schermo", "finestra", "tutti")
MIN_PX, MAX_PX = 512, 2560
JPEG_QUALITY = 82
Rect = tuple[int, int, int, int]  # sinistra, alto, destra, basso (pixel veri, coordinate del desktop virtuale)


class ScreenError(RuntimeError):
    pass


@dataclass
class Shot:
    jpeg: bytes
    size: tuple[int, int]  # dopo la riduzione
    original: tuple[int, int]
    what: str  # cosa si è catturato, detto a parole dopo "Screenshot" ("dello schermo 2 di 2")
    front: str = ""  # il titolo della finestra in primo piano (resta anche ai cervelli che non vedono le immagini)


# ---------------------------------------------------------------------- Windows


@contextlib.contextmanager
def _per_monitor_dpi() -> Iterator[None]:
    """Per questo thread, coordinate in pixel veri (DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)."""
    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if setter is None:
        yield
        return
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p]
    old = setter(ctypes.c_void_p(-4))
    try:
        yield
    finally:
        if old:
            setter(ctypes.c_void_p(old))


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


# la barra delle applicazioni e il desktop non sono "la finestra che stai guardando" (provato il 23/11: con la barra in
# primo piano "finestra" catturava una striscia di 1920x48)
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}


def is_real_window(class_name: str) -> bool:
    return class_name not in SHELL_CLASSES


def foreground() -> tuple[Rect, str] | None:
    """Il rettangolo della finestra in primo piano (senza l'ombra) e il suo titolo; None se non c'è."""
    from ..tools.system import active_window

    user32 = ctypes.windll.user32
    with _per_monitor_dpi():
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if not is_real_window(cls.value):
            return None
        r = _RECT()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # 9 = bordi veri
            user32.GetWindowRect(hwnd, ctypes.byref(r))
    if r.right - r.left < 2 or r.bottom - r.top < 2:
        return None
    w = active_window()
    title = w.get("title") or w.get("process") or "senza titolo"
    return (r.left, r.top, r.right, r.bottom), title


def monitor_of(rect: Rect | None) -> tuple[Rect, int, int]:
    """Il monitor che contiene di più il rettangolo (o il principale): (rettangolo, numero da 1, quanti sono)."""
    user32 = ctypes.windll.user32
    monitors: list[tuple[Rect, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double)
    def each(hmon, _hdc, _r, _data):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(info))
        m = info.rcMonitor
        monitors.append(((m.left, m.top, m.right, m.bottom), info.dwFlags & 1))
        return 1

    with _per_monitor_dpi():
        user32.EnumDisplayMonitors(None, None, each, 0)
    if not monitors:
        raise ScreenError("Windows non dice quali schermi ci sono")
    return pick_monitor(rect, monitors)


def pick_monitor(rect: Rect | None, monitors: list[tuple[Rect, int]]) -> tuple[Rect, int, int]:
    """Il monitor con l'area più grande in comune con rect; senza rect (o fuori da tutti) il principale."""
    def overlap(a: Rect, b: Rect) -> int:
        w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
        return w * h if w > 0 and h > 0 else 0

    scores = [overlap(rect, m) if rect else 0 for m, _primary in monitors]
    best = max(range(len(monitors)), key=scores.__getitem__)
    if scores[best] == 0:
        best = next((i for i, (_m, primary) in enumerate(monitors) if primary), 0)
    return monitors[best][0], best + 1, len(monitors)


def grab(bbox: Rect | None) -> Any:
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=bbox, all_screens=True)


# ---------------------------------------------------------------------- la cattura


def encode(img: Any, max_px: int) -> tuple[bytes, tuple[int, int]]:
    """JPEG con il lato lungo al massimo max_px (mai ingrandita)."""
    img = img.convert("RGB")
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), img.size


def capture(where: str = "schermo", max_px: int = 1568, grab: Callable[[Rect | None], Any] = grab,
            window: Callable[[], tuple[Rect, str] | None] = foreground,
            monitor: Callable[[Rect | None], tuple[Rect, int, int]] = monitor_of) -> Shot:
    where = where if where in WHERE else "schermo"
    max_px = max(MIN_PX, min(MAX_PX, int(max_px)))
    fg = window()
    if where == "tutti":
        bbox, what = None, "di tutti gli schermi"
    elif where == "finestra" and fg is not None:
        bbox, what = fg[0], f"della finestra «{fg[1]}»"
    else:
        rect, n, total = monitor(fg[0] if fg else None)
        bbox = rect
        what = "dello schermo" + (f" {n} di {total}" if total > 1 else "")
        if where == "finestra":  # nessuna finestra in primo piano: si guarda lo schermo, e lo si dice
            what += " (nessuna finestra in primo piano)"
    img = grab(bbox)
    original = img.size
    jpeg, size = encode(img, max_px)
    return Shot(jpeg, size, original, what, f"«{fg[1]}»" if fg else "")


BLIND = ("Lo screenshot c'è ma questo cervello non vede le immagini: hai solo il titolo della finestra. Dillo "
         "all'utente in una frase (con Claude come cervello lo vedresti).")


def describe(shot: Shot, now: float | None = None) -> str:
    """La riga di testo che accompagna l'immagine (e che resta ai cervelli che non vedono le immagini)."""
    hhmm = time.strftime("%H:%M", time.localtime(now))
    line = f"Screenshot {shot.what} alle {hhmm} ({shot.original[0]}x{shot.original[1]}"
    line += f", ridotto a {shot.size[0]}x{shot.size[1]})." if shot.size != shot.original else ")."
    return line + (f" In primo piano: {shot.front}." if shot.front else "")

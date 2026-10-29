"""Tasti rapidi globali di Windows (RegisterHotKey): funzionano con qualunque finestra in primo piano."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

log = logging.getLogger("jarvis.hotkeys")
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def parse(combo: str) -> tuple[int, int]:
    """"ctrl+alt+j" → (modificatori, codice tasto virtuale)."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("combinazione vuota")
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            raise ValueError(f"modificatore sconosciuto: {p}")
        mods |= MODS[p]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    elif key in ("space", "spazio"):
        vk = 0x20
    else:
        raise ValueError(f"tasto non supportato: {key}")
    return mods | MOD_NOREPEAT, vk


class Hotkeys:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._handlers: dict[int, Callable[[], None]] = {}
        self._combos: list[tuple[int, str, Callable[[], None]]] = []
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.registered: list[str] = []
        self.failed: list[str] = []

    def add(self, combo: str, handler: Callable[[], None]) -> None:
        self._combos.append((len(self._combos) + 1, combo, handler))

    def start(self) -> None:
        ready = threading.Event()

        def run():
            self._thread_id = kernel32.GetCurrentThreadId()
            for hid, combo, handler in self._combos:
                mods, vk = parse(combo)
                if user32.RegisterHotKey(None, hid, mods, vk):
                    self._handlers[hid] = handler
                    self.registered.append(combo)
                else:  # combinazione già presa da un altro programma
                    self.failed.append(combo)
                    log.warning("tasto rapido %s non disponibile (già usato da un altro programma)", combo)
            ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in self._handlers:
                    self.loop.call_soon_threadsafe(self._handlers[msg.wParam])
            for hid in self._handlers:
                user32.UnregisterHotKey(None, hid)

        self._thread = threading.Thread(target=run, name="hotkeys", daemon=True)
        self._thread.start()
        ready.wait(2)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

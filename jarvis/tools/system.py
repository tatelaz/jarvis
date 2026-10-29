"""Azioni rapide sul PC: aprire app/siti/cartelle, volume, musica, appunti, finestra attiva.

Sono le cose che Jarvis fa da solo, all'istante, senza scomodare un sotto-agente.
"""

from __future__ import annotations

import ctypes
import difflib
import json
import logging
import os
import re
import subprocess
import time
import unicodedata
import webbrowser
from ctypes import wintypes
from pathlib import Path

from .. import paths

log = logging.getLogger("jarvis.tools.system")
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ------------------------------------------------------------------ finestra attiva


def active_window() -> dict:
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    try:
        import psutil

        name = psutil.Process(pid.value).name()
    except Exception:
        pass
    return {"title": buf.value, "process": name, "pid": pid.value}


# ------------------------------------------------------------------ app installate


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


ALIASES = {
    "browser": "comet", "chrome": "google chrome", "edge": "microsoft edge", "word": "word", "excel": "excel",
    "blocco note": "blocco note", "notepad": "blocco note", "calcolatrice": "calcolatrice",
    "impostazioni": "impostazioni", "esplora file": "esplora file", "terminale": "terminale",
    "vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
    "musica": "spotify", "task manager": "gestione attivita", "gestione attività": "gestione attivita",
}


class AppIndex:
    def __init__(self, cache: Path | None = None, max_age_s: float = 86400) -> None:
        self.cache = cache or (paths.DATA_DIR / "cache" / "apps.json")
        self.max_age_s = max_age_s
        self.apps: list[dict] = []

    def load(self, force: bool = False) -> list[dict]:
        fresh = self.cache.exists() and time.time() - self.cache.stat().st_mtime < self.max_age_s
        if not force and fresh:
            self.apps = json.loads(self.cache.read_text(encoding="utf-8"))
            return self.apps
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(out.stdout or "[]")
        self.apps = [{"name": a["Name"], "id": a["AppID"]} for a in (data if isinstance(data, list) else [data])]
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps(self.apps, ensure_ascii=False), encoding="utf-8")
        return self.apps

    def find_exact(self, query: str) -> dict | None:
        """L'app che si chiama proprio così (alias compresi): batte un sito con lo stesso nome ("Paint.NET")."""
        if not self.apps:
            self.load()
        q = _norm(ALIASES.get(_norm(query), query))
        return next((a for a in self.apps if _norm(a["name"]) == q), None)

    def find(self, query: str) -> dict | None:
        if not self.apps:
            self.load()
        q = _norm(ALIASES.get(_norm(query), query))
        best, best_score = None, 0.0
        for app in self.apps:
            n = _norm(app["name"])
            if n == q:
                return app
            score = difflib.SequenceMatcher(None, q, n).ratio()
            if q and (n.startswith(q) or f" {q}" in f" {n}"):
                score = max(score, 0.85 - 0.002 * len(n))  # parola contenuta: preferisci il nome più corto
            if score > best_score:
                best, best_score = app, score
        return best if best_score >= 0.6 else None

    @staticmethod
    def launch(app: dict) -> None:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['id']}"], creationflags=subprocess.CREATE_NO_WINDOW)


KNOWN_FOLDERS = {
    "desktop": Path.home() / "Desktop", "scrivania": Path.home() / "Desktop",
    "download": Path.home() / "Downloads", "downloads": Path.home() / "Downloads",
    "documenti": Path.home() / "Documents", "immagini": Path.home() / "Pictures", "musica": Path.home() / "Music",
    "video": Path.home() / "Videos",
}
_EXPLICIT_URL = re.compile(r"^(https?://|www\.)\S+$", re.I)
_DOMAIN = re.compile(r"^[\w-]+(?:\.[\w-]+)*\.([a-z]{2,24})(?::\d+)?(?:/\S*)?$", re.I)
# "relazione.docx" o "setup.exe" sono file, non siti: prima diventavano https://relazione.docx
FILE_EXTENSIONS = set(
    "exe msi bat cmd ps1 lnk txt md pdf doc docx xls xlsx ppt pptx odt ods csv json xml yaml yml ini cfg log "
    "png jpg jpeg gif bmp webp svg ico mp3 wav flac ogg m4a mp4 mkv avi mov webm zip rar 7z tar gz iso "
    "py js ts html htm css c cpp h java cs rs go sln blend psd".split())

_apps = AppIndex()


def _site(w: str) -> str | None:
    """L'indirizzo, se w è un sito (www.x.it, youtube.com, https://...); None se è altro (file, app, frase)."""
    if _EXPLICIT_URL.match(w):
        return w if w.lower().startswith("http") else f"https://{w}"
    m = _DOMAIN.match(w)
    return f"https://{w}" if m and m.group(1).lower() not in FILE_EXTENSIONS else None


def open_target(what: str) -> str:
    """App, sito, cartella o file. Ordine: indirizzo esplicito, cartelle note, file o cartelle che esistono,
    app con quel nome esatto, dominio, app che gli somiglia."""
    w = what.strip().strip('"').strip()
    if not w:
        return "Errore: non mi hai detto cosa aprire"
    if _EXPLICIT_URL.match(w):
        url = _site(w)
        webbrowser.open(url)
        return f"aperto {url} nel browser predefinito"
    if _norm(w) in KNOWN_FOLDERS:
        os.startfile(KNOWN_FOLDERS[_norm(w)])
        return f"aperta la cartella {KNOWN_FOLDERS[_norm(w)]}"
    p = Path(os.path.expandvars(os.path.expanduser(w)))
    if p.is_absolute() and p.exists():
        os.startfile(p)
        return f"aperto {p}"
    app = _apps.find_exact(w)
    if app is None and (url := _site(w)):
        webbrowser.open(url)
        return f"aperto {url} nel browser predefinito"
    app = app or _apps.find(w)
    if app:
        AppIndex.launch(app)
        return f"avviata l'app {app['name']}"
    hint = " (per un file serve il percorso completo)" if Path(w).suffix.lower().lstrip(".") in FILE_EXTENSIONS else ""
    return f"Errore: non trovo né un'app né un percorso né un sito chiamato '{what}'{hint}"


# ------------------------------------------------------------------ volume e musica


def _endpoint():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def _level(level) -> int | None:
    """20, "20", "20%", "20,5" -> numero; None/"" -> None. Il cervello a volte passa i numeri come testo."""
    if level is None or str(level).strip() == "":
        return None
    return round(float(str(level).strip().rstrip("%").replace(",", ".")))


def volume(action: str, level: int | str | None = None) -> str:
    level = _level(level)
    ep = _endpoint()
    cur = round(ep.GetMasterVolumeLevelScalar() * 100)
    if action == "leggi":
        return f"volume al {cur}%{' (muto)' if ep.GetMute() else ''}"
    if action == "muto":
        ep.SetMute(1, None)
        return "audio silenziato"
    if action == "riattiva":
        ep.SetMute(0, None)
        return f"audio riattivato, volume al {cur}%"
    if action in ("alza", "abbassa"):
        step = abs(level) if level is not None else 10  # "abbassa di -10" resta un abbassare
        new = max(0, min(100, cur + (step if action == "alza" else -step)))
    elif action == "imposta":
        if level is None:
            return "Errore: serve il livello (0-100)"
        new = max(0, min(100, level))
    else:
        return f"Errore: azione sconosciuta '{action}'"
    ep.SetMasterVolumeLevelScalar(new / 100, None)
    if new > 0:
        ep.SetMute(0, None)
    return f"volume dal {cur}% al {new}%"


_MEDIA_KEYS = {"play_pausa": 0xB3, "successivo": 0xB0, "precedente": 0xB1, "stop": 0xB2}


def media(action: str) -> str:
    vk = _MEDIA_KEYS.get(action)
    if vk is None:
        return f"Errore: azione sconosciuta '{action}' (valide: {', '.join(_MEDIA_KEYS)})"
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
    return f"comando multimediale inviato: {action}"


# ------------------------------------------------------------------ appunti

CF_UNICODETEXT = 13
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]


def _open_clipboard() -> None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.03)
    raise OSError("appunti occupati da un altro programma")


def clipboard_read() -> str:
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_write(text: str) -> None:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
        if not h:
            raise OSError("memoria per gli appunti non disponibile")
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):  # senza controllo diceva "copiato" anche se non lo era
            kernel32.GlobalFree(h)
            raise OSError(f"Windows ha rifiutato gli appunti (errore {ctypes.get_last_error()})")
    finally:
        user32.CloseClipboard()


def lock_screen() -> str:
    user32.LockWorkStation()
    return "schermo bloccato"

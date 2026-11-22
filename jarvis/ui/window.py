"""Finestra di Jarvis (WebView2 via pywebview), in un processo separato dal cervello.

Se la finestra si chiude o si blocca, Jarvis continua ad ascoltare; si può riaprire quando si vuole.
Uso: pythonw -m jarvis.ui.window [--port 8791]
"""

from __future__ import annotations

import argparse
import os
import re
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Iterable
from pathlib import Path

import webview

RUNTIME_KEY = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # Microsoft Edge WebView2 Runtime


def runtime_registered() -> bool:
    """WebView2 come lo cerca pywebview: la voce di Edge Update. Senza, pywebview passa di nascosto a MSHTML
    (Internet Explorer), che disegna la pagina rotta: visto in Windows Sandbox il 22/11, dove WebView2 c'è ma la voce
    no."""
    import winreg

    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{RUNTIME_KEY}"),
                       (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{RUNTIME_KEY}")):
        try:
            with winreg.OpenKey(hive, path) as key:
                if str(winreg.QueryValueEx(key, "pv")[0]).split(".")[0] not in ("", "0"):
                    return True
        except OSError:
            pass
    return False


def runtime_roots() -> list[Path]:
    return [Path(os.environ.get(var, default)) / "Microsoft" / "EdgeWebView" / "Application"
            for var, default in (("ProgramFiles(x86)", r"C:\Program Files (x86)"), ("ProgramFiles", r"C:\Program Files"),
                                 ("LOCALAPPDATA", ""))]


def runtime_folder(roots: Iterable[Path]) -> Path | None:
    """La versione più nuova di WebView2 nelle sue cartelle (una sottocartella "131.0.2903.70" con l'eseguibile)."""
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if re.fullmatch(r"\d+(\.\d+){1,3}", d.name) and (d / "msedgewebview2.exe").is_file():
                found.append((tuple(int(x) for x in d.name.split(".")), d))
    return max(found)[1] if found else None


def engine(registered: Callable[[], bool] = runtime_registered,
           folder: Callable[[], Path | None] = lambda: runtime_folder(runtime_roots())) -> tuple[str, Path | None]:
    """"webview2" (registrato), "cartella" (c'è ma non registrato: pywebview lo usa se gli si dà la cartella) o
    "browser" (non c'è: la pagina si apre nel browser, mai la finestra rotta di Internet Explorer)."""
    if registered():
        return "webview2", None
    found = folder()
    return ("cartella", found) if found else ("browser", None)


def wait_server(url: str, timeout: float = 120) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}/"
    wait_server(url)
    how, folder = engine()
    if how == "browser":  # senza WebView2: la stessa pagina nel browser; questo processo resta vivo come la finestra
        webbrowser.open(url)  # (il primo avvio e Jarvis lo chiudono quando hanno finito)
        while True:
            time.sleep(3600)
    if folder is not None:
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(folder)
    webview.create_window("Jarvis", url, width=1060, height=720, min_size=(420, 560), frameless=True,
                          easy_drag=False, background_color="#000000", text_select=True)
    webview.start(private_mode=False, storage_path=None)


if __name__ == "__main__":
    main()

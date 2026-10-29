"""Finestra di Jarvis (WebView2 via pywebview), in un processo separato dal cervello.

Se la finestra si chiude o si blocca, Jarvis continua ad ascoltare; si può riaprire quando si vuole.
Uso: pythonw -m jarvis.ui.window [--port 8791]
"""

from __future__ import annotations

import argparse
import time
import urllib.request

import webview


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
    webview.create_window("Jarvis", url, width=1060, height=720, min_size=(420, 560), frameless=True,
                          easy_drag=False, background_color="#000000", text_select=True)
    webview.start(private_mode=False, storage_path=None)


if __name__ == "__main__":
    main()

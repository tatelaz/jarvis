"""Prova dal vivo: la finestra vera di Jarvis (pywebview + WebView2) si sposta trascinandola dalla fascia in alto.

Apre la finestra sulla vetrina, simula nella pagina "premi sulla fascia, sposta, rilascia" e controlla che la finestra
si sia mossa davvero; poi prova che premere sulla scena (fuori dalla fascia) non la sposta. Il mouse vero non si tocca.
Uso: python scripts/window_drag_live.py   (stampa OK o l'errore; codice di uscita 0 solo se va tutto bene)
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
PORT = 8789
result: dict = {}


def press_move_release(win: webview.Window, x: int, y: int, dx: int, dy: int) -> None:
    """Gli stessi eventi che manda il mouse: pywebview li ascolta per spostare la finestra."""
    win.evaluate_js(f"""(() => {{
        const t = document.elementFromPoint({x}, {y});
        const base = {{bubbles: true, clientX: {x}, clientY: {y}, screenX: window.screenX + {x}, screenY: window.screenY + {y}}};
        t.dispatchEvent(new MouseEvent('mousedown', base));
        window.dispatchEvent(new MouseEvent('mousemove', {{...base, screenX: base.screenX + {dx}, screenY: base.screenY + {dy}}}));
        window.dispatchEvent(new MouseEvent('mouseup', base));
        return t.id || t.tagName;
    }})()""")


def check(win: webview.Window) -> None:
    try:
        time.sleep(3)  # pagina caricata e script di pywebview iniettati
        x0, y0 = win.x, win.y
        press_move_release(win, 300, 12, 150, 90)  # sulla fascia
        time.sleep(1)
        moved = (win.x - x0, win.y - y0)
        x1, y1 = win.x, win.y
        press_move_release(win, 500, 400, 150, 90)  # sulla scena: non deve spostarla
        time.sleep(1)
        result.update(dalla_fascia=moved, dalla_scena=(win.x - x1, win.y - y1))
    except Exception as e:  # la prova riporta, non nasconde
        result["errore"] = repr(e)
    finally:
        win.destroy()


def main() -> int:
    vetrina = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "vetrina.py"), "--porta", str(PORT)],
                               cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.25)
        win = webview.create_window("Jarvis (prova)", f"http://127.0.0.1:{PORT}/", width=1060, height=720, x=200, y=150,
                                    frameless=True, easy_drag=False, background_color="#000000", text_select=True)
        webview.start(check, win, private_mode=True)
    finally:
        vetrina.terminate()
    print(result)
    dx, dy = result.get("dalla_fascia", (0, 0))
    ok = "errore" not in result and abs(dx) > 50 and abs(dy) > 30 and result.get("dalla_scena") == (0, 0)
    print("OK" if ok else "NON VA")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Collegamento "Jarvis" sul Desktop (come l'avvio automatico: guardiano senza console) con l'icona dell'anello.

Doppio clic: se Jarvis è spento si accende; se è già acceso si apre la sua finestra.
Uso: python scripts/tools/make_shortcut.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ICON = ROOT / "jarvis" / "ui" / "jarvis.ico"


def make_icon(path: Path = ICON) -> Path:
    """Lo stesso disegno dell'icona della finestra: anello azzurro su nero, disegnato in grande e rimpicciolito."""
    big = 1024
    img = Image.new("RGBA", (big, big), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    r, w = big * 9.5 / 32, big * 2 / 32
    c = big / 2
    d.ellipse([c - r - w / 2, c - r - w / 2, c + r + w / 2, c + r + w / 2], outline=(0x8F, 0xD8, 0xFF, 255),
              width=round(w))
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def make_shortcut() -> Path:
    from jarvis import autostart

    desktop = Path.home() / "Desktop"
    lnk = desktop / "Jarvis.lnk"
    pythonw, script = autostart.command(ROOT).split('" "')
    ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
          f"$s.TargetPath = '{pythonw.strip(chr(34))}'; $s.Arguments = '\"{script.strip(chr(34))}\"'; "
          f"$s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{make_icon()},0'; "
          "$s.Description = 'Accende Jarvis (o apre la sua finestra se è già acceso)'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    return lnk


if __name__ == "__main__":
    print(make_shortcut())
    sys.exit(0)

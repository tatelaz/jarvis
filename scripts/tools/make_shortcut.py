"""Collegamento "Jarvis" sul Desktop (come l'avvio automatico: guardiano senza console) con l'icona di Jarvis.

Doppio clic: se Jarvis è spento si accende; se è già acceso si apre la sua finestra.
Uso: python scripts/tools/make_shortcut.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ICON = ROOT / "jarvis" / "ui" / "jarvis.ico"  # la crea scripts/tools/make_icon.py dalle sorgenti SVG


def make_shortcut() -> Path:
    from jarvis import autostart

    desktop = Path.home() / "Desktop"
    lnk = desktop / "Jarvis.lnk"
    pythonw, script = autostart.command(ROOT).split('" "')
    ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
          f"$s.TargetPath = '{pythonw.strip(chr(34))}'; $s.Arguments = '\"{script.strip(chr(34))}\"'; "
          f"$s.WorkingDirectory = '{ROOT}'; $s.IconLocation = '{ICON},0'; "
          "$s.Description = 'Accende Jarvis (o apre la sua finestra se è già acceso)'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    return lnk


if __name__ == "__main__":
    print(make_shortcut())
    sys.exit(0)

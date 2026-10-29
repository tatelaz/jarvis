"""Avvia questa copia del codice come un'installazione a sé: dati suoi, motori e modelli presi da un'altra copia.

Serve per provare una versione nuova (o il primo avvio di un PC mai visto) senza toccare la copia di tutti i giorni e
senza duplicare i modelli. Tipico: una git worktree del progetto, avviata con il Python della copia principale:

    <principale>\\.venv\\Scripts\\python scripts\\avvia_prova.py --da "<cartella della copia principale>"

I dati (configurazione, memoria, voci, WhatsApp, registri) restano nella cartella `data` di questa copia, oppure in
quella data con --dati. Due Jarvis non possono stare accesi insieme (stesse porte, e la scheda video non basta per due
voci): se l'altro è acceso, lo script si ferma e lo dice.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_PORT = 8791  # la porta della finestra di Jarvis (ui.port predefinita)


def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def environment(source: Path, data: Path, base: dict[str, str]) -> dict[str, str]:
    """Le variabili per far girare questa copia sopra motori e modelli di `source`, con i dati in `data`."""
    env = dict(base)
    env.update(JARVIS_ENGINE_DIR=str(source / "engine"), JARVIS_MODELS_DIR=str(source / "models"),
               JARVIS_DATA_DIR=str(data), PYTHONUTF8="1")
    return env


def problems(source: Path, data: Path, port: int = UI_PORT) -> list[str]:
    out = []
    for sub in ("engine", "models"):
        if not (source / sub).is_dir():
            out.append(f"in {source} manca la cartella {sub}: indica con --da la copia che ha motori e modelli")
    if data.resolve() == (source / "data").resolve() and source.resolve() != ROOT:
        out.append("i dati sarebbero quelli della copia principale: indica un'altra cartella con --dati")
    if port_busy(port):
        out.append(f"la porta {port} è occupata: c'è già un Jarvis acceso, spegnilo prima")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--da", type=Path, default=ROOT, help="la copia da cui prendere engine e models")
    ap.add_argument("--dati", type=Path, default=ROOT / "data", help="dove tenere i dati di questa prova")
    a = ap.parse_args(argv)
    source, data = a.da.resolve(), a.dati.resolve()
    found = problems(source, data)
    if found:
        for p in found:
            print(f"Non parto: {p}.")
        return 1
    data.mkdir(parents=True, exist_ok=True)
    print(f"Copia: {ROOT}\nMotori e modelli: {source}\nDati: {data}")
    return subprocess.call([sys.executable, "-m", "jarvis", "--console"], cwd=ROOT,
                           env=environment(source, data, dict(os.environ)))


if __name__ == "__main__":
    sys.exit(main())

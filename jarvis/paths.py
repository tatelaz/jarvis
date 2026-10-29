"""Percorsi del progetto. Un solo posto da cui derivare tutto il resto."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Motori, modelli e dati si possono indicare da fuori: i test spostano i dati, e il supervisore fa girare
# una versione precedente del codice (cartella a parte) sopra i motori, i modelli e i dati veri.
ENGINE_DIR = Path(os.environ.get("JARVIS_ENGINE_DIR", ROOT / "engine"))
MODELS_DIR = Path(os.environ.get("JARVIS_MODELS_DIR", ROOT / "models"))
DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", ROOT / "data"))
STATE_DIR = DATA_DIR / "state"  # battito, ultima versione sana, motivo dell'ultimo riavvio

AUDIOCPP_DIR = ENGINE_DIR / "audiocpp"
AUDIOCPP_SERVER = AUDIOCPP_DIR / "audiocpp_server.exe"
AUDIOCPP_MODELS = MODELS_DIR / "audiocpp"
ONNX_DIR = MODELS_DIR / "onnx"

SILERO_VAD = ONNX_DIR / "silero_vad.onnx"
SMART_TURN = ONNX_DIR / "smart-turn-v3.2-cpu.onnx"


def data_path(*parts: str) -> Path:
    """Percorso dentro la cartella dati, creando le cartelle intermedie."""
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    d = DATA_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

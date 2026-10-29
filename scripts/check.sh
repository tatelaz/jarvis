#!/usr/bin/env bash
# Controllo completo prima di ogni commit: lint + test. Esce con errore se qualcosa fallisce.
# JARVIS_PYTHON: il Python da usare (le copie di lavoro dell'auto-miglioramento non hanno un .venv loro).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${JARVIS_PYTHON:-.venv/Scripts/python}"
"$PY" -m ruff check jarvis tests scripts
"$PY" -m pytest -q -m "not engine and not claude and not hardware and not desktop" "$@"

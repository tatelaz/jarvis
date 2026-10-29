"""Avvio senza console (usato dall'avvio automatico con Windows): guardiano + Jarvis con la sua finestra."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from jarvis.supervisor import main  # noqa: E402

sys.exit(main())

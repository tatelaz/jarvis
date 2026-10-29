"""Il blocco <contesto> che accompagna ogni messaggio al cervello.

Contiene ciò che serve per capire "a cosa ti riferisci": l'ora, cosa hai in primo piano sul
PC, i compiti in corso con il loro stato e i ricordi pertinenti a ciò che hai appena detto.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger("jarvis.context")

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
        "novembre", "dicembre"]


def now_it(t: float | None = None) -> str:
    lt = time.localtime(t)
    return f"{GIORNI[lt.tm_wday]} {lt.tm_mday} {MESI[lt.tm_mon - 1]} {lt.tm_year}, ore {lt.tm_hour:02d}:{lt.tm_min:02d}"


class ContextBuilder:
    def __init__(self, tasks_summary: Callable[[], str] | None = None,
                 active_window: Callable[[], dict] | None = None,
                 memory_search: Callable[[str], list[str]] | None = None) -> None:
        self.tasks_summary = tasks_summary
        self.active_window = active_window
        self.memory_search = memory_search

    def build(self, user_text: str = "", owner: bool = True) -> str:
        """owner=False (parla un ospite): solo l'ora. Niente memoria, finestra in primo piano o compiti dell'utente."""
        lines = [f"Adesso è {now_it()}."]
        if not owner:
            return lines[0]
        if self.active_window:
            try:
                w = self.active_window()
                if w.get("title"):
                    lines.append(f"In primo piano sul PC: \"{w['title']}\" ({w.get('process', '')}).")
            except Exception as e:  # il contesto non deve mai bloccare una risposta
                log.debug("finestra attiva non disponibile: %s", e)
        if self.tasks_summary:
            s = self.tasks_summary()
            if s:
                lines.append("Compiti:\n" + s)
        if self.memory_search and user_text:
            try:
                facts = self.memory_search(user_text)
                if facts:
                    lines.append("Ricordi pertinenti:\n" + "\n".join(f"- {f}" for f in facts))
            except Exception as e:
                log.debug("ricerca in memoria fallita: %s", e)
        return "\n".join(lines)

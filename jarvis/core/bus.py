"""Bus degli eventi verso l'interfaccia e i registri: chi è interessato si abbona e riceve tutto.

Gli abbonati sono code asincrone limitate: un'interfaccia lenta o bloccata perde gli eventi più
vecchi invece di rallentare la conversazione.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.bus")


class Bus:
    def __init__(self, history: int = 300, journal: Path | None = None) -> None:
        self._subs: set[asyncio.Queue] = set()
        self.history: deque[dict] = deque(maxlen=history)
        self.journal = journal
        self.state: dict[str, Any] = {}  # ultimo valore di ogni evento "di stato" (per chi si collega dopo)

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def emit(self, kind: str, /, sticky: bool = False, **data: Any) -> dict:
        ev = {"type": kind, "ts": time.time(), **data}
        if sticky:
            self.state[kind] = ev
        if kind != "level":
            self.history.append(ev)
            if self.journal is not None:
                try:
                    with self.journal.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                except OSError:
                    pass
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)
        return ev

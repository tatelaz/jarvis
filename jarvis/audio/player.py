"""Riproduzione della voce di Jarvis.

Il callback del dispositivo audio chiama `render(n)` dal suo thread; tutto il resto (accodare,
fermare, abbassare il volume) arriva dal thread principale. Le sezioni protette dal lock sono
minime, per non causare mai buchi nell'audio.

Funzioni chiave per la conversazione:
- `duck()`/`unduck()`: abbassa e rialza il volume con una rampa (niente clic) mentre si capisce se
  l'utente sta davvero interrompendo;
- `stop()`: taglio con dissolvenza di pochi millisecondi;
- traccia di quanto di ogni frase è stato effettivamente riprodotto: se Jarvis viene interrotto,
  sappiamo fin dove è arrivato e il cervello non crede di aver detto ciò che non ha detto.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

_ids = itertools.count(1)


@dataclass
class Segment:
    audio: np.ndarray  # mono float32 alla frequenza del dispositivo
    text: str = ""
    meta: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_ids))
    pos: int = 0
    finished: bool = False
    interrupted: bool = False

    @property
    def fraction_played(self) -> float:
        return self.pos / max(1, self.audio.size)

    def spoken_text(self) -> str:
        """Il testo pronunciato fin qui (stima proporzionale, tagliata a parola intera)."""
        if not self.interrupted:
            return self.text
        cut = int(len(self.text) * self.fraction_played)
        part = self.text[:cut]
        return part.rsplit(" ", 1)[0] if " " in part and cut < len(self.text) else part


class Player:
    def __init__(self, sample_rate: int, ramp_ms: float = 30.0, fade_ms: float = 15.0,
                 on_event: Callable[[str, Segment], None] | None = None) -> None:
        self.sr = sample_rate
        self._q: deque[Segment] = deque()
        self._lock = threading.Lock()
        self._gain = 1.0
        self._target = 1.0
        self._step = 1.0 / max(1, int(sample_rate * ramp_ms / 1000))
        self._fade_len = max(1, int(sample_rate * fade_ms / 1000))
        self._fading = 0  # campioni di dissolvenza rimasti
        self.volume = 1.0
        self.on_event = on_event
        self._pending_events: list[tuple[str, Segment]] = []
        self.history: deque[Segment] = deque(maxlen=50)

    # ------------------------------------------------------------ comandi

    def enqueue(self, seg: Segment) -> Segment:
        with self._lock:
            self._q.append(seg)
        return seg

    @property
    def speaking(self) -> bool:
        return bool(self._q)

    @property
    def current(self) -> Segment | None:
        with self._lock:
            return self._q[0] if self._q else None

    def duck(self, gain: float) -> None:
        self._target = float(np.clip(gain, 0.0, 1.0))

    def unduck(self) -> None:
        self._target = 1.0

    def stop(self) -> list[Segment]:
        """Ferma tutto con una breve dissolvenza; restituisce i segmenti tolti (il primo era in corso)."""
        with self._lock:
            if not self._q:
                return []
            dropped = list(self._q)
            head = self._q[0]
            if head.pos > 0 and self._fading == 0:
                self._fading = self._fade_len  # il primo finisce sfumando, gli altri spariscono
                rest = list(self._q)[1:]
                self._q = deque([head])
            else:
                rest = list(self._q)
                self._q.clear()
                self._fading = 0
            for s in rest:
                s.interrupted = True
                s.finished = True
                self._pending_events.append(("segment_dropped", s))
            head.interrupted = True
        self._flush_events()
        return dropped

    # ------------------------------------------------------------ thread audio

    def render(self, n: int) -> np.ndarray:
        """Chiamato dal callback del dispositivo: restituisce esattamente n campioni."""
        out = np.zeros(n, np.float32)
        filled = 0
        with self._lock:
            while filled < n and self._q:
                seg = self._q[0]
                if seg.pos == 0:
                    self._pending_events.append(("segment_start", seg))
                take = min(n - filled, seg.audio.size - seg.pos)
                if self._fading:
                    take = min(take, self._fading)
                chunk = seg.audio[seg.pos:seg.pos + take]
                if self._fading:
                    start = self._fade_len - self._fading
                    env = 1.0 - (np.arange(start, start + take) + 1) / self._fade_len
                    chunk = chunk * env
                    self._fading -= take
                out[filled:filled + take] = chunk
                seg.pos += take
                filled += take
                if seg.pos >= seg.audio.size or (seg.interrupted and self._fading == 0):
                    seg.finished = True
                    self._q.popleft()
                    self.history.append(seg)
                    self._pending_events.append(("segment_end", seg))
        # guadagno con rampa (ducking) + volume
        if self._gain != self._target or self._gain != 1.0:
            g = np.empty(n, np.float32)
            cur = self._gain
            direction = np.sign(self._target - cur)
            if direction == 0:
                g.fill(cur)
            else:
                ramp = cur + direction * self._step * np.arange(1, n + 1)
                g[:] = np.minimum(ramp, self._target) if direction > 0 else np.maximum(ramp, self._target)
            self._gain = float(g[-1])
            out *= g
        if self.volume != 1.0:
            out *= self.volume
        self._flush_events()
        return out

    def _flush_events(self) -> None:
        """Consegna gli eventi fuori dal lock. `on_event` può essere chiamato dal thread audio:
        deve solo inoltrare (es. con loop.call_soon_threadsafe), mai bloccare."""
        if not self._pending_events:
            return
        with self._lock:
            events, self._pending_events = self._pending_events, []
        if self.on_event:
            for kind, seg in events:
                self.on_event(kind, seg)

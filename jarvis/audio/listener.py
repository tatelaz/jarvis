"""Turni di parola: quando l'utente inizia a parlare, quando fa solo una pausa, quando ha finito.

Riceve audio a 16 kHz già ripulito dall'eco, a blocchi di qualunque lunghezza, e produce eventi:
- `speech_start`: inizio di una frase vera (non un colpo di tosse: serve voce per `min_speech_ms`);
- `partial`: a intervalli regolari durante la frase, con l'audio finora (per il testo in tempo reale);
- `turn_end`: fine del turno, con tutto l'audio della frase e il motivo.

La fine del turno non è un semplice timeout di silenzio: dopo una pausa breve chiede a Smart Turn
se la frase suona conclusa ("...e poi?" no, "...grazie." sì). Se non lo è, aspetta ancora, fino a un
silenzio massimo. Così non taglia a metà chi riflette e non fa aspettare chi ha finito.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..config import AudioConfig
from .vad import WINDOW

SR = 16000
WIN_MS = WINDOW * 1000 / SR  # 32 ms


@dataclass
class ListenEvent:
    kind: str  # "speech_start" | "partial" | "turn_end"
    t: float  # secondi dall'avvio del listener, alla fine della finestra che ha generato l'evento
    audio: np.ndarray | None = None
    info: dict = field(default_factory=dict)


class Listener:
    def __init__(self, cfg: AudioConfig, vad_fn: Callable[[np.ndarray], list[float]],
                 turn_fn: Callable[[np.ndarray], float], partial_interval_ms: int = 450,
                 max_utterance_ms: int = 45000, recheck_ms: int = 220) -> None:
        self.cfg = cfg
        self._vad = vad_fn
        self._turn = turn_fn
        self.partial_interval_ms = partial_interval_ms
        self.max_utterance_ms = max_utterance_ms
        self.recheck_ms = recheck_ms
        self.jarvis_speaking = False
        self.enabled = True
        self._samples = 0
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    # ------------------------------------------------------------ stato

    def reset(self) -> None:
        self.state = "idle"
        self._pre: list[np.ndarray] = []
        self._utt: list[np.ndarray] = []
        self._run_ms = 0.0  # voce consecutiva (in idle) / silenzio consecutivo (in speech)
        self._candidate: list[np.ndarray] = []
        self._since_partial = 0.0
        self._utt_ms = 0.0
        self._voiced_ms = 0.0
        self._next_check_ms = 0.0
        self.last_turn_prob: float | None = None

    def set_jarvis_speaking(self, speaking: bool) -> None:
        self.jarvis_speaking = speaking

    @property
    def threshold(self) -> float:
        return self.cfg.vad_threshold_while_speaking if self.jarvis_speaking else self.cfg.vad_threshold

    @property
    def now(self) -> float:
        return self._samples / SR

    def snapshot(self) -> np.ndarray:
        """Copia dell'audio della frase in corso, leggibile da un altro thread."""
        parts = list(self._utt)
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def utterance_audio(self) -> np.ndarray:
        return np.concatenate(self._utt) if self._utt else np.zeros(0, np.float32)

    # ------------------------------------------------------------ ingresso

    def feed(self, audio: np.ndarray) -> list[ListenEvent]:
        """Accoda audio; restituisce gli eventi generati dalle finestre complete."""
        events: list[ListenEvent] = []
        self._pending = np.concatenate([self._pending, np.asarray(audio, dtype=np.float32)])
        while self._pending.size >= WINDOW:
            win, self._pending = self._pending[:WINDOW], self._pending[WINDOW:]
            self._samples += WINDOW
            (prob,) = self._vad(win)
            events.extend(self._step(win, prob))
        return events

    def _step(self, win: np.ndarray, prob: float) -> list[ListenEvent]:
        if not self.enabled:
            return []
        thr = self.threshold
        if self.state == "idle":
            return self._step_idle(win, prob, thr)
        return self._step_speech(win, prob, thr)

    def _step_idle(self, win: np.ndarray, prob: float, thr: float) -> list[ListenEvent]:
        if prob >= thr:
            self._candidate.append(win)
            self._run_ms += WIN_MS
            if self._run_ms >= self.cfg.min_speech_ms:
                self.state = "speech"
                self._utt = [*self._pre, *self._candidate]
                self._utt_ms = sum(len(x) for x in self._utt) * 1000 / SR
                self._pre, self._candidate = [], []
                self._voiced_ms = self._run_ms
                self._run_ms = 0.0
                self._since_partial = 0.0
                return [ListenEvent("speech_start", self.now, info={"prob": prob})]
            return []
        # silenzio: i candidati tornano nel pre-roll, che resta lungo al massimo pre_roll_ms
        self._pre.extend(self._candidate)
        self._pre.append(win)
        self._candidate = []
        self._run_ms = 0.0
        max_pre = max(1, int(self.cfg.pre_roll_ms / WIN_MS))
        if len(self._pre) > max_pre:
            self._pre = self._pre[-max_pre:]
        return []

    def _step_speech(self, win: np.ndarray, prob: float, thr: float) -> list[ListenEvent]:
        events: list[ListenEvent] = []
        self._utt.append(win)
        self._utt_ms += WIN_MS
        self._since_partial += WIN_MS
        silent = prob < max(0.05, thr - 0.15)
        if silent:
            self._run_ms += WIN_MS
        else:
            self._run_ms = 0.0
            self._next_check_ms = 0.0
        if prob >= thr:
            self._voiced_ms += WIN_MS
        if not silent and self._since_partial >= self.partial_interval_ms:
            self._since_partial = 0.0
            events.append(ListenEvent("partial", self.now, self.utterance_audio()))
        reason = None
        if self._utt_ms >= self.max_utterance_ms:
            reason = "max_length"
        elif self._run_ms >= self.cfg.max_silence_ms:
            reason = "max_silence"
        elif self._run_ms >= self.cfg.min_silence_ms and self._run_ms >= self._next_check_ms:
            p = self._turn(self.utterance_audio())
            self.last_turn_prob = p
            if p >= self.cfg.turn_threshold:
                reason = "smart_turn"
            else:
                self._next_check_ms = self._run_ms + self.recheck_ms
        if reason:
            audio = self.utterance_audio()
            info = {"reason": reason, "turn_prob": self.last_turn_prob, "silence_ms": self._run_ms,
                    "duration_ms": len(audio) * 1000 / SR, "voiced_ms": self._voiced_ms}
            self.reset()
            events.append(ListenEvent("turn_end", self.now, audio, info))
        return events

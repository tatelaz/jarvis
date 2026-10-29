"""Effetto "voce di JARVIS": dà alla voce pulita il carattere digitale del film.

Ricavato dal confronto tra le battute originali del film e le stesse restaurate:
- colorazione tonale (EQ "a corrispondenza"): niente bassi profondi, risonanza da casco
  tra 200 e 600 Hz, buco intorno ai 2 kHz, brillantezza tra 3 e 12 kHz;
- ondulazione dello spettro con passo di ~2 kHz (picco cepstrale a ~0,5 ms), cioè un
  filtro a pettine cortissimo: qui un flanger lento attorno a 0,5 ms, che dà il "suonino";
- facoltativi: prime riflessioni corte (spazio chiuso) e una leggera saturazione.

Tutto è deterministico, costa pochi millisecondi per frase e ha un'intensità 0..1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal

# Differenza media film − restaurato (dB) per banda, misurata su ~12 s di battute di Jarvis.
FILM_EQ: list[tuple[float, float]] = [
    (0, -14.0), (60, -12.0), (80, -10.1), (125, -3.8), (175, -0.4), (250, 2.7), (350, 5.4),
    (500, 3.0), (700, 0.2), (900, -1.5), (1250, -1.3), (1750, -6.0), (2500, -2.1), (3500, 4.9),
    (4500, 2.1), (5500, 4.8), (7000, 5.7), (9000, 5.9), (11000, 5.7), (13000, -2.5), (15000, -11.4),
    (18000, -24.0), (24000, -30.0),
]


@dataclass
class VoiceFxParams:
    intensity: float = 1.0  # scala tutto: 0 = voce pulita
    eq_strength: float = 0.8  # quanto della colorazione del film applicare
    flanger_mix: float = 0.35
    flanger_delay_ms: float = 0.5
    flanger_depth_ms: float = 0.15
    flanger_rate_hz: float = 0.25
    flanger_feedback: float = 0.25
    reflections_mix: float = 0.0
    reflections_ms: list[float] = field(default_factory=lambda: [3.7, 6.9, 11.3, 17.9])
    drive: float = 0.0  # saturazione morbida (0 = spenta)


class VoiceFx:
    def __init__(self, params: VoiceFxParams | None = None) -> None:
        self.p = params or VoiceFxParams()
        self._eq_cache: dict[int, np.ndarray] = {}

    # ---- singoli stadi

    def _eq_fir(self, sr: int) -> np.ndarray:
        if sr not in self._eq_cache:
            nyq = sr / 2
            pts = [(f, g) for f, g in FILM_EQ if f < nyq] + [(nyq, FILM_EQ[-1][1])]
            freqs = np.array([f for f, _ in pts]) / nyq
            gains = 10 ** (self.p.eq_strength * np.array([g for _, g in pts]) / 20)
            numtaps = 1023 if sr > 32000 else 511
            self._eq_cache[sr] = signal.firwin2(numtaps, freqs, gains)
        return self._eq_cache[sr]

    def _flanger(self, x: np.ndarray, sr: int) -> np.ndarray:
        p = self.p
        n = np.arange(x.size)
        delay = (p.flanger_delay_ms + p.flanger_depth_ms * np.sin(2 * np.pi * p.flanger_rate_hz * n / sr)) * sr / 1000
        y = x.astype(np.float64).copy()
        # due passaggi = retroazione approssimata senza ricorsione campione per campione
        for _ in range(2 if p.flanger_feedback > 0 else 1):
            pos = n - delay
            i0 = np.floor(pos).astype(np.int64)
            frac = pos - i0
            valid = i0 >= 0
            i0c = np.clip(i0, 0, x.size - 1)
            i1c = np.clip(i0 + 1, 0, x.size - 1)
            delayed = np.where(valid, y[i0c] * (1 - frac) + y[i1c] * frac, 0.0)
            y = x + p.flanger_feedback * delayed
        wet = 0.5 * (x + delayed)
        return (1 - p.flanger_mix) * x + p.flanger_mix * wet

    def _reflections(self, x: np.ndarray, sr: int) -> np.ndarray:
        ir = np.zeros(int(max(self.p.reflections_ms) * sr / 1000) + 2)
        ir[0] = 1.0
        for k, ms in enumerate(self.p.reflections_ms):
            ir[int(ms * sr / 1000)] += self.p.reflections_mix * (0.75 ** k) * (-1) ** k
        return signal.fftconvolve(x, ir)[: x.size]

    # ---- catena completa

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        x = np.asarray(audio, dtype=np.float64)
        k = float(np.clip(self.p.intensity, 0.0, 1.0))
        if k == 0 or x.size == 0:
            return np.asarray(audio, dtype=np.float32)
        rms_in = np.sqrt(np.mean(x ** 2)) + 1e-12
        y = signal.fftconvolve(x, self._eq_fir(sr), mode="same")
        if self.p.flanger_mix > 0:
            y = self._flanger(y, sr)
        if self.p.reflections_mix > 0:
            y = self._reflections(y, sr)
        if self.p.drive > 0:
            d = 1 + 4 * self.p.drive
            y = np.tanh(d * y) / np.tanh(d)
        y = (1 - k) * x + k * y
        y *= rms_in / (np.sqrt(np.mean(y ** 2)) + 1e-12)  # stesso volume percepito di prima
        peak = np.max(np.abs(y))
        if peak > 0.98:
            y *= 0.98 / peak
        return y.astype(np.float32)

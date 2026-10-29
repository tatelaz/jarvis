"""Spettrogramma log-mel in stile Whisper, in numpy puro.

Riproduce `transformers.WhisperFeatureExtractor` (80 bande, n_fft 400, hop 160, finestra di Hann
periodica, filtri mel "slaney") senza dover installare transformers e torch solo per questo.
La parità con l'implementazione di riferimento è verificata in tests/test_features.py.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

SAMPLE_RATE = 16000
N_FFT = 400
HOP = 160
N_MELS = 80


def _hz_to_mel(freq: np.ndarray) -> np.ndarray:
    freq = np.asarray(freq, dtype=np.float64)
    min_log_hz, min_log_mel = 1000.0, 15.0
    logstep = 27.0 / np.log(6.4)
    mels = 3.0 * freq / 200.0
    log_region = freq >= min_log_hz
    mels = np.where(log_region, min_log_mel + np.log(np.maximum(freq, 1e-12) / min_log_hz) * logstep, mels)
    return mels


def _mel_to_hz(mels: np.ndarray) -> np.ndarray:
    mels = np.asarray(mels, dtype=np.float64)
    min_log_hz, min_log_mel = 1000.0, 15.0
    logstep = np.log(6.4) / 27.0
    freq = 200.0 * mels / 3.0
    return np.where(mels >= min_log_mel, min_log_hz * np.exp(logstep * (mels - min_log_mel)), freq)


@lru_cache(maxsize=4)
def mel_filters(n_mels: int = N_MELS, n_fft: int = N_FFT, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Banco di filtri triangolari (n_freq, n_mels) con normalizzazione slaney."""
    n_freq = 1 + n_fft // 2
    fft_freqs = np.linspace(0, sr // 2, n_freq)
    mel_pts = np.linspace(_hz_to_mel(np.array(0.0)), _hz_to_mel(np.array(sr / 2)), n_mels + 2)
    filter_freqs = _mel_to_hz(mel_pts)
    diff = np.diff(filter_freqs)
    slopes = filter_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    fb = np.maximum(0.0, np.minimum(down, up))
    enorm = 2.0 / (filter_freqs[2 : n_mels + 2] - filter_freqs[:n_mels])
    return fb * enorm[None, :]


@lru_cache(maxsize=2)
def _hann(n: int) -> np.ndarray:
    return np.hanning(n + 1)[:-1]  # periodica, come window_function(..., "hann")


def log_mel(audio: np.ndarray) -> np.ndarray:
    """Log-mel Whisper di un segnale a 16 kHz: restituisce (80, n_frames) in float32."""
    x = np.asarray(audio, dtype=np.float64)
    pad = N_FFT // 2
    x = np.pad(x, (pad, pad), mode="reflect")
    n_frames = 1 + (x.size - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = x[idx] * _hann(N_FFT)[None, :]
    power = np.abs(np.fft.rfft(frames, n=N_FFT, axis=1)) ** 2  # (frames, 201)
    mel = np.maximum(1e-10, mel_filters().T @ power.T)  # (80, frames)
    spec = np.log10(mel)[:, :-1]
    spec = np.maximum(spec, spec.max() - 8.0)
    return ((spec + 4.0) / 4.0).astype(np.float32)


def normalize(audio: np.ndarray) -> np.ndarray:
    """Media zero e varianza unitaria (do_normalize=True del feature extractor)."""
    x = np.asarray(audio, dtype=np.float64)
    return (x - x.mean()) / np.sqrt(x.var() + 1e-7)

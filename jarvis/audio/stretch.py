"""Cambia la durata del parlato senza cambiarne l'altezza (WSOLA).

Serve a dare alla voce un ritmo costante: Qwen3-TTS parla tra 18 e 26 caratteri al secondo a seconda
della frase e del seme, e il motore ignora il parametro di velocità. Ogni frase viene portata al ritmo
voluto prima dell'effetto "film".

WSOLA: si prendono finestre di ~30 ms dall'originale con un passo diverso da quello con cui si
rimettono insieme; ogni finestra viene cercata in un piccolo intorno (±8 ms) dove si raccorda meglio
con la precedente, così non nascono scatti né eco metallica.
"""

from __future__ import annotations

import numpy as np


def wsola(x: np.ndarray, factor: float, sr: int, frame_ms: float = 30.0, tol_ms: float = 8.0) -> np.ndarray:
    """factor > 1 allunga (più lento), factor < 1 accorcia. Restituisce float32 lungo ~len(x)·factor."""
    x = np.asarray(x, np.float32)
    if x.size == 0 or abs(factor - 1.0) < 1e-3:
        return x.copy()
    n = max(16, int(sr * frame_ms / 1000) // 2 * 2)
    hs = n // 2  # passo di sintesi (50 % di sovrapposizione)
    ha = hs / factor  # passo di analisi
    tol = max(1, int(sr * tol_ms / 1000))
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)  # Hann periodica: somma 1
    out_len = round(x.size * factor)
    frames = int(np.ceil(out_len / hs)) + 1
    # zeri ai lati: la ricerca e l'ultima finestra non escono mai dal segnale (anche accorciando molto)
    pad_end = max(2 * n, int(frames * ha) + 2 * n + 2 * tol - x.size)
    xp = np.concatenate([np.zeros(tol, np.float32), x, np.zeros(pad_end, np.float32)])
    y = np.zeros(frames * hs + n, np.float32)
    wsum = np.zeros_like(y)
    prev = 0
    for k in range(frames):
        nominal = round(k * ha)
        if k == 0:
            pos = 0
        else:
            # la continuazione naturale della finestra precedente: è lei che la nuova deve somigliare
            natural = xp[tol + prev + hs: tol + prev + hs + n]
            region = xp[nominal: nominal + 2 * tol + n]  # posizioni nominal-tol .. nominal+tol
            pos = nominal - tol + int(np.argmax(np.correlate(region, natural, mode="valid")))
        y[k * hs: k * hs + n] += xp[tol + pos: tol + pos + n] * win
        wsum[k * hs: k * hs + n] += win
        prev = pos
    y = np.where(wsum > 1e-3, y / np.maximum(wsum, 1e-3), 0.0).astype(np.float32)
    return y[:out_len]

"""Metriche oggettive di qualità vocale (da eseguire in un venv con torch, torchaudio e librosa).

Uso: python voice_metrics.py <cartella_wav> [-o risultati.json]
- PESQ / SI-SDR / STOI stimati da SQUIM (senza riferimento), a finestre da 10 s
- F0 mediana e 10° percentile (pyin) sui tratti sonori
- banda utile (rolloff 99%) e silenzio iniziale/finale
"""

import argparse
import glob
import json
import os

import librosa
import numpy as np
import torch
import torchaudio

squim = torchaudio.pipelines.SQUIM_OBJECTIVE.get_model().eval()


def analyze(path: str) -> dict:
    y, sr = librosa.load(path, sr=16000, mono=True)
    f0, voiced, _ = librosa.pyin(y, fmin=50, fmax=300, sr=sr, frame_length=1024)
    f0 = f0[voiced & ~np.isnan(f0)]
    stoi, pesq, sisdr = [], [], []
    win = 10 * sr
    for i in range(0, max(1, len(y) - sr), win):
        seg = torch.tensor(y[i:i + win]).unsqueeze(0)
        if seg.shape[1] < 2 * sr:
            continue
        with torch.no_grad():
            s, p, d = squim(seg)
        stoi.append(s.item())
        pesq.append(p.item())
        sisdr.append(d.item())
    y_full, sr_full = librosa.load(path, sr=None, mono=True)
    roll = librosa.feature.spectral_rolloff(y=y_full, sr=sr_full, roll_percent=0.99)[0]
    rms = librosa.feature.rms(y=y_full)[0]
    active = rms > rms.max() * 0.05
    nz = np.where(np.abs(y) > 0.01)[0]
    return {
        "file": os.path.basename(path),
        "dur_s": round(len(y) / sr, 2),
        "lead_sil_ms": int(1000 * nz[0] / sr) if nz.size else None,
        "tail_sil_ms": int(1000 * (len(y) - nz[-1]) / sr) if nz.size else None,
        "f0_med": round(float(np.median(f0)), 1) if f0.size else None,
        "f0_p10": round(float(np.percentile(f0, 10)), 1) if f0.size else None,
        "f0_spread": round(float(np.percentile(f0, 90) - np.percentile(f0, 10)), 1) if f0.size else None,
        "pesq": round(float(np.mean(pesq)), 2) if pesq else None,
        "sisdr": round(float(np.mean(sisdr)), 1) if sisdr else None,
        "stoi": round(float(np.mean(stoi)), 3) if stoi else None,
        "rolloff_khz": round(float(np.median(roll[active])) / 1000, 1),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    res = [analyze(f) for f in sorted(glob.glob(os.path.join(a.folder, "*.wav")))]
    for r in res:
        print(json.dumps(r, ensure_ascii=False))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)

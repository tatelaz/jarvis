"""Gara tra modelli di impronta vocale nelle condizioni di Jarvis: italiano, frasi brevi, processore.

Voci: MLS Italiano (33 lettori, models/testdata/mls_it, estratte con prep.py). Per ogni persona l'impronta si
registra con 3 frasi intere (come quando l'utente legge qualche frase a Jarvis); le prove sono pezzi da 1, 2 e 3 s
delle altre sue frasi (bersaglio) e delle frasi degli altri 32 (estranei).
Misure: EER, e quanto spesso si respinge la persona giusta se gli estranei accettati sono al massimo 1% (FRR@FAR1%):
per Jarvis conta di più non scambiare un ospite per l'utente. Tempo: ms per un pezzo da 3 s, 4 thread.
Uso: <python di Doppio> scripts/explore/speaker_arena.py
"""

import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf
import torch
import torchaudio.compliance.kaldi as kaldi

ROOT = Path(__file__).resolve().parents[2]
VOICES = ROOT / "models" / "testdata" / "mls_it"
SPK = ROOT / "models" / "speaker"


def fbank(wav: np.ndarray, scale: float, window: str) -> np.ndarray:
    t = torch.from_numpy(wav * scale).float().unsqueeze(0)
    f = kaldi.fbank(t, num_mel_bins=80, frame_length=25, frame_shift=10, dither=0.0, sample_frequency=16000,
                    window_type=window, use_energy=False)
    return (f - f.mean(dim=0, keepdim=True)).numpy()


def session(path: Path) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


class Model:
    def __init__(self, name, path, kind):
        self.name, self.kind, self.s = name, kind, session(path)
        self.inp = self.s.get_inputs()[0].name

    def embed(self, wav: np.ndarray) -> np.ndarray:
        if self.kind == "wav":
            x = wav[None, :].astype(np.float32)
        elif self.kind == "wespeaker":
            x = fbank(wav, 32768.0, "hamming")[None]
        else:  # 3dspeaker
            x = fbank(wav, 1.0, "povey")[None]
        e = self.s.run(None, {self.inp: x})[0][0]
        return e / np.linalg.norm(e)


def eer_and_frr(target: np.ndarray, impostor: np.ndarray) -> tuple[float, float, float]:
    thr = np.sort(np.concatenate([target, impostor]))
    far = np.array([(impostor >= t).mean() for t in thr])
    frr = np.array([(target < t).mean() for t in thr])
    i = np.argmin(np.abs(far - frr))
    ok = np.where(far <= 0.01)[0]
    j = ok[0] if len(ok) else len(thr) - 1
    return 100 * (far[i] + frr[i]) / 2, 100 * frr[j], float(thr[j])


def main() -> None:
    meta = json.loads((VOICES / "voci.json").read_text())
    by: dict[str, list[np.ndarray]] = {}
    for m in meta:
        a, _ = sf.read(VOICES / "wav" / m["file"], dtype="float32")
        by.setdefault(m["speaker"], []).append(a)
    models = [Model("ReDimNet2+ (lab260, 2026)", SPK / "redimnet2-plus" / "redimnet2plus.onnx", "wav"),
              Model("WeSpeaker CAM++ LM", SPK / "wespeaker_en_voxceleb_CAM++_LM.onnx", "wespeaker"),
              Model("WeSpeaker ResNet293 LM", SPK / "wespeaker_en_voxceleb_resnet293_LM.onnx", "wespeaker"),
              Model("3D-Speaker CAM++ advanced", SPK / "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
                    "3dspeaker")]
    for model in models:
        enroll = {}
        for spk, utts in by.items():
            v = np.mean([model.embed(u[:16000 * 20]) for u in utts[:3]], axis=0)
            enroll[spk] = v / np.linalg.norm(v)
        row = []
        for dur in (1.0, 2.0, 3.0):
            n = int(16000 * dur)
            tgt, imp = [], []
            for spk, utts in by.items():
                for u in utts[3:]:
                    seg = u[8000:8000 + n]
                    e = model.embed(seg)
                    for other, v in enroll.items():
                        (tgt if other == spk else imp).append(float(e @ v))
            eer, frr, thr = eer_and_frr(np.array(tgt), np.array(imp))
            row.append(f"{dur:.0f}s EER {eer:4.1f}% FRR@1% {frr:4.1f}% (soglia {thr:.2f})")
        seg = by[next(iter(by))][3][:48000]
        model.embed(seg)
        t = time.perf_counter()
        for _ in range(10):
            model.embed(seg)
        ms = (time.perf_counter() - t) * 100
        print(f"{model.name:28s} | " + " | ".join(row) + f" | {ms:.0f} ms per 3 s", flush=True)


if __name__ == "__main__" and not __import__("sys").argv[1:]:
    main()


# ------------------------------------------------------------------ condizioni difficili (frasi da 2 s)

def degrade(seg: np.ndarray, kind: str, rng: np.random.Generator, others: list[np.ndarray]) -> np.ndarray:
    """Rovina un pezzo come in una stanza vera: chiacchiericcio/TV, eco della stanza, microfono scadente."""
    x = seg.copy()
    if kind == "tv":  # altre voci sotto (5 dB sotto) + un fruscio
        b = others[rng.integers(len(others))]
        start = rng.integers(0, max(1, len(b) - len(x)))
        b = b[start:start + len(x)]
        b = np.pad(b, (0, len(x) - len(b)))
        x = x + b * (np.std(x) / (np.std(b) + 1e-9)) * 10 ** (-5 / 20) + rng.normal(0, 0.003, len(x))
    elif kind == "stanza":  # riverbero sintetico, RT60 ~0,5 s
        n = 8000
        rir = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / n)
        rir[0] = 3.0
        x = np.convolve(x, rir / np.abs(rir).sum() * 2)[: len(x)]
    elif kind == "microfono":  # banda telefonica 300-3400 Hz + rumore + saturazione leggera
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(len(x), 1 / 16000)
        spec[(f < 300) | (f > 3400)] = 0
        x = np.tanh(3 * np.fft.irfft(spec, len(x))) / 3 + rng.normal(0, 0.002, len(x))
    return (x / (np.abs(x).max() + 1e-9) * 0.5).astype(np.float32)


def hard() -> None:
    meta = json.loads((VOICES / "voci.json").read_text())
    by: dict[str, list[np.ndarray]] = {}
    for m in meta:
        a, _ = sf.read(VOICES / "wav" / m["file"], dtype="float32")
        by.setdefault(m["speaker"], []).append(a)
    models = [Model("ReDimNet2+", SPK / "redimnet2-plus" / "redimnet2plus.onnx", "wav"),
              Model("3D-Speaker CAM++ adv", SPK / "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
                    "3dspeaker")]
    n = 32000
    for kind in ("tv", "stanza", "microfono"):
        scores = {}
        for model in models:
            rng = np.random.default_rng(7)  # stesse rovine per tutti i modelli
            enroll = {}
            for spk, utts in by.items():
                v = np.mean([model.embed(u[:16000 * 20]) for u in utts[:3]], axis=0)
                enroll[spk] = v / np.linalg.norm(v)
            tgt, imp = [], []
            for spk, utts in by.items():
                others = [u for s, us in by.items() if s != spk for u in us[:2]]
                for u in utts[3:]:
                    e = model.embed(degrade(u[8000:8000 + n], kind, rng, others))
                    for other, v in enroll.items():
                        (tgt if other == spk else imp).append(float(e @ v))
            scores[model.name] = (np.array(tgt), np.array(imp))
            eer, frr, _ = eer_and_frr(*scores[model.name])
            print(f"{kind:9s} {model.name:22s} EER {eer:4.1f}%  FRR@1% {frr:4.1f}%", flush=True)
        (t1, i1), (t2, i2) = scores.values()
        eer, frr, _ = eer_and_frr((t1 + t2) / 2, (i1 + i2) / 2)
        print(f"{kind:9s} {'insieme (media)':22s} EER {eer:4.1f}%  FRR@1% {frr:4.1f}%", flush=True)


if __name__ == "__main__" and __import__("sys").argv[1:] == ["difficile"]:
    hard()

"""Somiglianza del timbro (coseno tra impronte WeSpeaker) tra ogni clone e la sua voce di riferimento.

Da eseguire in un venv con torch + pyannote.audio. Uso: python voice_similarity.py <cloni> <riferimenti>
I file dei cloni si chiamano <riferimento>__<motore>__<n>.wav.
"""

import glob
import json
import os
import sys
import warnings

import librosa
import numpy as np
import torch

warnings.filterwarnings("ignore")
import torch._dynamo  # noqa: E402,F401  (va importato prima di pyannote)
from pyannote.audio import Inference, Model  # noqa: E402

model = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
inf = Inference(model, window="whole", device=torch.device("cuda" if torch.cuda.is_available() else "cpu"))


def emb(path: str) -> np.ndarray:
    y, _ = librosa.load(path, sr=16000, mono=True)
    e = inf({"waveform": torch.from_numpy(y[None, :].copy()), "sample_rate": 16000})
    return e / max(np.linalg.norm(e), 1e-9)


clones, refs = sys.argv[1], sys.argv[2]
ref_emb = {os.path.splitext(os.path.basename(f))[0]: emb(f) for f in glob.glob(os.path.join(refs, "*.wav"))}
out = {}
for f in sorted(glob.glob(os.path.join(clones, "*__*__*.wav"))):
    ref, eng, _ = os.path.splitext(os.path.basename(f))[0].split("__")
    out[os.path.basename(f)] = float(ref_emb[ref] @ emb(f))
print(json.dumps(out, indent=1))
with open(os.path.join(clones, "similarity.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1)

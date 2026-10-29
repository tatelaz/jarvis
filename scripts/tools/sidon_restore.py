"""Restauro vocale con Sidon v0.1 (sarulab-speech), stesso codice della demo ufficiale.
Uso: python sidon_restore.py ingresso.wav uscita.wav   (serve un venv con torch, torchaudio, transformers)"""
import sys

import numpy as np
import soundfile as sf
import torch
import torchaudio
import transformers
from huggingface_hub import hf_hub_download

src, dst = sys.argv[1], sys.argv[2]
dev = "cuda" if torch.cuda.is_available() else "cpu"
fe = torch.jit.load(hf_hub_download("sarulab-speech/sidon-v0.1", f"feature_extractor_{dev}.pt"), map_location=dev).to(dev)
dec = torch.jit.load(hf_hub_download("sarulab-speech/sidon-v0.1", f"decoder_{dev}.pt"), map_location=dev).to(dev)
pre = transformers.SeamlessM4TFeatureExtractor.from_pretrained("facebook/w2v-bert-2.0")
w, sr = sf.read(src, dtype="float32")
if w.ndim > 1:
    w = w.mean(1)
w = 0.9 * w / np.abs(w).max()
n_out = int(48000 / sr * len(w))
x = torchaudio.functional.highpass_biquad(torch.tensor(w).view(1, -1), sr, 50)
x16 = torch.nn.functional.pad(torchaudio.functional.resample(x, sr, 16000), (0, 24000))
outs, cache = [], None
with torch.inference_mode():
    for chunk in x16.view(-1).split(16000 * 96):
        inp = pre(torch.nn.functional.pad(chunk, (160, 160)), return_tensors="pt")
        feat = fe(inp["input_features"].to(dev))["last_hidden_state"]
        if cache is not None:
            feat = torch.cat([cache, feat], dim=1)
        outs.append(dec(feat.transpose(1, 2)).view(-1)[:-960])
        cache = feat[:, -1:]
y = torch.cat(outs).cpu().numpy()[:n_out]
sf.write(dst, y, 48000)
print("ok", dst, round(len(y) / 48000, 2), "s")

"""Converte ReDimNet2+ (lab260/redimnet2-plus, Apache 2.0) in ONNX: audio 16 kHz grezzo -> impronta vocale (192, L2).

Gira nel venv di Doppio (torch + torchaudio + onnx), una volta sola; Jarvis usa solo il file .onnx con onnxruntime.
Il frontend è quello interno del modello (TFMelBanks, 72 bande), non quello della scheda HuggingFace (64 bande).
Uso: <python di Doppio> scripts/explore/export_redimnet.py
"""

import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "models" / "speaker" / "redimnet2-plus"
OUT = SRC / "redimnet2plus.onnx"
sys.path.insert(0, str(SRC))

from asv.models.redimnet2 import ReDimNet2Encoder  # noqa: E402


class Full(torch.nn.Module):
    def __init__(self, enc: ReDimNet2Encoder) -> None:
        super().__init__()
        self.enc = enc

    def forward(self, wav: torch.Tensor) -> torch.Tensor:  # [B, N] float32 a 16 kHz
        spec = self.enc.spec_frontend(wav)
        emb = self.enc(spec)
        return torch.nn.functional.normalize(emb, dim=1)


def main() -> None:
    enc = ReDimNet2Encoder(model_name="b6", pretrained=False, train_type="lm", dataset="vox2",
                           model_overrides={"out_channels": 224, "return_2d_output": True})  # la scheda dice False: i pesi dicono True
    state = torch.load(SRC / "pytorch_model_fsdp.bin", map_location="cpu", weights_only=True)  # niente pickle eseguibile
    state = {k.replace("_orig_mod.", "").replace("module.", ""): v for k, v in state.items()}
    enc_state = {k.removeprefix("encoder."): v for k, v in state.items() if k.startswith("encoder.")}
    rest = sorted({k.split(".")[0] for k in state if not k.startswith("encoder.")})
    print("chiavi fuori dall'encoder (non servono per l'impronta):", rest)
    res = enc.load_state_dict(enc_state, strict=True)
    print("caricamento:", res)
    full = Full(enc).eval()
    n_params = sum(p.numel() for p in enc.parameters())
    print(f"parametri: {n_params / 1e6:.1f} M")

    wav = torch.randn(1, 16000 * 3) * 0.05
    with torch.no_grad():
        ref = full(wav).numpy()
    torch.onnx.export(full, (wav,), str(OUT), input_names=["wav"], output_names=["embedding"],
                      dynamic_axes={"wav": {0: "batch", 1: "samples"}, "embedding": {0: "batch"}},
                      opset_version=17, dynamo=False)
    import onnxruntime as ort

    sess = ort.InferenceSession(str(OUT), providers=["CPUExecutionProvider"])
    for secs in (1.0, 3.0, 7.3):
        w = (torch.randn(1, int(16000 * secs)) * 0.05)
        with torch.no_grad():
            a = full(w).numpy()
        b = sess.run(None, {"wav": w.numpy()})[0]
        print(f"{secs} s: torch vs onnx max diff {np.abs(a - b).max():.2e}, coseno {float((a * b).sum()):.6f}")
    print("onnx (3 s):", np.abs(sess.run(None, {"wav": wav.numpy()})[0] - ref).max())
    print("salvato:", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

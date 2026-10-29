"""Clona le voci finaliste con i motori veloci e misura latenza, RTF e qualità su frasi tipiche di Jarvis."""

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech.tts import TtsClient, Voice  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CAND = ROOT / "data/voices/candidates"
OUT = ROOT / "data/bench/clones"
OUT.mkdir(parents=True, exist_ok=True)
REF_TEXT = json.loads((ROOT / "data/bench/casting/manifest.json").read_text(encoding="utf-8"))["text"]

LINES = [
    "Subito, signore.",
    "Certamente. Me ne occupo io.",
    "Ho trovato tre voli per Londra domani mattina; il più conveniente parte alle sette e quaranta.",
    "Temo che il server non risponda da circa dieci minuti. Vuole che provi a riavviarlo?",
    "Fatto. Il documento è sulla sua scrivania, e mi sono permesso di correggere due refusi.",
    "Se posso permettermi, signore, sono le tre del mattino: forse sarebbe il caso di riposare.",
]
ENGINES = {
    "omni": ("omnivoice", {}),
    "omni16": ("omnivoice", {"num_inference_steps": 16}),
    "qwen": ("qwen3-base", {}),
    "pocket": ("pocket", {}),
}
if len(sys.argv) > 1:
    ENGINES = {k: v for k, v in ENGINES.items() if k in sys.argv[1].split(",")}


async def main() -> None:
    c = TtsClient("127.0.0.1", 8790)
    rows = []
    manifest = {"items": []}
    from jarvis.speech.http import request_json
    for eng, (model, opts) in ENGINES.items():
        await request_json("127.0.0.1", 8790, "POST", "/v1/tasks/unload_all_models")
        for ref in sorted(CAND.glob("*.wav")):
            voice = Voice(ref_wav=ref, ref_text=REF_TEXT, language="Italian" if model.startswith("qwen") else (None if model == "pocket" else "it"))
            await c.synthesize(model, "Prova.", voice, options=opts or None, seed=7)  # riscaldamento
            for i, line in enumerate(LINES):
                t = time.perf_counter()
                audio, sr = await c.synthesize(model, line, voice, options=opts or None, seed=7, timeout=120)
                dt = time.perf_counter() - t
                name = f"{ref.stem}__{eng}__{i}.wav"
                sf.write(OUT / name, audio, sr)
                manifest["items"].append({"file": name, "text": line})
                rows.append((ref.stem, eng, i, dt, len(audio) / sr))
                print(f"{ref.stem:24s} {eng:7s} frase {i}: {dt * 1000:6.0f} ms per {len(audio) / sr:5.2f} s", flush=True)
    old = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))["items"] if (OUT / "manifest.json").exists() else []
    names = {it["file"] for it in manifest["items"]}
    manifest["items"] += [it for it in old if it["file"] not in names]
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nRiepilogo latenza (frasi brevi 0-1 / lunghe 2-5) e RTF medio:")
    for eng in ENGINES:
        short = [dt for (_, e, i, dt, _) in rows if e == eng and i < 2]
        long = [dt for (_, e, i, dt, _) in rows if e == eng and i >= 2]
        rtf = [dt / d for (_, e, _, dt, d) in rows if e == eng]
        print(f"  {eng:7s} brevi {1000 * np.median(short):5.0f} ms   lunghe {1000 * np.median(long):5.0f} ms   RTF {np.mean(rtf):.2f}")


asyncio.run(main())

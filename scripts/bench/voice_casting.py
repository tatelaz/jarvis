"""Casting della voce di Jarvis: genera voci originali da descrizione (voice design) e le salva.

Uso: python scripts/bench/voice_casting.py <cartella_uscita>
Le metriche si calcolano poi con voice_metrics.py (torch/torchaudio, venv separato).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jarvis.speech.tts import TtsClient, Voice  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/bench/casting")
OUT.mkdir(parents=True, exist_ok=True)

REF_TEXT = (
    "Buonasera, signore. I sistemi sono operativi e tutto procede secondo i piani. "
    "Ho preparato il resoconto della giornata: quando desidera, posso illustrarglielo."
)

OMNI = {
    "omni_mid_low": "male, middle-aged, low pitch",
    "omni_mid_vlow": "male, middle-aged, very low pitch",
    "omni_mid_mod": "male, middle-aged, moderate pitch",
    "omni_eld_low": "male, elderly, low pitch",
    "omni_ya_low": "male, young adult, low pitch",
    "omni_mid_low_brit": "male, middle-aged, low pitch, british accent",
}
QWEN = {
    "qw_butler": "A refined, composed middle-aged man with a deep, smooth baritone voice. Calm, precise and "
                 "quietly confident, he speaks at a measured pace with impeccable diction and a hint of dry wit.",
    "qw_warm": "A warm, velvety male baritone in his forties. Polished and articulate, gentle but authoritative, "
               "like a trusted personal assistant; relaxed tempo, clear studio-quality recording.",
    "qw_crisp": "A crisp, elegant male voice, mid-low pitch, cultivated and serene. Speaks with understated "
                "politeness and effortless clarity, never rushed. Clean, close-microphone recording.",
}
SEEDS = [11, 23, 47]


async def main() -> None:
    c = TtsClient("127.0.0.1", 8790)
    manifest = []
    jobs = [(n, "omnivoice", Voice(instruct=d, language="it")) for n, d in OMNI.items()]
    jobs += [(n, "qwen3-design", Voice(instruct=d, language="Italian")) for n, d in QWEN.items()]
    for name, model, voice in jobs:
        for seed in SEEDS:
            t = time.perf_counter()
            audio, sr = await c.synthesize(model, REF_TEXT, voice, seed=seed, timeout=180)
            dt = time.perf_counter() - t
            f = OUT / f"{name}_s{seed}.wav"
            sf.write(f, audio, sr)
            manifest.append({"file": f.name, "model": model, "instruct": voice.instruct, "seed": seed,
                             "secs": round(len(audio) / sr, 2), "gen_s": round(dt, 2)})
            print(f"{f.name:28s} {len(audio) / sr:5.2f}s audio in {dt:5.2f}s", flush=True)
    (OUT / "manifest.json").write_text(json.dumps({"text": REF_TEXT, "items": manifest}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")


asyncio.run(main())

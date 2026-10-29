"""La voce di Jarvis con una versione del motore audio.cpp: VRAM, tempo per frase, durata, audio salvato.

La versione si sceglie con JARVIS_ENGINE_DIR (cartella che contiene audiocpp/). Stesse frasi e stesso seme per tutte,
così due versioni si confrontano davvero; gli audio vanno poi riascoltati/trascritti (non basta che "suonino").
Uso: set JARVIS_ENGINE_DIR=...  &&  python scripts/bench/tts_engine_bench.py <etichetta> [ripetizioni] [opzioni json]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bench"))

from tts_arena_bench import PHRASES  # noqa: E402
from vram_bench import vram_mb  # noqa: E402

from jarvis import paths  # noqa: E402
from jarvis.speech import engine  # noqa: E402
from jarvis.speech.tts import TtsClient, Voice  # noqa: E402


async def main(label: str, repeats: int, session: dict) -> dict:
    voice = engine.load_voice("jarvis")
    spec = engine.voice_spec(8797, voice, "qwen3-1.7b")
    if session:
        spec.models[0]["session_options"] = session
    out_dir = ROOT / "data" / "bench" / f"motore_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    before = vram_mb()
    eng = engine.EngineProcess(spec)
    t = time.perf_counter()
    await eng.start(timeout=120)
    tts = TtsClient(eng.host, eng.port)
    v = Voice(preset="jarvis", language="Italian")
    try:
        await tts.synthesize("tts", PHRASES[0], v, seed=7)  # caricamento e riscaldamento
        load_s = time.perf_counter() - t
        peak = vram_mb() - before
        times: dict[int, list[float]] = {i: [] for i in range(len(PHRASES))}
        durs = {}
        for r in range(repeats):
            for i, p in enumerate(PHRASES):
                t = time.perf_counter()
                audio, sr = await tts.synthesize("tts", p, v, seed=7)
                times[i].append(round(time.perf_counter() - t, 3))
                durs[i] = round(len(audio) / sr, 2)
                peak = max(peak, vram_mb() - before)
                if r == 0:
                    sf.write(out_dir / f"frase_{i}.wav", audio, sr)
        return {"motore": label, "server": str(paths.AUDIOCPP_SERVER), "carico_s": round(load_s, 1),
                "vram_mb": peak, "tempi_s": times, "durate_s": durs}
    finally:
        await eng.stop()


if __name__ == "__main__":
    lab = sys.argv[1]
    rep = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    opts = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(json.dumps(asyncio.run(main(lab, rep, opts)), ensure_ascii=False))

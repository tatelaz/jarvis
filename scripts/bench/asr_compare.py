"""Confronta due modelli di trascrizione nelle condizioni di Jarvis (processore, 6 thread, stesse arene di memoria).

Avvia un motore audio.cpp a parte con i due modelli, li alterna file per file sul set FLEURS italiano (così il carico
del PC pesa uguale su entrambi) e prova anche silenzio e rumore (un buon modello non inventa parole).
Uso: python scripts/bench/asr_compare.py <modello_a.gguf> <modello_b.gguf> [porta] [cpu|cuda]
Con Jarvis SPENTO: due modelli caricati prenotano molta memoria (26/10: col Jarvis acceso è caduto tutto).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bench"))

from asr_bench import DATA, wer  # noqa: E402

from jarvis import paths  # noqa: E402
from jarvis.speech.engine import PARAKEET_SESSION  # noqa: E402
from jarvis.speech.http import request_json  # noqa: E402
from jarvis.speech.stt import transcribe_file  # noqa: E402


async def wait_ready(port: int, proc: subprocess.Popen, timeout: float = 120) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise SystemExit(f"il motore si è chiuso (codice {proc.returncode})")
        try:
            await request_json("127.0.0.1", port, "GET", "/v1/models", timeout=2)
            return
        except (OSError, TimeoutError):
            await asyncio.sleep(0.5)
    raise SystemExit("il motore non risponde")


async def main(a: Path, b: Path, port: int, backend: str = "cpu") -> None:
    models = {"A": a, "B": b}
    cfg = {"host": "127.0.0.1", "port": port, "backend": backend, "threads": 6, "lazy_load": False,
           "busy_timeout_ms": 60000,
           "models": [{"id": k, "family": "parakeet_tdt", "path": p.as_posix(), "task": "asr", "mode": "offline",
                       "session_options": PARAKEET_SESSION} for k, p in models.items()]}
    cfg_path = ROOT / "data" / "engine" / "confronto_asr.json"
    cfg_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    log = (ROOT / "data" / "logs" / "engine_confronto.log").open("wb")
    proc = subprocess.Popen([str(paths.AUDIOCPP_SERVER), "--config", str(cfg_path), "--no-ui"], stdout=log,
                            stderr=log, cwd=str(paths.AUDIOCPP_DIR), creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        await wait_ready(port, proc)
        meta = json.load(open(DATA / "meta.json", encoding="utf-8"))
        res = {k: {"err": 0, "words": 0, "lat": [], "diff": []} for k in models}
        for i, item in enumerate(meta):
            audio, _ = sf.read(DATA / item["file"], dtype="float32")
            order = ("A", "B") if i % 2 == 0 else ("B", "A")
            out = {}
            for k in order:
                t = time.perf_counter()
                r = await transcribe_file("127.0.0.1", port, k, audio, timeout=60)
                res[k]["lat"].append(time.perf_counter() - t)
                out[k] = r.get("text", "")
                e, n = wer(item["text"], out[k])
                res[k]["err"] += e
                res[k]["words"] += n
            if out["A"] != out["B"]:
                res["A"]["diff"].append((item["text"], out["A"], out["B"]))
        rng = np.random.default_rng(0)
        extra = {"silenzio": np.zeros(3 * 16000, np.float32),
                 "rumore": (0.02 * rng.standard_normal(3 * 16000)).astype(np.float32)}
        for k in models:
            r = res[k]
            print(f"{k} {models[k].name}: WER {100 * r['err'] / r['words']:.2f}% su {len(meta)} frasi, "
                  f"latenza mediana {1000 * np.median(r['lat'][1:]):.0f} ms (p90 {1000 * np.percentile(r['lat'], 90):.0f})")
            for name, audio in extra.items():
                txt = (await transcribe_file("127.0.0.1", port, k, audio, timeout=60)).get("text", "")
                print(f"   {name}: {txt!r}")
        print(f"\nfrasi trascritte diversamente: {len(res['A']['diff'])}; le prime 8:")
        for ref, ta, tb in res["A"]["diff"][:8]:
            print(f" RIF {ref}\n  A  {ta}\n  B  {tb}")
    finally:
        proc.terminate()
        proc.wait(10)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 8795,
                     sys.argv[4] if len(sys.argv) > 4 else "cpu"))

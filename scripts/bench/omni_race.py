"""Gara di misura dei modelli per musica e suoni (jarvis/media/omni.py). DA FARE CON JARVIS SPENTO.

Ogni candidato si carica da solo sulla scheda video (come farebbe Jarvis dopo aver scaricato la voce), ascolta le
stesse clip e si spegne. Si misurano: tempo di caricamento, memoria video (picco, letto da nvidia-smi ogni 0,5 s),
secondi per ogni minuto di audio, e quante parole attese compaiono nella risposta (le scrive l'utente in attese.json).
Il giudizio vero lo dà l'utente leggendo le risposte una accanto all'altra nel rapporto.

Preparazione (una volta):
  1. scaricare i modelli:  python scripts/bench/omni_race.py --scarica   (usa `hf download`; ~14 GB in tutto)
  2. per Qwen2.5-Omni e LFM3-Omni serve llama.cpp per Windows con CUDA, versione del 27/10/2026 o più nuova
     (supporto di LFM3-Omni, PR ggml-org/llama.cpp#31257): lo zip "bin-win-cuda-x64" e quello "cudart" della
     release, estratti in engine/llamacpp (deve esserci engine/llamacpp/llama-server.exe)
  3. le clip in data/prove/omni_clips (wav, mp3, m4a, mp4...), 20-60 s l'una: musica di generi diversi, suoni
     (pioggia, applausi, cane, traffico, sirena), azioni in video (cucinare, sport, una caduta), parlato con
     musica sotto; in attese.json le parole inglesi che una buona risposta deve contenere, es.
     {"pioggia.wav": ["rain"], "concerto.mp4": ["guitar", "crowd"]}; con | le alternative ("clap|applau") e basta
     l'inizio della parola. Le clip di prova del 29/10 (fonti e licenze in FONTI.md accanto) le prepara
     scripts/bench/omni_clips.py.
Uso: python scripts/bench/omni_race.py [--modelli audio-flamingo-next,lfm3-omni-2b] [--finto]
     --finto prova il percorso (clip, rapporto) con un modello finto, senza scheda video: si può fare anche a
     Jarvis acceso.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from jarvis import paths  # noqa: E402
from jarvis.media import omni as O  # noqa: E402
from jarvis.media import watch as W  # noqa: E402
from jarvis.media.fetch import MEDIA_EXT  # noqa: E402

CLIPS = paths.DATA_DIR / "prove" / "omni_clips"
OUT = paths.DATA_DIR / "logs" / "omni_race"


class VramWatch:
    """Memoria video usata, letta da nvidia-smi ogni mezzo secondo in un thread: il picco durante il lavoro."""

    def __init__(self) -> None:
        self.base = self.read()
        self.peak = self.base  # anche una prova brevissima (mai campionata) ha almeno la memoria di partenza
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def read() -> int:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip().splitlines()[0])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return 0

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self.peak = max(self.peak, self.read())

    def __enter__(self) -> VramWatch:
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join(2)


class FakeBackend:
    """Per --finto: risponde subito, senza scheda video."""

    def __init__(self, model: O.OmniModel) -> None:
        self.model = model

    async def start(self) -> None:
        await asyncio.sleep(0.01)

    async def ask(self, audio, frames, prompt) -> str:
        return f"fake answer ({len(audio) / W.SR:.0f} s, {len(frames)} frames): rain, guitar"

    async def stop(self) -> None:
        pass


def found(word: str, text: str) -> bool:
    """Una parola attesa, con le alternative separate da | ("clap|applau"): basta che ne compaia una."""
    low = text.lower()
    return any(w.strip().lower() in low for w in word.split("|") if w.strip())


def download(keys: list[str]) -> None:
    hf = shutil.which("hf")
    for k in keys:
        m = O.MODELS[k]
        for rel, target in zip(m.files, m.paths(), strict=True):
            if target.is_file():
                print(f"già presente: {target}")
                continue
            cmd = ["hf", "download", m.repo, Path(rel).name, "--local-dir", str(target.parent)]
            print(" ".join(cmd))
            if hf is None:
                print("  (manca il comando hf: installare huggingface_hub, poi rilanciare)")
                continue
            subprocess.run([hf, *cmd[1:]], check=True)


async def load_clip(path: Path) -> tuple[np.ndarray, list[W.Frame], float]:
    p = W.probe(path)
    end = min(p.duration or 60.0, 120.0)
    audio = await asyncio.to_thread(W.load_audio, path, 0.0, end)
    frames: list[W.Frame] = []
    if p.has_video:
        samples = await asyncio.to_thread(W.scan_video, path, 0.0, end, 768)
        frames = [W.Frame(samples[i].t, samples[i].jpeg, samples[i].width, samples[i].height, s)
                  for i, s in W.pick(samples, 8)]
    return audio, frames, end


async def run_model(key: str, clips: list[Path], expected: dict[str, list[str]], fake: bool) -> dict:
    m = O.MODELS[key]
    if not fake and (missing := m.missing()):
        return {"modello": key, "errore": f"non scaricato: manca {missing[0]}"}
    be = FakeBackend(m) if fake else O.default_backend(m)
    row: dict = {"modello": key, "titolo": m.title, "clip": []}
    with VramWatch() as vram:
        t = time.perf_counter()
        try:
            await be.start()
            if not fake:  # il primo ascolto carica il modello (caricamento pigro): si misura a parte
                await be.ask(np.zeros(W.SR, np.float32) + 0.01, [], "Describe this audio.")
            row["caricamento_s"] = round(time.perf_counter() - t, 1)
            for c in clips:
                audio, frames, length = await load_clip(c)
                t1 = time.perf_counter()
                answers = []
                for a, b, piece in O.windows(audio, 0.0):
                    near = [f for f in frames if a <= f.t < b][:O.FRAMES_PER_WINDOW] if m.sees else []
                    answers.append(await be.ask(piece, near, O.PROMPT + (O.PROMPT_FRAMES if near else "")))
                spent = time.perf_counter() - t1
                text = " ".join(answers)
                words = expected.get(c.name, [])
                hit = [w for w in words if found(w, text)]
                row["clip"].append({"file": c.name, "durata_s": round(length, 1), "secondi": round(spent, 1),
                                    "s_per_minuto": round(spent / max(length / 60, 1e-6), 1),
                                    "parole_trovate": f"{len(hit)}/{len(words)}", "risposta": text})
        except Exception as e:  # un candidato che non parte non ferma la gara
            row["errore"] = f"{type(e).__name__}: {e}"
        finally:
            await be.stop()
    row["vram_picco_mb"] = vram.peak
    row["vram_prima_mb"] = vram.base
    return row


def report(rows: list[dict]) -> str:
    lines = ["# Gara dei modelli per musica e suoni", "", f"{time.strftime('%d/%m/%Y %H:%M')}", ""]
    lines += ["| Modello | Caricamento | VRAM picco | s per minuto (media) | Parole attese |", "|---|---|---|---|---|"]
    for r in rows:
        clips = r.get("clip") or []
        per_min = sum(c["s_per_minuto"] for c in clips) / len(clips) if clips else 0
        found = sum(int(c["parole_trovate"].split("/")[0]) for c in clips)
        total = sum(int(c["parole_trovate"].split("/")[1]) for c in clips)
        lines.append(f"| {r['modello']} | {r.get('caricamento_s', '-')} s | {r.get('vram_picco_mb', 0)} MB | "
                     f"{per_min:.1f} | {found}/{total} |" + (f" ERRORE: {r['errore']}" if r.get("errore") else ""))
    lines.append("")
    names = sorted({c["file"] for r in rows for c in r.get("clip") or []})
    for n in names:
        lines += [f"## {n}", ""]
        for r in rows:
            c = next((c for c in r.get("clip") or [] if c["file"] == n), None)
            if c:
                lines.append(f"- **{r['modello']}** ({c['secondi']} s, {c['parole_trovate']}): {c['risposta']}")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelli", default=",".join(O.MODELS))
    ap.add_argument("--clip", default=str(CLIPS))
    ap.add_argument("--scarica", action="store_true")
    ap.add_argument("--finto", action="store_true")
    a = ap.parse_args()
    keys = [k.strip() for k in a.modelli.split(",") if k.strip()]
    if unknown := [k for k in keys if k not in O.MODELS]:
        print(f"modelli sconosciuti: {unknown} (ci sono: {', '.join(O.MODELS)})")
        return 2
    if a.scarica:
        download(keys)
        return 0
    if not a.finto:
        from jarvis import single

        if not single.acquire(single.APP):  # la voce di Jarvis occupa la scheda video: la gara la falserebbe
            print("Jarvis è acceso: per la gara va spento (\"Jarvis, spegniti\").")
            return 1
    folder = Path(a.clip)
    clips = sorted(p for p in await asyncio.to_thread(lambda: list(folder.glob("*"))) if p.suffix.lower() in MEDIA_EXT)
    if not clips:
        print(f"nessuna clip in {folder}")
        return 1
    wanted = folder / "attese.json"  # utf-8-sig: il Blocco note e PowerShell mettono il BOM
    expected = json.loads(wanted.read_text(encoding="utf-8-sig")) if wanted.is_file() else {}
    rows = [await run_model(k, clips, expected, a.finto) for k in keys]
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + ("_finto" if a.finto else "")
    (OUT / f"gara_{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"gara_{stamp}.md").write_text(report(rows), encoding="utf-8")
    print(report(rows))
    print(f"\nrapporto in {OUT / f'gara_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

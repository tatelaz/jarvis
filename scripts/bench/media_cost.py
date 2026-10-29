"""Quanto costa a Jarvis guardare un video: secondi (scaricamento, fotogrammi, audio) e token per Claude.

Uso: python scripts/bench/media_cost.py URL_O_FILE [...] [--fotogrammi 20] [--px 768]

Non usa le orecchie né la scheda video (Jarvis può restare acceso): il parlato si stima dai sottotitoli automatici
di YouTube (stessa lunghezza del testo che darebbero le orecchie) e il tempo delle orecchie dalle misure già fatte
(parakeet-ultra sulla scheda video: 10 s di parlato in 41 ms; sul processore ~0,25 s per secondo).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.media import fetch  # noqa: E402
from jarvis.media import watch as W  # noqa: E402

GPU_S_PER_AUDIO_S = 0.041 / 10  # misurato il 26/10 (scripts/bench/ears_gpu_bench.py)
CPU_S_PER_AUDIO_S = 0.25  # misurato il 26/10 (trascrizione sul processore)


def captions(url: str, dest: Path) -> list[tuple[float, str]]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "writeautomaticsub": True,
            "writesubtitles": True, "subtitleslangs": ["it"], "subtitlesformat": "json3",
            "outtmpl": str(dest / "sub.%(ext)s"), "js_runtimes": {"deno": {}, "node": {}}}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:  # YouTube a volte risponde 429 ai sottotitoli
        print(f"sottotitoli non disponibili: {e}", file=sys.stderr)
        return []
    files = sorted(dest.glob("sub*.json3"))
    if not files:
        return []
    data = json.loads(files[0].read_text(encoding="utf-8"))
    out = []
    for ev in data.get("events") or []:
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if text:
            out.append((ev.get("tStartMs", 0) / 1000, text))
    return out


def by_chunks(caps: list[tuple[float, str]], start: float, end: float) -> list[W.Segment]:
    """I sottotitoli raggruppati a pezzi di 30 s, come i pezzi che vanno alle orecchie."""
    segs: dict[int, list[str]] = {}
    for t, text in caps:
        if start <= t < end:
            segs.setdefault(int((t - start) // 30), []).append(text)
    return [W.Segment(start + 30 * k, start + 30 * (k + 1), " ".join(v)) for k, v in sorted(segs.items())]


async def measure(source: str, frames: int, px: int) -> dict:
    limits = W.Limits(frames=frames, frame_px=px)
    with tempfile.TemporaryDirectory(prefix="jarvis_bench_") as tmp:
        t = time.perf_counter()
        if fetch.is_url(source):
            media = fetch.download(source, Path(tmp), frame_px=px)
            caps = captions(source, Path(tmp))
        else:
            p = Path(source)
            media, caps = fetch.Media(p.name, p, p, "file"), []
        downloaded = time.perf_counter() - t

        async def no_ears(_audio):
            return ""

        w, pcm = await W.watch(media.video, media.audio, no_ears, limits, media.name, keep_audio=True)
        if caps:
            w.transcript = by_chunks(caps, w.start, w.end)
        audio_s = len(pcm) / W.SR if pcm is not None else 0.0
        out = w.output(limits.transcript_chars)
        img_tokens = sum(f.tokens() for f in w.frames)
        return {
            "sorgente": media.name, "durata_s": round(w.duration, 1),
            "scaricamento_s": round(downloaded, 1),
            "fotogrammi": len(w.frames), "lato_px": f"{w.frames[0].width}x{w.frames[0].height}" if w.frames else "-",
            "fotogrammi_s": round(w.timings.get("frames", 0), 2), "audio_s": round(w.timings.get("audio", 0), 2),
            "orecchie_gpu_s_stima": round(audio_s * GPU_S_PER_AUDIO_S + 0.03 * len(W.chunks(pcm)), 1)
            if pcm is not None else 0,
            "orecchie_cpu_s_stima": round(audio_s * CPU_S_PER_AUDIO_S, 0),
            "token_immagini": img_tokens, "caratteri_testo": len(out.text),
            "token_testo_stima": round(len(out.text) / 3.5), "token_totali_stima": w.tokens(limits.transcript_chars),
            "jpeg_kb": round(sum(len(f.jpeg) for f in w.frames) / 1024),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sorgenti", nargs="+")
    ap.add_argument("--fotogrammi", type=int, default=20)
    ap.add_argument("--px", type=int, default=768)
    a = ap.parse_args()
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    logging.getLogger("jarvis.media").setLevel(logging.INFO)
    for s in a.sorgenti:
        print(json.dumps(asyncio.run(measure(s, a.fotogrammi, a.px)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

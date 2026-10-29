"""Prova completa di Jarvis senza microfono: una voce sintetica gli parla in una stanza simulata.

Tutto il resto è vero: motori vocali, rilevamento dei turni, trascrizione, Claude, sintesi con la
voce di Jarvis, riproduzione. Alla fine si trascrive ciò che Jarvis ha detto e si misurano i tempi.

Uso: python scripts/e2e.py [--echo 0.4] "Jarvis, quanto fa diciassette per ventitré?" ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import soxr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import log
from jarvis.app import Jarvis
from jarvis.audio.devices import SimulatedRoom
from jarvis.speech.stt import transcribe_file
from jarvis.speech.tts import TtsClient, Voice

ROOT = Path(__file__).resolve().parents[1]
OUT_WAV = ROOT / "data" / "bench" / "e2e_uscita.wav"
REPORTS = ROOT / "data" / "logs" / "e2e"  # un rapporto completo per ogni prova, anche quando fallisce


def long_pauses(audio16: np.ndarray, min_s: float = 0.6, between_answers_s: float = 3.0) -> list[float]:
    """Silenzi lunghi DENTRO le risposte (i buchi che si sentono a metà frase), in secondi.

    Le pause oltre between_answers_s sono lo spazio tra una risposta e l'altra e non contano."""
    frames = audio16[: audio16.size // 160 * 160].reshape(-1, 160)  # 10 ms
    silent = np.sqrt(np.mean(frames ** 2, axis=1)) < 0.005
    voiced = np.where(~silent)[0]
    if voiced.size == 0:
        return []
    out, run = [], 0
    for s in silent[voiced[0]: voiced[-1] + 1]:
        if s:
            run += 1
            continue
        if min_s <= run / 100 < between_answers_s:
            out.append(round(run / 100, 2))
        run = 0
    return out


async def user_voices(questions: list[str]) -> list[np.ndarray]:
    """Genera la voce dell'utente PRIMA di avviare Jarvis, con un motore temporaneo (voce diversa)."""
    from jarvis.speech import engine

    spec = engine.EngineSpec("utente_test", 8799, [{"id": "u", "family": "supertonic", "task": "tts", "mode": "offline",
                                                   "path": engine.model_path("supertonic")}])
    eng = engine.EngineProcess(spec)
    await eng.start()
    try:
        tts = TtsClient(eng.host, eng.port)
        out = []
        for text in questions:
            audio, sr = await tts.synthesize("u", text, Voice(preset="M2", language="it"), seed=3)
            out.append(soxr.resample(audio, sr, 16000))
        return out
    finally:
        await eng.stop()


async def main(questions: list[str], echo: float, gap: float, provider: str | None = None) -> dict:
    from jarvis import single

    if not single.acquire(single.APP):  # stesse porte e la VRAM non basta per due voci
        raise RuntimeError("Jarvis è acceso: per la prova completa va spento (\"Jarvis, spegniti\")")
    try:
        voices = await user_voices(questions)
        room = SimulatedRoom(echo_gain=echo, speed=1.0)
        app = Jarvis(backend=room)
        if provider:  # quale cervello mettere in testa per questa prova (non viene salvato)
            app.cfg.brain.provider = provider
        events: list[dict] = []
        try:
            await app.start()
            report = await _run(app, room, questions, voices, events, gap)
        except BaseException as e:
            _save({"domande": questions, "eccezione": repr(e)}, events)
            raise
        finally:
            await app.stop()
    finally:
        single.release(single.APP)  # la prova dopo (nello stesso processo) deve poter ripartire
    _save(report, events)
    return report


def _save(report: dict, events: list[dict]) -> None:
    import time as _time

    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / (_time.strftime("%Y%m%d_%H%M%S") + ".json")
    path.write_text(json.dumps({"rapporto": report, "eventi": events}, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    report["file_rapporto"] = str(path)


async def _run(app: Jarvis, room: SimulatedRoom, questions: list[str], voices: list[np.ndarray],
               events: list[dict], gap: float) -> dict:
    q = app.bus.subscribe(5000)

    async def collect():
        while True:
            ev = await q.get()
            if ev["type"] != "level":
                events.append(ev)

    collector = asyncio.ensure_future(collect())
    import time as _time
    t_start, room_t0 = _time.time() - room.now, 0.0
    t = room.now + 2.0
    plan = []
    for text, a16 in zip(questions, voices, strict=True):
        room.say(t, a16, 16000, gain=0.6)
        plan.append((t, text))
        t += len(a16) / 16000 + gap
    end = t + 6
    await asyncio.sleep(max(0.0, end - room.now) + 1)  # la stanza va in tempo reale
    collector.cancel()
    out = np.concatenate(room.output_log)
    out16 = soxr.resample(out, room.out_rate, 16000)
    import soundfile as sf
    sf.write(str(OUT_WAV), out16, 16000)
    voiced = np.where(np.abs(out16) > 0.01)[0]
    heard = {"text": ""}
    if voiced.size:  # solo il tratto in cui Jarvis parla (Parakeet non ama decine di secondi di silenzio)
        seg = out16[max(0, voiced[0] - 1600): voiced[-1] + 1600]
        heard = await transcribe_file(app.ears.host, app.ears.port, "stt", seg)
    report = {
        "domande": [p[1] for p in plan],
        "trascritto_utente": [e["text"] for e in events if e["type"] == "user"],
        "risposte_testo": [e["text"] for e in events if e["type"] == "jarvis_done"],
        "jarvis_sentito": heard["text"],
        "prima_voce_ms": [round(e["latency_ms"]) for e in events if e["type"] == "first_audio"],
        "metriche_turni": list(app.conv.metrics),
        "errori": [e for e in events if e["type"] == "error"],
        "strumenti": [e["name"] for e in events if e["type"] == "tool"],
        "aec_attivo": app.pipeline.aec_active,
        "conteggio_eventi": {k: sum(1 for e in events if e["type"] == k) for k in sorted({e["type"] for e in events})},
        "ignorati": [(e.get("text"), e.get("reason")) for e in events if e["type"] == "ignored"],
        "stati": [e.get("state") for e in events if e["type"] == "state"],
        "pause_lunghe_s": long_pauses(out16),
        "uscita": {"secondi_totali": round(out16.size / 16000, 1), "secondi_con_voce": round(voiced.size / 16000, 2),
                   "picco": round(float(np.abs(out16).max()), 3)},
        "cronologia_s": [(round(e["ts"] - t_start, 2), e["type"], (e.get("state") or e.get("text") or "")[:40])
                         for e in events if e["type"] in ("state", "user", "first_audio", "jarvis_done", "partial")],
        "fine_domanda_s": [round(p[0] - room_t0 + len(v) / 16000, 2) for p, v in zip(plan, voices, strict=True)],
    }
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("domande", nargs="*", default=["Jarvis, quanto fa diciassette per ventitré?"])
    ap.add_argument("--echo", type=float, default=0.0)
    ap.add_argument("--gap", type=float, default=14.0, help="secondi tra una domanda e l'altra")
    ap.add_argument("--cervello", default=None, help="claude, ollama-cloud o local (solo per questa prova)")
    a = ap.parse_args()
    log.setup()
    r = asyncio.run(main(a.domande, a.echo, a.gap, a.cervello))
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))

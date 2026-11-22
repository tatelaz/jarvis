"""Una voce nuova per Jarvis da un file audio o video (strumento `crea_voce`).

Serve un pezzo di parlato pulito di 10-14 secondi: se chi chiede non dice dove, lo cerca qui (`best_span`: il
tratto più pieno di voce, che comincia e finisce in una pausa, senza silenzi lunghi né suoni saturati). Il pezzo
si porta a 24 kHz mono a volume giusto, le orecchie di Jarvis ne scrivono il testo (il modello della voce vuole
anche le parole dette) e tutto va in `data/voices/<nome>` con la sua scheda, come le voci incluse.

La voce che ne esce è **ispirata** a quella del file ma non è la voce vera: è un'imitazione che il modello della voce
ricava da pochi secondi, e la scheda (`voice.json`) lo dice. Solo per uso personale: non serve a far credere che
parli davvero quella persona.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import paths

log = logging.getLogger("jarvis.speech")

REF_SR = 24_000  # come ref.wav delle voci incluse
STT_SR = 16_000
HOP_S = 0.05
BEST_MIN_S, BEST_MAX_S = 10.0, 14.0  # il pezzo cercato da solo
MIN_S, MAX_S = 4.0, 20.0  # un pezzo indicato da chi chiede
MAX_SCAN_S = 10 * 60.0  # si cerca nei primi 10 minuti del file (a 24 kHz sono già 58 MB)
LONG_PAUSE_S = 0.8  # una pausa più lunga dentro il pezzo pesa nella scelta
QUIET_DB = -50.0  # sotto, silenzio vero
TARGET_RMS = 0.1  # circa -20 dBFS sul parlato
PEAK = 0.97
DISCLAIMER = ("Non è la voce vera: è un'imitazione che il modello della voce di Jarvis ricava da pochi secondi. "
              "Solo per uso personale: non usarla per far credere che parli davvero quella persona.")
# lo stesso effetto "del film" delle voci incluse
JARVIS_FX = {"intensity": 1.0, "eq_strength": 1.0, "flanger_mix": 0.55, "flanger_feedback": 0.4,
             "reflections_mix": 0.18, "drive": 0.25}


class VoiceMakerError(ValueError):
    """Una voce che non si può creare: il messaggio dice perché, per il cervello."""


@dataclass(frozen=True)
class Made:
    name: str
    folder: Path
    start: float
    end: float
    text: str


def slug(name: str) -> str:
    """Il nome della cartella: minuscole senza accenti, trattini al posto degli spazi."""
    t = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:32].strip("-")
    if not t:
        raise VoiceMakerError("serve un nome per la voce (lettere o numeri)")
    if t.startswith("base-"):
        raise VoiceMakerError("i nomi che cominciano con 'base-' sono delle voci incluse: scegline un altro")
    return t


def frame_db(audio: np.ndarray, sr: int) -> np.ndarray:
    hop = int(HOP_S * sr)
    k = len(audio) // hop
    if k == 0:
        return np.zeros(0)
    rms = np.sqrt((audio[:k * hop].reshape(k, hop).astype(np.float64) ** 2).mean(axis=1))
    return 20 * np.log10(rms + 1e-9)


def voiced_frames(db: np.ndarray) -> np.ndarray:
    """Le finestre da 50 ms con la voce: entro 25 dB dai picchi del parlato e sopra il silenzio vero."""
    if db.size == 0:
        return np.zeros(0, bool)
    top = float(np.percentile(db, 95))
    return (db > top - 25.0) & (db > QUIET_DB)


def best_span(audio: np.ndarray, sr: int) -> tuple[float, float]:
    """Il tratto di 10-14 s più pieno di voce che comincia e finisce in una pausa (mai a metà parola), con poche
    pause lunghe e niente saturazione. Errore se nel file non c'è abbastanza parlato."""
    db = frame_db(audio, sr)
    voiced = voiced_frames(db)
    n = voiced.size
    lo, hi = int(BEST_MIN_S / HOP_S), int(BEST_MAX_S / HOP_S)
    if n < lo:
        raise VoiceMakerError(f"il file è troppo corto: servono almeno {BEST_MIN_S:g} secondi di parlato")
    hop = int(HOP_S * sr)
    clipped = np.abs(audio[:n * hop]).reshape(n, hop).max(axis=1) >= 0.999
    pause = ~voiced
    v = voiced.astype(np.int8)
    # dove può cominciare: voce dopo una pausa; dove può finire: voce prima di una pausa (o i bordi del file)
    starts = np.flatnonzero(voiced & np.concatenate([[True], pause[:-1]]))
    ends = np.flatnonzero(voiced & np.concatenate([pause[1:], [True]])) + 1
    edges = np.flatnonzero(np.diff(np.concatenate([[0], pause.astype(np.int8), [0]])))
    run_starts, run_lens = edges[0::2], edges[1::2] - edges[0::2]  # le pause, una per una
    cum_v = np.concatenate([[0], np.cumsum(v)])
    cum_c = np.concatenate([[0], np.cumsum(clipped)])
    best, best_score = None, -1.0
    for s in starts:
        a = max(0, int(s) - 2)  # un attimo di respiro prima della prima parola
        i0 = int(np.searchsorted(run_starts, s, "right"))
        for e in ends[np.searchsorted(ends, s + lo):np.searchsorted(ends, s + hi, "right")]:
            b = min(n, int(e) + 2)
            i1 = int(np.searchsorted(run_starts, e, "left"))
            longest = int(run_lens[i0:i1].max()) if i1 > i0 else 0  # la pausa più lunga dentro il pezzo
            length = b - a
            score = ((cum_v[b] - cum_v[a]) / length - 0.5 * max(0.0, longest * HOP_S - LONG_PAUSE_S)
                     - 5.0 * (cum_c[b] - cum_c[a]) / length)
            if score > best_score:
                best, best_score = (a, b), score
    if best is None or best_score < 0.55:
        raise VoiceMakerError("nel file non trovo 10-14 secondi di parlato pulito (una sola voce, senza musica sotto): "
                              "indica tu da dove a dove, con inizio e fine")
    return best[0] * HOP_S, best[1] * HOP_S


def prepare(clip: np.ndarray, sr: int) -> np.ndarray:
    """Volume giusto sul parlato, picchi sotto il massimo, attacco e coda sfumati (15 ms)."""
    x = clip.astype(np.float64) - float(np.mean(clip))
    db = frame_db(x, sr)
    voiced = voiced_frames(db)
    hop = int(HOP_S * sr)
    speech = x[:voiced.size * hop].reshape(-1, hop)[voiced] if voiced.any() else x
    rms = float(np.sqrt(np.mean(speech ** 2))) if speech.size else 0.0
    if rms < 1e-4:
        raise VoiceMakerError("il pezzo scelto è in silenzio")
    gain = min(TARGET_RMS / rms, PEAK / (float(np.max(np.abs(x))) + 1e-9))
    x *= gain
    f = min(len(x) // 2, int(0.015 * sr))
    if f:
        ramp = np.linspace(0.0, 1.0, f)
        x[:f] *= ramp
        x[-f:] *= ramp[::-1]
    return x.astype(np.float32)


class VoiceMaker:
    def __init__(self, transcribe: Callable[[np.ndarray], Awaitable[str]], voices_dir: Path | None = None,
                 load: Callable[[Path, float, float, int], np.ndarray] | None = None,
                 language: Callable[[], str] = lambda: "it") -> None:
        self.transcribe = transcribe  # audio a 16 kHz -> testo (le orecchie di Jarvis)
        self.voices_dir = voices_dir or paths.DATA_DIR / "voices"
        self.load = load or _load
        self.language = language

    async def make(self, source: Path, name: str, start: float | None = None, end: float | None = None,
                   whose: str = "", effect: bool = True) -> Made:
        import asyncio

        import soundfile as sf

        key = slug(name)
        folder = self.voices_dir / key
        if folder.exists():
            raise VoiceMakerError(f"c'è già una voce chiamata '{key}': scegli un altro nome")
        if (start is None) != (end is None):
            raise VoiceMakerError("servono tutti e due: inizio e fine (oppure nessuno, e il pezzo lo cerco io)")
        if start is not None:
            if start < 0 or not MIN_S <= end - start <= MAX_S:
                raise VoiceMakerError(f"il pezzo deve durare da {MIN_S:g} a {MAX_S:g} secondi (meglio 10-14)")
            a, b = start, end
        else:
            whole = await asyncio.to_thread(self.load, source, 0.0, MAX_SCAN_S, REF_SR)
            if whole.size == 0:
                raise VoiceMakerError("nel file non c'è audio")
            a, b = best_span(whole, REF_SR)
        clip = await asyncio.to_thread(self.load, source, a, b, REF_SR)
        if clip.size < int((b - a) * REF_SR * 0.9):  # il file finisce prima del pezzo chiesto
            raise VoiceMakerError("il file finisce prima della fine indicata")
        ref = prepare(clip, REF_SR)
        text = " ".join((await self.transcribe(await asyncio.to_thread(self.load, source, a, b, STT_SR))).split())
        if len(text.split()) < 4:
            raise VoiceMakerError("nel pezzo scelto non capisco abbastanza parole: serve parlato chiaro")
        folder.mkdir(parents=True)
        sf.write(folder / "ref.wav", ref, REF_SR, subtype="PCM_16")
        who = " ".join(str(whose).split())[:80] or Path(source).stem[:80]
        meta = {
            "name": key,
            "description": f"Ispirata alla voce di {who}. {DISCLAIMER}",
            "imitation": True,
            "language": self.language(),
            "ref": "ref.wav",
            "ref_text": text,
            "seed": 7,
            "speed": 1.0,
            "fx": dict(JARVIS_FX) if effect else {"intensity": 0.0},
            "source": {"file": Path(source).name, "from_s": round(a, 2), "to_s": round(b, 2),
                       "made": time.strftime("%Y-%m-%d")},
        }
        (folder / "voice.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("voce '%s' creata da %s (%.1f-%.1f s)", key, Path(source).name, a, b)
        return Made(key, folder, a, b, text)


def _load(path: Path, start: float, end: float, rate: int) -> np.ndarray:
    from ..media.watch import load_audio

    return load_audio(path, start, end, rate)

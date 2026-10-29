"""Guardare un video e ascoltare un audio: i fotogrammi dei cambi di scena e il parlato trascritto.

Il cervello (Claude) vede le immagini e legge il testo: così "guarda" un video senza un modello video.
- Fotogrammi: si decodifica a intervalli regolari (solo i fotogrammi chiave se sono abbastanza fitti, altrimenti
  tutti) e per ognuno si tiene una copia minuscola in grigio, per misurare quanto cambia la scena (pixel e
  istogramma), e il JPEG già pronto. Si scelgono i cambi più forti, mai due troppo vicini, più qualche fotogramma
  sparso dove la scena non cambia mai (un'intervista ha un solo "cambio": l'inizio).
- Parlato: l'audio a 16 kHz va alle orecchie di Jarvis (parakeet-ultra, le stesse dei turni di parola) a pezzi di
  al massimo 30 secondi, tagliati nel punto più silenzioso; i pezzi muti si saltano.
Tutto con PyAV (FFmpeg dentro il pacchetto: niente programmi esterni da installare).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from ..apps.base import AppError
from ..brain.tools import Image, ToolOutput

log = logging.getLogger("jarvis.media")

SR = 16000  # le orecchie di Jarvis vogliono mono a 16 kHz
SMALL_W, SMALL_H = 64, 36  # la copia minuscola per misurare i cambi di scena
MAX_SAMPLES = 120  # fotogrammi esaminati al massimo (ognuno costa ~4 ms di JPEG)
MIN_GAP_S = 0.25  # mai esaminare due fotogrammi più vicini di così
SCENE_CUT = 0.12  # oltre questa differenza (0-1) è un cambio di scena
SPARSE_EVERY_S = 45.0  # dove la scena non cambia, un fotogramma ogni tanto (almeno MIN_FRAMES)
MIN_FRAMES = 4
CHUNK_MAX_S = 30.0  # pezzi per le orecchie: fatte per frasi, non per monologhi
CHUNK_MIN_S = 18.0  # il taglio si cerca nella pausa più silenziosa tra 18 e 30 secondi
QUIET_RMS = 0.005  # un pezzo che non supera mai questo livello (in 100 ms) è silenzio: non si trascrive
JPEG_QUALITY = 80


class MediaError(AppError):
    """Errore da spiegare al cervello: file che non c'è, formato illeggibile, sito che non scarica..."""


@dataclass
class Probe:
    duration: float  # secondi (0 = sconosciuta)
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    title: str = ""


@dataclass
class Sample:
    """Un fotogramma esaminato: la copia minuscola per i confronti e il JPEG pronto da mostrare."""
    t: float
    small: np.ndarray  # grigio SMALL_H×SMALL_W, valori 0-1
    jpeg: bytes = b""
    width: int = 0
    height: int = 0


@dataclass
class Frame:
    t: float
    jpeg: bytes
    width: int
    height: int
    score: float  # quanto è cambiata la scena rispetto a prima (1 = primo fotogramma)

    def tokens(self) -> int:
        """Costo per Claude: ~1 token ogni 750 pixel (documentazione di Anthropic sulla visione)."""
        return math.ceil(self.width * self.height / 750)


@dataclass
class Segment:
    t0: float
    t1: float
    text: str


@dataclass
class Watched:
    name: str  # per il cervello: titolo o nome del file, e da dove viene
    duration: float
    start: float
    end: float
    has_video: bool
    has_audio: bool
    frames: list[Frame] = field(default_factory=list)
    transcript: list[Segment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    sounds: str = ""  # cosa ha sentito il modello locale (musica, suoni), se richiesto

    def output(self, max_chars: int) -> ToolOutput:
        """Il risultato per il cervello: intestazione, fotogrammi con l'ora, parlato con l'ora, note."""
        kind = "Video" if self.has_video else "Audio"
        whole = self.duration and self.start <= 0.5 and self.end >= self.duration - 0.5
        span = (f"durata {clock(self.duration)}" if whole else
                f"guardato da {clock(self.start)} a {clock(self.end)}"
                + (f" su {clock(self.duration)}" if self.duration else ""))
        head = [f"{kind}: {self.name} ({span})."]
        if self.has_video:
            head.append(f"{len(self.frames)} fotogrammi ai cambi di scena (sotto, in ordine di tempo).")
        parts: list[str | Image] = [" ".join(head)]
        for i, f in enumerate(self.frames, 1):
            parts.append(f"[fotogramma {i} di {len(self.frames)}, {clock(f.t)}]")
            parts.append(Image(f.jpeg))
        if self.has_audio:
            text, cut_at = transcript_text(self.transcript, max_chars)
            if text:
                parts.append("Parlato (trascritto dalle orecchie di Jarvis: i nomi propri possono essere sbagliati):\n"
                             + text)
            else:
                parts.append("Parlato: nessuna parola riconosciuta (musica, suoni o silenzio).")
            if cut_at is not None:
                parts.append(f"Trascrizione tagliata a {clock(cut_at)} (limite media.transcript_chars): per il resto "
                             f"richiama con inizio={clock(cut_at)}.")
        else:
            parts.append("Nessuna traccia audio.")
        if self.sounds:
            parts.append("Musica e suoni (modello locale, in inglese; può sbagliare): " + self.sounds)
        parts += [f"Nota: {n}" for n in self.notes]
        return ToolOutput(parts)

    def tokens(self, max_chars: int) -> int:
        """Stima del costo per Claude: immagini a pixel, testo a ~3,5 caratteri per token (italiano)."""
        out = self.output(max_chars)
        return sum(f.tokens() for f in self.frames) + math.ceil(len(out.text) / 3.5)


# ---------------------------------------------------------------------- tempi


def clock(t: float) -> str:
    """Secondi -> "1:05" o "1:02:03"."""
    t = max(0, round(t))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_time(v: Any) -> float | None:
    """"1:30", "90", "1:02:03", "2m", "1h5m", "1,5" -> secondi; vuoto -> None."""
    if v is None:
        return None
    s = str(v).strip().lower().replace(",", ".")
    if not s:
        return None
    try:
        if ":" in s:
            total = 0.0
            for p in s.split(":"):
                total = total * 60 + float(p or 0)
            return total
        if any(c in s for c in "hms"):
            total, num = 0.0, ""
            for c in s.replace(" ", ""):
                if c.isdigit() or c == ".":
                    num += c
                elif c in "hms" and num:
                    total += float(num) * {"h": 3600, "m": 60, "s": 1}[c]
                    num = ""
                else:
                    raise ValueError(c)
            return total + (float(num) if num else 0.0)
        return float(s)
    except ValueError:
        raise MediaError(f"tempo non valido: '{v}' (usa secondi o minuti:secondi, es. 1:30)") from None


# ---------------------------------------------------------------------- scelta dei fotogrammi


def change(a: np.ndarray, b: np.ndarray) -> float:
    """Quanto è cambiata la scena (0-1): media della differenza dei pixel e di quella degli istogrammi. Una
    mano che passa muove i pixel, una dissolvenza anche l'istogramma: un taglio netto muove entrambi."""
    pix = float(np.mean(np.abs(a - b)))
    ha = np.histogram(a, bins=16, range=(0.0, 1.0))[0] / a.size
    hb = np.histogram(b, bins=16, range=(0.0, 1.0))[0] / b.size
    return (pix + 0.5 * float(np.abs(ha - hb).sum())) / 2


def pick(samples: list[Sample], max_frames: int, cut: float = SCENE_CUT) -> list[tuple[int, float]]:
    """Quali fotogrammi mostrare: (indice, punteggio) in ordine di tempo.

    Il primo sempre; poi i cambi di scena più forti, mai due più vicini di un intervallo minimo (una dissolvenza
    non vale tre fotogrammi); infine, se la scena cambia poco, fotogrammi sparsi nei buchi più lunghi."""
    if not samples or max_frames <= 0:
        return []
    scores = [1.0] + [change(samples[i - 1].small, samples[i].small) for i in range(1, len(samples))]
    span = samples[-1].t - samples[0].t
    min_gap = span / (max_frames * 2) if span > 0 else 0.0
    chosen = {0}
    for i in sorted(range(1, len(samples)), key=lambda i: -scores[i]):
        if len(chosen) >= max_frames or scores[i] < cut:
            break
        if all(abs(samples[i].t - samples[j].t) >= min_gap for j in chosen):
            chosen.add(i)
    want = min(max_frames, len(samples), max(MIN_FRAMES, round(span / SPARSE_EVERY_S)))
    while len(chosen) < want:  # nel mezzo del buco più lungo (anche quello fino alla fine)
        bounds = [*sorted(samples[j].t for j in chosen), samples[-1].t]
        a, b = max(pairwise(bounds), key=lambda p: p[1] - p[0])
        mid = (a + b) / 2
        free = [k for k in range(len(samples)) if k not in chosen]
        chosen.add(min(free, key=lambda k: abs(samples[k].t - mid)))
    return [(i, scores[i]) for i in sorted(chosen)]


# ---------------------------------------------------------------------- PyAV


def _av():
    try:
        import av
    except ImportError:
        raise MediaError("manca il pacchetto PyAV per leggere i video: va installato nel venv di Jarvis "
                         "(uv add av)") from None
    return av


def _open(path: Path):
    av = _av()
    try:
        return av.open(str(path))
    except (av.FFmpegError, OSError) as e:
        raise MediaError(f"non riesco ad aprire {Path(path).name}: {e}") from None


def _is_cover(stream) -> bool:
    """Una copertina dentro un mp3 o un m4a è una "traccia video" di un solo fotogramma: non è un video."""
    disp = getattr(stream, "disposition", 0)
    try:
        return bool(int(disp) & 0x0400)  # AV_DISPOSITION_ATTACHED_PIC
    except (TypeError, ValueError):
        return False


def probe(path: Path) -> Probe:
    with _open(path) as c:
        v = next((s for s in c.streams.video if not _is_cover(s)), None)
        a = c.streams.audio[0] if c.streams.audio else None
        duration = c.duration / 1_000_000 if c.duration else 0.0
        if not duration:
            for s in (v, a):
                if s is not None and s.duration and s.time_base:
                    duration = max(duration, float(s.duration * s.time_base))
        title = str((c.metadata or {}).get("title") or "")
        return Probe(duration, v is not None, a is not None,
                     v.codec_context.width if v is not None else 0, v.codec_context.height if v is not None else 0, title)


def fit(width: int, height: int, max_px: int) -> tuple[int, int]:
    """Dimensioni con il lato lungo al massimo max_px, proporzioni e numeri pari (per il ridimensionamento)."""
    if width <= 0 or height <= 0:
        return width, height
    k = min(1.0, max_px / max(width, height))
    return max(2, round(width * k / 2) * 2), max(2, round(height * k / 2) * 2)


def _sample(frame, t: float, max_px: int) -> Sample:
    from PIL import Image as PilImage

    small = frame.to_ndarray(format="gray", width=SMALL_W, height=SMALL_H).astype(np.float32) / 255.0
    w, h = fit(frame.width, frame.height, max_px)
    img = frame.to_image(width=w, height=h, interpolation="AREA")
    rot = int(getattr(frame, "rotation", 0) or 0)
    if rot % 360:  # video del telefono girato: la matrice dice di quanto ruotarlo (in senso antiorario)
        img = img.rotate(rot, expand=True, resample=PilImage.Resampling.BICUBIC)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return Sample(t, small, buf.getvalue(), img.width, img.height)


def _keyframes_enough(path: Path, start: float, end: float, need: int) -> bool:
    """Ci sono almeno `need` fotogrammi chiave nell'intervallo? Si leggono solo i pacchetti (niente decodifica) e ci
    si ferma appena bastano."""
    with _open(path) as c:
        vs = next(s for s in c.streams.video if not _is_cover(s))
        if start > 0 and vs.time_base:
            c.seek(int(start / vs.time_base), stream=vs, backward=True)
        n = 0
        for pkt in c.demux(vs):
            if pkt.pts is None or not vs.time_base:
                continue
            t = float(pkt.pts * vs.time_base)
            if t > end:
                break
            if pkt.is_keyframe and t >= start - 1e-3:
                n += 1
                if n >= need:
                    return True
    return False


def scan_video(path: Path, start: float, end: float, max_px: int, max_samples: int = MAX_SAMPLES) -> list[Sample]:
    """I fotogrammi da esaminare, uno ogni (durata / max_samples) secondi."""
    span = max(end - start, 0.001)
    gap = max(span / max_samples, MIN_GAP_S)
    keys_only = _keyframes_enough(path, start, end, max(2, int(span / gap * 0.5)))
    av = _av()
    out: list[Sample] = []
    with _open(path) as c:
        vs = next(s for s in c.streams.video if not _is_cover(s))
        vs.thread_type = "AUTO"
        # solo i fotogrammi chiave (veloce) se sono fitti; altrimenti tutti tranne quelli che nessuno usa come
        # riferimento (i B, saltarli non rovina gli altri)
        vs.codec_context.skip_frame = "NONKEY" if keys_only else "NONREF"
        if start > 0 and vs.time_base:
            c.seek(int(start / vs.time_base), stream=vs, backward=True)
        next_t = start
        try:
            for f in c.decode(vs):
                t = f.time
                if t is None or t < start - 1e-3 or t + 1e-3 < next_t:
                    continue
                if t > end:
                    break
                out.append(_sample(f, t, max_px))
                next_t = t + gap
        except av.FFmpegError as e:  # file troncato: si tiene quello che si è visto
            log.warning("video %s letto solo in parte: %s", Path(path).name, e)
    log.info("video %s: %d fotogrammi esaminati (%s)", Path(path).name, len(out),
             "solo chiave" if keys_only else "tutti")
    return out


def load_audio(path: Path, start: float, end: float) -> np.ndarray:
    """L'audio dell'intervallo, mono a 16 kHz in float32."""
    av = _av()
    with _open(path) as c:
        if not c.streams.audio:
            return np.zeros(0, np.float32)
        a = c.streams.audio[0]
        if start > 0:
            c.seek(int(start * 1_000_000), backward=True)  # in microsecondi (AV_TIME_BASE)
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        parts: list[np.ndarray] = []
        skip: int | None = None
        limit = int((end - start) * SR)
        got = 0

        def add(frames) -> None:
            nonlocal got
            for g in frames:
                x = g.to_ndarray().reshape(-1)
                parts.append(x)
                got += x.size

        try:
            for f in c.decode(a):
                if skip is None:  # dopo un salto si riparte un po' prima: si scarta quel pezzo
                    skip = max(0, round((start - (f.time if f.time is not None else start)) * SR))
                add(rs.resample(f))
                if got >= skip + limit:
                    break
            add(rs.resample(None))
        except av.FFmpegError as e:
            log.warning("audio %s letto solo in parte: %s", Path(path).name, e)
    x = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    s = skip or 0
    return np.ascontiguousarray(x[s:s + limit], dtype=np.float32)


# ---------------------------------------------------------------------- parlato


def chunks(audio: np.ndarray, max_s: float = CHUNK_MAX_S, min_s: float = CHUNK_MIN_S) -> list[tuple[int, int]]:
    """Pezzi di al massimo max_s secondi, tagliati nei 50 ms più silenziosi tra min_s e max_s: mai a metà parola,
    se c'è una pausa."""
    n, hop = len(audio), int(0.05 * SR)
    out, i = [], 0
    while n - i > max_s * SR:
        lo, hi = i + int(min_s * SR), i + int(max_s * SR)
        k = (hi - lo) // hop
        energy = (audio[lo:lo + k * hop].reshape(k, hop) ** 2).mean(axis=1)
        cut = lo + int(np.argmin(energy)) * hop + hop // 2
        out.append((i, cut))
        i = cut
    if n > i:
        out.append((i, n))
    return out


def silent(x: np.ndarray) -> bool:
    """Silenzio vero (nessun pezzo da 100 ms sopra QUIET_RMS): musica e rumore invece si trascrivono."""
    hop = int(0.1 * SR)
    k = len(x) // hop
    if k == 0:
        return float(np.sqrt(np.mean(x ** 2))) < QUIET_RMS if len(x) else True
    rms = np.sqrt((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1))
    return float(rms.max()) < QUIET_RMS


async def transcribe(stt: Callable[[np.ndarray], Awaitable[str]], audio: np.ndarray, t0: float = 0.0
                     ) -> list[Segment]:
    out = []
    for a, b in chunks(audio):
        piece = audio[a:b]
        if len(piece) < SR // 10 or silent(piece):
            continue
        text = (await stt(piece)).strip()
        if text:
            out.append(Segment(t0 + a / SR, t0 + b / SR, text))
    return out


def transcript_text(segments: list[Segment], max_chars: int) -> tuple[str, float | None]:
    """Il parlato con l'ora di ogni pezzo; se supera max_chars si ferma a un pezzo intero e dice dove."""
    lines, used = [], 0
    for s in segments:
        line = f"[{clock(s.t0)}] {s.text}"
        if lines and used + len(line) > max_chars:
            return "\n".join(lines), s.t0
        if not lines and len(line) > max_chars:  # un pezzo solo più lungo del limite: si taglia lui
            return line[:max_chars] + "…", s.t1
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines), None


# ---------------------------------------------------------------------- tutto insieme


@dataclass
class Limits:
    frames: int = 20
    frame_px: int = 768
    max_minutes: float = 60.0
    transcript_chars: int = 30000


async def watch(video: Path | None, audio: Path | None, stt: Callable[[np.ndarray], Awaitable[str]] | None,
                limits: Limits, name: str, start: float | None = None, end: float | None = None,
                keep_audio: bool = False) -> tuple[Watched, np.ndarray | None]:
    """Guarda (fotogrammi) e ascolta (parlato) l'intervallo chiesto. video e audio possono essere lo stesso file
    o due file (YouTube li dà separati). Con keep_audio restituisce anche l'audio (per il modello dei suoni)."""
    t = time.perf_counter()
    probes = await asyncio.to_thread(lambda: {p: probe(p) for p in {x for x in (video, audio) if x is not None}})
    has_video = video is not None and probes[video].has_video
    has_audio = audio is not None and probes[audio].has_audio
    if not has_video and not has_audio:
        raise MediaError(f"{name}: né video né audio leggibili")
    duration = max(p.duration for p in probes.values())
    s = max(0.0, start or 0.0)
    if duration and s >= duration:
        raise MediaError(f"l'inizio ({clock(s)}) è oltre la fine del file ({clock(duration)})")
    e = end if end is not None else (duration or s + limits.max_minutes * 60)
    if duration:
        e = min(e, duration)
    if e <= s:
        raise MediaError(f"intervallo vuoto: fine ({clock(e)}) prima dell'inizio ({clock(s)})")
    notes = []
    if e - s > limits.max_minutes * 60 + 0.5:
        e = s + limits.max_minutes * 60
        notes.append(f"guardati solo {limits.max_minutes:g} minuti (media.max_minutes): per il resto richiama con "
                     f"inizio={clock(e)}")
    w = Watched(name, duration, s, e, has_video, has_audio, notes=notes)
    w.timings["probe"] = time.perf_counter() - t

    async def frames() -> None:
        t0 = time.perf_counter()
        samples = await asyncio.to_thread(scan_video, video, s, e, limits.frame_px)
        w.frames = [Frame(samples[i].t, samples[i].jpeg, samples[i].width, samples[i].height, score)
                    for i, score in pick(samples, limits.frames)]
        w.timings["frames"] = time.perf_counter() - t0

    pcm: np.ndarray | None = None

    async def speech() -> None:
        nonlocal pcm
        t0 = time.perf_counter()
        pcm = await asyncio.to_thread(load_audio, audio, s, e)
        w.timings["audio"] = time.perf_counter() - t0
        if stt is None:
            w.notes.append("le orecchie di Jarvis non sono disponibili: niente trascrizione")
            return
        t1 = time.perf_counter()
        w.transcript = await transcribe(stt, pcm, s)
        w.timings["stt"] = time.perf_counter() - t1

    jobs = ([frames()] if has_video else []) + ([speech()] if has_audio else [])
    await asyncio.gather(*jobs)
    if has_video and not w.frames:
        w.notes.append("nessun fotogramma leggibile in questo intervallo")
    w.timings["total"] = time.perf_counter() - t
    log.info("guardato %s [%s-%s]: %d fotogrammi, %d pezzi di parlato, %s", name, clock(s), clock(e), len(w.frames),
             len(w.transcript), {k: round(v, 2) for k, v in w.timings.items()})
    return w, (pcm if keep_audio else None)

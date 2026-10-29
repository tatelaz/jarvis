"""La voce di Jarvis: testo → audio pronto da riprodurre.

Sopra al client TTS aggiunge tutto ciò che serve per non deludere mai:
- testo reso pronunciabile (numeri, sigle, niente markdown);
- controllo del risultato: audio vuoto (succede) o di durata assurda per il testo (allucinazioni,
  ripetizioni) → nuovo tentativo con un altro seme, poi voce di riserva;
- rifinitura: silenzi iniziali/finali tagliati, pause interne troppo lunghe accorciate, ritmo
  costante (il modello va da 18 a 26 caratteri al secondo e ignora il parametro di velocità),
  volume uniforme, effetto "JARVIS del film";
- cache: le frasi ricorrenti ("Subito, signore.") escono istantaneamente.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .. import paths
from ..audio.stretch import wsola
from ..audio.voicefx import VoiceFx, VoiceFxParams
from .text import speakable
from .tts import TtsClient, Voice

log = logging.getLogger("jarvis.voice")

COMMON_PHRASES = [
    "Subito, signore.", "Certamente, signore.", "Certamente.", "Subito.", "Come desidera.", "Fatto.",
    "Un momento.", "Un attimo, signore.", "Me ne occupo io.", "Ricevuto.", "Va bene.", "D'accordo.",
    "Ecco fatto.", "Sì, signore.", "Bentornato, signore.", "Buongiorno, signore.", "Buonasera, signore.",
    "Ai suoi ordini.", "Al suo servizio, signore.", "Ci penso io.", "Controllo subito.", "Verifico subito.",
]


class VoiceError(RuntimeError):
    pass


def trim_and_tighten(audio: np.ndarray, sr: int, keep_ms: int = 40, max_pause_ms: int = 380,
                     floor_db: float = -45.0) -> np.ndarray:
    """Taglia i silenzi agli estremi e accorcia le pause interne troppo lunghe."""
    if audio.size == 0:
        return audio
    fr = max(1, sr // 100)  # 10 ms
    n = audio.size // fr
    if n == 0:
        return audio
    frames = audio[: n * fr].reshape(n, fr)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) + 1e-12
    thr = max(rms.max() * 10 ** (floor_db / 20), 1e-4)
    voiced = np.where(rms > thr)[0]
    if voiced.size == 0:
        return np.zeros(0, np.float32)
    keep = keep_ms // 10
    start, end = max(0, voiced[0] - keep), min(n, voiced[-1] + 1 + keep)
    out, run = [], 0
    max_run = max_pause_ms // 10
    for i in range(start, end):
        silent = rms[i] <= thr
        run = run + 1 if silent else 0
        if silent and run > max_run:
            continue
        out.append(frames[i])
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)


def normalize_loudness(audio: np.ndarray, target_dbfs: float = -20.0) -> np.ndarray:
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
    if rms < 1e-6:
        return audio
    y = audio * (10 ** (target_dbfs / 20) / rms)
    peak = float(np.max(np.abs(y)))
    return (y * (0.97 / peak) if peak > 0.97 else y).astype(np.float32)


def plausible_duration(text: str, seconds: float) -> bool:
    """L'italiano parlato sta sui 12-17 caratteri al secondo: fuori da limiti larghi c'è un guasto."""
    chars = max(1, len(text))
    return 0.15 + chars / 40 <= seconds <= 2.0 + chars / 5


# Ritmo a velocità 1: il JARVIS del film nella scena del volo fa ~21 caratteri al secondo (è un'emergenza);
# in conversazione 19 suona calmo senza essere lento. Entro ±6 % la frase resta com'è.
BASE_RATE = 19.0
RATE_TOLERANCE = 0.06
STRETCH_LIMITS = (0.75, 1.4)


class VoiceOutput:
    def __init__(self, client: TtsClient, voice_meta: dict, model: str = "tts", fallback_model: str = "tts-fallback",
                 language: str = "Italian", cache_dir: Path | None = None, memory_items: int = 256,
                 speed: Callable[[], float] | float | None = None) -> None:
        self.client = client
        self.meta = voice_meta
        self.model = model
        self.fallback_model = fallback_model
        self.language = language
        self.fx = VoiceFx(VoiceFxParams(**voice_meta.get("fx", {})))
        self.seed = int(voice_meta.get("seed", 7))
        self._speed = speed  # impostazione "velocità voce" (1 = normale); può cambiare mentre gira
        # stima del tempo di sintesi (s) = scala · (a + b · caratteri). a e b misurati a motore libero;
        # la scala impara dalle sintesi vere (GPU contesa, PC carico) e rende la stima onesta
        self.synth_a, self.synth_b = 0.53, 0.0139
        self.synth_scale = 1.0
        self.cache_dir = cache_dir or (paths.DATA_DIR / "cache" / "tts")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[str, tuple[np.ndarray, int]] = OrderedDict()
        self._mem_items = memory_items
        self._lock = asyncio.Lock()  # una sintesi alla volta: la GPU non va contesa
        self._urgent = 0  # richieste vere in attesa: il riscaldamento si fa da parte
        self._last_urgent = 0.0
        self._no_urgent = asyncio.Event()
        self._no_urgent.set()
        self.stats = {"synth": 0, "cache_hits": 0, "retries": 0, "fallbacks": 0, "last_ms": 0.0}

    # ------------------------------------------------------------ cache

    def _key(self, text: str) -> str:
        fx = json.dumps(self.meta.get("fx", {}), sort_keys=True)
        raw = f"{self.meta.get('name')}|{self.model}|{self.seed}|ritmo{self.target_rate():.2f}|{fx}|{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> tuple[np.ndarray, int] | None:
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        f = self.cache_dir / f"{key}.npz"
        if f.exists():
            try:
                d = np.load(f)
                item = (d["audio"], int(d["sr"]))
                self._mem_put(key, item)
                return item
            except (OSError, ValueError, KeyError):
                f.unlink(missing_ok=True)
        return None

    def _mem_put(self, key: str, item: tuple[np.ndarray, int]) -> None:
        self._mem[key] = item
        self._mem.move_to_end(key)
        while len(self._mem) > self._mem_items:
            self._mem.popitem(last=False)

    def _cache_put(self, key: str, item: tuple[np.ndarray, int], persist: bool) -> None:
        self._mem_put(key, item)
        if persist:
            np.savez(self.cache_dir / f"{key}.npz", audio=item[0], sr=item[1])

    @property
    def speed(self) -> float:
        src = self._speed
        value = src() if callable(src) else src if src is not None else self.meta.get("speed", 1.0)
        return min(1.6, max(0.6, float(value)))

    def target_rate(self) -> float:
        """Caratteri al secondo a cui portare ogni frase."""
        return BASE_RATE * self.speed

    def pace(self, audio: np.ndarray, sr: int, text: str) -> np.ndarray:
        """Porta la frase al ritmo voluto se ne è uscita troppo veloce o troppo lenta."""
        seconds = audio.size / sr if sr else 0.0
        if seconds <= 0.2 or not text:
            return audio
        rate, target = len(text) / seconds, self.target_rate()
        if abs(rate / target - 1.0) <= RATE_TOLERANCE:
            return audio
        factor = min(STRETCH_LIMITS[1], max(STRETCH_LIMITS[0], rate / target))
        return wsola(audio, factor, sr)

    def estimate_seconds(self, text: str) -> float:
        """Quanto ci vorrà a sintetizzare questo testo (0 se è già in cache)."""
        spoken = speakable(text)
        if not spoken or self._cache_get(self._key(spoken)) is not None:
            return 0.0
        return self.synth_scale * (self.synth_a + self.synth_b * len(spoken))

    def _learn_timing(self, chars: int, seconds: float) -> None:
        if chars >= 15:  # le frasi cortissime sono dominate dal costo fisso: dicono poco sul resto
            ratio = seconds / (self.synth_a + self.synth_b * chars)
            self.synth_scale = min(5.0, max(0.5, 0.7 * self.synth_scale + 0.3 * ratio))

    def is_cached(self, text: str) -> bool:
        """Vero se la frase ha già l'audio pronto: suonerà senza attesa."""
        spoken = speakable(text)
        return bool(spoken) and self._cache_get(self._key(spoken)) is not None

    # ------------------------------------------------------------ sintesi

    async def prime(self, text: str = "Sistemi in linea.") -> float:
        """Carica davvero il modello con una sintesi vera, saltando la cache.

        Il motore carica i modelli al primo uso (~5 s): se la frase di prova arrivasse dalla cache, quel
        costo finirebbe sulla prima risposta vera. Restituisce i secondi impiegati."""
        async with self._lock:
            t = time.perf_counter()
            await self._synth_checked(speakable(text))
            return time.perf_counter() - t

    async def say(self, text: str, background: bool = False) -> tuple[np.ndarray, int, str]:
        """Restituisce (audio, frequenza, testo effettivamente pronunciato).

        background=True (riscaldamento della cache) cede sempre il passo alle frasi vere."""
        spoken = speakable(text)
        if not spoken:
            return np.zeros(0, np.float32), 24000, ""
        key = self._key(spoken)
        hit = self._cache_get(key)
        if hit is not None:
            self.stats["cache_hits"] += 1
            return hit[0], hit[1], spoken
        if background:
            await self._yield_to_urgent()
        else:
            self._urgent += 1
            self._no_urgent.clear()
        try:
            async with self._lock:
                t = time.perf_counter()
                audio, sr = await self._synth_checked(spoken)
                self.stats["last_ms"] = 1000 * (time.perf_counter() - t)
                self._learn_timing(len(spoken), time.perf_counter() - t)
        finally:
            if not background:
                self._urgent -= 1
                self._last_urgent = time.monotonic()
                if self._urgent == 0:
                    self._no_urgent.set()
        audio = self.pace(trim_and_tighten(audio, sr), sr, spoken)
        audio = normalize_loudness(self.fx.process(audio, sr))
        self.stats["synth"] += 1
        self._cache_put(key, (audio, sr), persist=len(spoken) <= 60)
        return audio, sr, spoken

    async def _yield_to_urgent(self, quiet_s: float = 3.0) -> None:
        """Aspetta che non ci siano frasi vere in coda da almeno quiet_s secondi."""
        while True:
            await self._no_urgent.wait()
            remaining = quiet_s - (time.monotonic() - self._last_urgent)
            if remaining <= 0 and self._urgent == 0:
                return
            await asyncio.sleep(max(0.05, remaining))

    async def _synth_checked(self, text: str) -> tuple[np.ndarray, int]:
        attempts = [(self.model, self.seed), (self.model, self.seed + 101), (self.fallback_model, self.seed)]
        last_err = None
        for i, (model, seed) in enumerate(attempts):
            try:
                lang = self.language if model == self.model else "it"
                # niente parametro di velocità: Qwen3 lo ignora; il ritmo lo fa pace()
                audio, sr = await self.client.synthesize(model, text, Voice(language=lang), seed=seed, timeout=60)
            except Exception as e:  # motore giù, timeout, errore del modello: si prova la strada successiva
                last_err = f"{type(e).__name__}: {e}"
                log.warning("sintesi fallita (%s, tentativo %d): %s", model, i + 1, last_err)
                continue
            dur = audio.size / sr if sr else 0.0
            if audio.size and plausible_duration(text, dur):
                if i == 1:
                    self.stats["retries"] += 1
                if model == self.fallback_model:
                    self.stats["fallbacks"] += 1
                return audio, sr
            last_err = f"durata implausibile {dur:.2f}s per {len(text)} caratteri"
            log.warning("audio scartato (%s, seme %d): %s", model, seed, last_err)
        raise VoiceError(f"nessuna sintesi valida per {text!r}: {last_err}")

    async def warm_up(self, phrases: list[str] | None = None) -> int:
        """Prepara in anticipo le frasi ricorrenti (si fa quando il motore è libero)."""
        n = 0
        for p in phrases or COMMON_PHRASES:
            if self._cache_get(self._key(speakable(p))) is None:
                try:
                    await self.say(p, background=True)
                    n += 1
                except VoiceError as e:
                    log.warning("preparazione di %r fallita: %s", p, e)
        return n

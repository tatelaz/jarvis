"""Il modello "omni": sente ciò che le parole non dicono (musica, suoni, rumori, azioni) e lo descrive a parole.

Claude vede già i fotogrammi e legge il parlato; quello che gli manca è l'orecchio per tutto il resto. Questo modello
gira sul PC e si carica solo quando serve, perché la scheda video (RTX 4060, 8 GB) non basta per lui e per la voce
di Jarvis insieme: `GpuLease` scarica la voce (e, se serve, sposta l'udito sul processore, come il riposo), il
modello lavora, si spegne, e tutto torna com'era, anche se il modello fallisce o la richiesta viene annullata.

Candidati (dimensioni dei file da scaricare; la VRAM è stimata: la misura la fa scripts/bench/omni_race.py a Jarvis
spento):
- Audio Flamingo Next (NVIDIA, aprile 2026, 8B, Q4_K 5,46 GB) su audio.cpp, lo stesso motore delle orecchie: il
  migliore per musica e suoni tra i modelli aperti che stanno in 8 GB; solo audio, risponde in inglese.
- Qwen2.5-Omni 7B (Q4_K_M 4,68 GB + proiettore 1,55 GB) su llama.cpp: audio e immagini insieme, più vecchio (2025).
- LFM3-Omni 2B (Liquid AI, ottobre 2026, Q8_0 1,85 GB + proiettore 0,54 GB) su llama.cpp (supporto entrato il
  27/10/2026): audio e immagini, piccolo (l'udito resta sulla scheda video), meno preciso sull'audio.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .. import paths, winjob
from .watch import SR, Frame, MediaError, clock, silent

log = logging.getLogger("jarvis.media.omni")

OMNI_PORT = 8794  # 8788/8790 orecchie, 8791 finestra, 8792 voce
WINDOW_S = 60.0  # l'audio si descrive a finestre di un minuto (con i fotogrammi di quel minuto)
MAX_WINDOWS = 10  # al massimo 10 minuti per richiesta: oltre si chiede un intervallo
FRAMES_PER_WINDOW = 4
PROMPT = ("Describe what can be heard in this audio apart from the spoken words: music (genre, mood, instruments, "
          "tempo, whether it is a well-known song), sounds and noises, ambience, and the actions or events they "
          "suggest. Be factual and brief (at most 3 sentences). If there is only speech or silence, say so.")
PROMPT_FRAMES = " The images are frames from the same minute of the video, in order: also say what actions happen."


@dataclass(frozen=True)
class OmniModel:
    key: str
    title: str
    runtime: str  # "audiocpp" (stesso motore delle orecchie) o "llamacpp" (llama-server)
    files: tuple[str, ...]  # relativi a models/audiocpp (audiocpp) o models/omni (llamacpp); per llamacpp: modello, proiettore
    download_gb: float
    vram_gb: float  # stima: la misura vera la fa la gara
    sees: bool  # riceve anche i fotogrammi
    ears_off_gpu: bool  # non ci sta con l'udito sulla scheda video: l'udito passa sul processore
    repo: str  # da dove si scarica (Hugging Face)
    license: str
    server_args: tuple[str, ...] = ()  # opzioni in più per llama-server (misurate nella gara)

    def paths(self) -> list[Path]:
        base = paths.AUDIOCPP_MODELS if self.runtime == "audiocpp" else paths.MODELS_DIR / "omni"
        return [base / f for f in self.files]

    def missing(self) -> list[Path]:
        return [p for p in self.paths() if not p.is_file()]


MODELS = {m.key: m for m in (
    OmniModel("audio-flamingo-next", "Audio Flamingo Next", "audiocpp",
              ("Audio-Flamingo-GGUF/audio-flamingo-next-q4_k.gguf",), 5.46, 6.5, sees=False, ears_off_gpu=True,
              repo="audio-cpp/Audio-Flamingo-GGUF",
              license="NVIDIA OneWay Noncommercial: uso privato sì, commerciale no"),
    OmniModel("qwen2.5-omni-7b", "Qwen2.5-Omni 7B", "llamacpp",
              ("Qwen2.5-Omni-7B-GGUF/Qwen2.5-Omni-7B-Q4_K_M.gguf",
               "Qwen2.5-Omni-7B-GGUF/mmproj-Qwen2.5-Omni-7B-Q8_0.gguf"), 6.23, 7.0, sees=True, ears_off_gpu=True,
              repo="ggml-org/Qwen2.5-Omni-7B-GGUF", license="licenza Qwen (vedi la scheda del modello)"),
    OmniModel("lfm3-omni-2b", "LFM3-Omni 2B", "llamacpp",
              ("LFM3-Omni-2B-GGUF/LFM3-Omni-2B-Q8_0.gguf", "LFM3-Omni-2B-GGUF/mmproj-LFM3-Omni-2B-Q8_0.gguf"),
              2.39, 3.2, sees=True, ears_off_gpu=False, repo="ggml-org/LFM3-Omni-2B-GGUF",
              license="LFM Open License v1.0"),
)}


def windows(audio: np.ndarray, t0: float, window_s: float = WINDOW_S, limit: int = MAX_WINDOWS
            ) -> list[tuple[float, float, np.ndarray]]:
    """L'audio a finestre di un minuto (l'ultima può essere più corta); quelle silenziose si saltano."""
    n = int(window_s * SR)
    out = []
    for i in range(0, len(audio), n):
        piece = audio[i:i + n]
        if len(piece) < SR or silent(piece):
            continue
        out.append((t0 + i / SR, t0 + (i + len(piece)) / SR, piece))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------- prestito della scheda video


class GpuLease:
    """Presta la scheda video a un modello a richiesta e poi rimette tutto com'era.

    Usa lo stesso lucchetto del riposo: mai un riposo o un risveglio a metà prestito. Prima aspetta che Jarvis abbia
    finito di dire la frase già scritta ("lo ascolto un attimo"), poi scarica la voce; se il modello non ci sta con
    l'udito, sposta l'udito sul processore. Alla fine riaccende la voce (riprovando) e riporta l'udito dov'era
    possibile, anche se la richiesta è stata annullata a metà."""

    VOICE_TRIES = 3

    def __init__(self, *, lock: asyncio.Lock, resting: Callable[[], bool], quiet: Callable[[], Awaitable[None]],
                 stop_voice: Callable[[], Awaitable[None]], start_voice: Callable[[], Awaitable[None]],
                 ears_device: Callable[[], str], move_ears: Callable[[str], Awaitable[None]],
                 wanted_ears: Callable[[], Awaitable[str]], notify: Callable[[str], None] | None = None,
                 retry_s: float = 2.0) -> None:
        self.lock, self.resting, self.quiet = lock, resting, quiet
        self.stop_voice, self.start_voice = stop_voice, start_voice
        self.ears_device, self.move_ears, self.wanted_ears = ears_device, move_ears, wanted_ears
        self.notify = notify
        self.retry_s = retry_s
        self.active = False

    @contextlib.asynccontextmanager
    async def borrow(self, ears_off_gpu: bool) -> AsyncIterator[None]:
        async with self.lock:
            if self.resting():  # a riposo la scheda video è già libera
                yield
                return
            await self.quiet()
            moved = False
            self.active = True
            try:
                await self.stop_voice()
                if ears_off_gpu and self.ears_device() == "cuda":
                    await self.move_ears("cpu")
                    moved = True
                yield
            finally:
                job = asyncio.ensure_future(self._restore(moved))
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:  # annullato: si finisce di rimettere a posto prima di lasciare il lucchetto
                    await job
                    raise
                finally:
                    self.active = False

    async def _restore(self, moved: bool) -> None:
        jobs = [self._voice_back()]
        if moved:
            wanted = await self.wanted_ears()
            if wanted != "cpu":
                jobs.append(self.move_ears(wanted))
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, BaseException):
                log.error("dopo il modello omni non ho rimesso tutto a posto: %s", r)

    async def _voice_back(self) -> None:
        for attempt in range(1, self.VOICE_TRIES + 1):
            try:
                await self.start_voice()
                return
            except Exception as e:
                log.error("la voce non riparte dopo il modello omni (tentativo %d): %s", attempt, e)
                if attempt < self.VOICE_TRIES:
                    await asyncio.sleep(self.retry_s)
        if self.notify:
            self.notify("Avviso di sistema: dopo aver ascoltato i suoni la tua voce non è ripartita. Scrivilo "
                        "nella finestra: l'utente può riavviarti.")
        raise MediaError("la voce di Jarvis non è ripartita")


# ---------------------------------------------------------------------- motori


class Backend(Protocol):
    async def start(self) -> None: ...

    async def ask(self, audio: np.ndarray, frames: list[Frame], prompt: str) -> str: ...

    async def stop(self) -> None: ...


class AudioCppBackend:
    """Audio Flamingo su audio.cpp: un terzo processo del motore che già fa orecchie e voce."""

    def __init__(self, model: OmniModel, port: int = OMNI_PORT) -> None:
        from ..speech import engine

        self.model = model
        self.proc = engine.EngineProcess(engine.EngineSpec("omni", port, [
            {"id": "omni", "family": "audio_flamingo", "path": model.paths()[0].as_posix(), "task": "asr",
             "mode": "offline"}]))

    async def start(self) -> None:
        await self.proc.start(timeout=120)

    async def ask(self, audio: np.ndarray, frames: list[Frame], prompt: str) -> str:
        from ..speech.stt import transcribe_file

        # la domanda va nel campo "prompt" della richiesta (come nell'API di OpenAI): "instruct" e "instruction" sono
        # ignorati e il modello resta sulla sua istruzione di base, "trascrivi il parlato" (misurato il 29/10: sul
        # tuono "The spoken content of the audio is", con prompt "This is a recording of thunder.")
        r = await transcribe_file(self.proc.host, self.proc.port, "omni", audio, timeout=600,
                                  fields={"prompt": prompt, "max_tokens": "400"})
        return str(r.get("text") or "").strip()

    async def stop(self) -> None:
        await self.proc.stop()


class LlamaServerBackend:
    """Qwen2.5-Omni o LFM3-Omni su llama-server (llama.cpp): audio e fotogrammi nella stessa domanda."""

    def __init__(self, model: OmniModel, port: int = OMNI_PORT, exe: Path | None = None) -> None:
        self.model = model
        self.port = port
        self.exe = exe or paths.ENGINE_DIR / "llamacpp" / "llama-server.exe"
        self.proc: asyncio.subprocess.Process | None = None

    def command(self) -> list[str]:
        gguf, proj = self.model.paths()
        # una sola richiesta alla volta (-np 1) e 8192 di contesto (un minuto di audio e 4 fotogrammi ~3.800 token):
        # col predefinito (16384, più posti) Qwen2.5-Omni 7B riempiva la scheda video e andava 20 volte più lento
        return [str(self.exe), "-m", str(gguf), "--mmproj", str(proj), "-ngl", "99", "-c", "8192", "-np", "1",
                *self.model.server_args, "--host", "127.0.0.1", "--port", str(self.port), "--no-webui"]

    async def start(self) -> None:
        import aiohttp

        if not self.exe.is_file():
            raise MediaError(f"manca llama.cpp ({self.exe}): va scaricato per {self.model.title}")
        log_path = paths.logs_dir() / "engine_omni.log"
        fh = log_path.open("ab", buffering=0)
        try:
            self.proc = await asyncio.create_subprocess_exec(*self.command(), stdout=fh, stderr=fh,
                                                             creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            fh.close()  # il processo ha il suo
        winjob.adopt(self.proc.pid)
        deadline = time.monotonic() + 180
        async with aiohttp.ClientSession() as http:
            while time.monotonic() < deadline:
                if self.proc.returncode is not None:
                    raise MediaError(f"llama-server si è chiuso all'avvio (codice {self.proc.returncode}): vedi "
                                     f"{log_path}")
                with contextlib.suppress(aiohttp.ClientError, TimeoutError):
                    async with http.get(f"http://127.0.0.1:{self.port}/health",
                                        timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status == 200:  # 503 mentre carica il modello
                            return
                await asyncio.sleep(0.5)
        raise MediaError(f"{self.model.title} non è pronto dopo 3 minuti")

    async def ask(self, audio: np.ndarray, frames: list[Frame], prompt: str) -> str:
        import aiohttp

        from ..speech.stt import to_wav

        content: list[dict] = [{"type": "input_audio", "input_audio": {
            "data": base64.b64encode(to_wav(audio)).decode("ascii"), "format": "wav"}}]
        for f in frames if self.model.sees else []:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(f.jpeg).decode("ascii")}})
        content.append({"type": "text", "text": prompt})
        body = {"messages": [{"role": "user", "content": content}], "max_tokens": 400, "temperature": 0.2}
        async with aiohttp.ClientSession() as http, http.post(
                f"http://127.0.0.1:{self.port}/v1/chat/completions", json=body,
                timeout=aiohttp.ClientTimeout(total=600)) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise MediaError(f"{self.model.title}: HTTP {r.status}: {str(data)[:200]}")
        return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()

    async def stop(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        self.proc = None


def default_backend(model: OmniModel) -> Backend:
    return AudioCppBackend(model) if model.runtime == "audiocpp" else LlamaServerBackend(model)


# ---------------------------------------------------------------------- chi lo usa


class Omni:
    """Descrive musica, suoni e azioni di un audio (con i fotogrammi, se il modello vede), un minuto alla volta."""

    def __init__(self, choice: Callable[[], str], lease: GpuLease,
                 backend: Callable[[OmniModel], Backend] = default_backend) -> None:
        self.choice = choice  # media.omni, riletta a ogni richiesta
        self.lease = lease
        self.backend = backend
        self._busy = asyncio.Lock()

    def model(self) -> OmniModel:
        key = self.choice()
        if key == "spento" or key not in MODELS:
            raise MediaError("il modello per musica e suoni è spento (impostazione media.omni): per ora ho solo "
                             "le immagini e il parlato")
        m = MODELS[key]
        if missing := m.missing():
            raise MediaError(f"{m.title} non è ancora scaricato ({m.download_gb:g} GB da {m.repo}; manca "
                             f"{missing[0].name}): per ora ho solo le immagini e il parlato")
        return m

    async def describe(self, audio: np.ndarray, t0: float, frames: list[Frame], question: str = "") -> str:
        m = self.model()
        parts = windows(audio, t0)
        if not parts:
            return "solo silenzio"
        prompt = PROMPT + (PROMPT_FRAMES if m.sees and frames else "")
        if question.strip():
            prompt += f" The user also asks (answer it if the audio tells): {question.strip()}"
        lines = []
        async with self._busy, self.lease.borrow(m.ears_off_gpu):
            be = self.backend(m)
            t = time.perf_counter()
            try:
                await be.start()
                loaded = time.perf_counter() - t
                for a, b, piece in parts:
                    near = [f for f in frames if a <= f.t < b][:FRAMES_PER_WINDOW] if m.sees else []
                    text = await be.ask(piece, near, prompt)
                    lines.append(f"[{clock(a)}-{clock(b)}] {text or '(niente)'}")
            finally:
                await be.stop()
            log.info("%s: %d minuti descritti in %.1f s (caricamento %.1f s)", m.title, len(parts),
                     time.perf_counter() - t, loaded)
        return "\n".join(lines)

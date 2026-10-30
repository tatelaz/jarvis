"""Il primo avvio come procedura: cosa manca per partire, le scelte, il download, la fine.

Qui non c'è HTTP: la pagina arriva attraverso `server.py`, i test parlano direttamente con `Setup` con un PC,
uno scaricatore e un Claude Code inventati. Le scelte si salvano subito nella configurazione (lingua, cervello),
il resto alla fine: nome, avvio con Windows (lo applica Jarvis stesso al primo avvio, `autostart.reconcile`) e
la voce adatta al profilo.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .. import paths
from ..config import Config, ConfigStore
from . import catalog, pc
from .brains import ClaudeCode
from .download import Downloader, DownloadError

log = logging.getLogger("jarvis.setup")

LANGUAGES = ("it", "en")
PROVIDERS = ("claude", "opencode")  # quelli che funzionano su un PC nuovo senza altro da installare a mano
GROUPS = ("engine", "ears", "voice", "speakers", "memory", "turns")  # le righe della pagina, in quest'ordine
GROUP_OF = {"engine-cuda13": "engine", "engine-cuda12": "engine", "engine-cpu": "engine", "ears": "ears",
            "voice": "voice", "voice-small": "voice", "voice-fallback": "voice", "turns": "turns",
            "speakers": "speakers", "memory": "memory"}
TTS_FOR = {"voice": "qwen3-1.7b", "voice-small": "qwen3-0.6b"}
MAX_NAME = 40
SPEED_WINDOW_S = 6.0  # la velocità mostrata è la media degli ultimi secondi


class SetupError(ValueError):
    """Una richiesta che non si può soddisfare adesso. Il messaggio è un codice: la pagina ha i testi."""


def required_files(cfg: Config) -> list[Path]:
    """I file senza cui Jarvis non parte (tutti tra quelli che scarica il primo avvio)."""
    from ..identity.model import MODEL as SPEAKER_MODEL
    from ..memory.embed import MODEL_DIR as EMBED_DIR
    from ..speech.engine import MODEL_FILES

    models = [MODEL_FILES.get(k) for k in (cfg.speech.stt_model, cfg.speech.tts_model, "supertonic")]
    return [paths.AUDIOCPP_SERVER, *(paths.AUDIOCPP_MODELS / m for m in models if m), paths.SILERO_VAD,
            paths.SMART_TURN, SPEAKER_MODEL, EMBED_DIR / "onnx" / "model_q4.onnx", EMBED_DIR / "tokenizer.json"]


def missing_files(cfg: Config) -> list[Path]:
    return [p for p in required_files(cfg) if not p.exists()]


def clean_name(text: str) -> str:
    """Il nome scritto da chi installa: niente a capo né caratteri di controllo, spazi in ordine, non troppo lungo."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    return re.sub(r"\s+", " ", text).strip()[:MAX_NAME].strip()


class Setup:
    def __init__(self, store: ConfigStore, downloader: Downloader,
                 gather: Callable[[], pc.Facts] = lambda: pc.gather(paths.MODELS_DIR),
                 claude: ClaudeCode | None = None, voices_dir: Path | None = None, done_path: Path | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store, self.downloader, self.gather = store, downloader, gather
        self.claude = claude or ClaudeCode()
        self.voices_dir = voices_dir or paths.DATA_DIR / "voices"
        self.done_path = done_path or paths.DATA_DIR / "setup" / "fatto.json"
        self.clock = clock
        self.finished = asyncio.Event()  # finito bene: Jarvis può partire
        self.quit = asyncio.Event()  # chiuso prima della fine
        self.profile: pc.Profile | None = None
        self._task: asyncio.Task | None = None
        self._files: list[catalog.File] = []
        self._bytes: dict[str, int] = {}
        self._samples: list[tuple[float, int]] = []
        self._finished_bytes, self._current = 0, ""
        self._state, self._error, self._permanent = "idle", "", False

    # --- inizio ---

    def start_info(self) -> dict:
        c = self.store.config
        name = c.persona.user_name if c.persona.user_name != Config().persona.user_name else ""
        return {"lang": c.persona.language, "name": name, "autostart": c.system.autostart,
                "provider": c.brain.provider if c.brain.provider in PROVIDERS else "claude"}

    def set_language(self, lang: str) -> None:
        if lang not in LANGUAGES:
            raise SetupError("lingua_sconosciuta")
        self.store.set("persona.language", lang)

    # --- il PC ---

    async def check_pc(self) -> dict:
        facts = await asyncio.to_thread(self.gather)
        present = []
        for p in catalog.PACKAGES.values():
            for f in p.files:  # solo una stima: misura giusta = c'è (l'impronta la controlla il download)
                with contextlib.suppress(OSError, ValueError):
                    if self.downloader.target(f.dest).stat().st_size == f.size:
                        present.append(f.dest)
        self.profile = pc.choose(facts, present)
        best = max(facts.gpus, key=lambda g: (g.vendor == "nvidia", g.vram_mb), default=None)
        return {"facts": {"gpu": best and {"name": best.name, "vram_mb": best.vram_mb}, "ram_mb": facts.ram_mb,
                          "free_mb": facts.free_mb, "mics": facts.mics, "speakers": facts.speakers,
                          "online": facts.online},
                "profile": self.profile.public()}

    # --- il cervello ---

    async def brain_status(self) -> dict:
        claude = await asyncio.to_thread(self.claude.status)
        return {"claude": claude.public(), "provider": self.start_info()["provider"]}

    def set_brain(self, provider: str) -> None:
        if provider not in PROVIDERS:
            raise SetupError("cervello_sconosciuto")
        self.store.set("brain.provider", provider)

    async def claude_login(self) -> None:
        if not await asyncio.to_thread(self.claude.login):
            raise SetupError("claude_mancante")

    async def claude_install(self) -> None:
        await asyncio.to_thread(self.claude.install)

    # --- il download ---

    def plan(self) -> list[catalog.File]:
        if self.profile is None:
            raise SetupError("controllo_pc")
        if not self.profile.ok:
            raise SetupError(self.profile.blockers[0])
        return [f for p in catalog.plan(self.profile.engine, self.profile.voice) for f in p.files]

    async def start_download(self) -> dict:
        files = self.plan()
        if self._task is None or self._task.done():
            self._files = files
            self._bytes = {f.dest: 0 for f in files}
            self._samples, self._finished_bytes, self._current = [], 0, ""
            self._state, self._error, self._permanent = "running", "", False
            self._task = asyncio.create_task(self._download(files))
        return self.download_status()

    async def _download(self, files: list[catalog.File]) -> None:
        try:
            todo = await self.downloader.missing(files)
            waiting = {f.dest for f in todo}
            for f in files:
                if f.dest not in waiting:
                    self._bytes[f.dest] = f.size
            await self.downloader.run(todo, self._report)
        except DownloadError as e:
            self._state, self._error, self._permanent = "error", str(e), e.permanent
            log.warning("primo avvio: download fermo: %s", e)
        except asyncio.CancelledError:
            self._state = "idle"
            raise
        except Exception as e:  # rete, disco, permessi: la pagina lo dice e si può riprovare
            log.exception("primo avvio: download non riuscito")
            self._state, self._error, self._permanent = "error", f"errore inatteso: {e}", False
        else:
            for f in files:
                self._bytes[f.dest] = f.size
            self._state = "done"

    def _report(self, done: int, total: int, name: str) -> None:
        if name != self._current:
            if self._current:  # il file di prima è finito
                size = next(f.size for f in self._files if f.dest == self._current)
                self._bytes[self._current] = size
                self._finished_bytes += size
            self._current = name
        self._bytes[name] = max(0, done - self._finished_bytes)
        now = self.clock()
        self._samples.append((now, sum(self._bytes.values())))
        while len(self._samples) > 2 and now - self._samples[0][0] > SPEED_WINDOW_S:
            self._samples.pop(0)

    def download_status(self) -> dict:
        size = {f.dest: f.size for f in self._files}
        group = {f.dest: GROUP_OF[p.key] for p in catalog.PACKAGES.values() for f in p.files}
        groups = []
        for g in GROUPS:
            dests = [d for d in size if group[d] == g]
            if dests:
                groups.append({"key": g, "done": sum(min(self._bytes.get(d, 0), size[d]) for d in dests),
                               "total": sum(size[d] for d in dests)})
        speed = 0.0
        if len(self._samples) >= 2:
            (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
            speed = (b1 - b0) / (t1 - t0) if t1 > t0 else 0.0
        return {"state": self._state, "done": sum(g["done"] for g in groups),
                "total": sum(g["total"] for g in groups), "groups": groups, "speed": round(speed),
                "error": self._error, "permanent": self._permanent}

    async def cancel_download(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # --- la fine ---

    def finish(self, name: str, autostart: bool) -> dict:
        if self.profile is None:
            raise SetupError("controllo_pc")
        if self._state != "done":
            raise SetupError("download_incompleto")
        if name := clean_name(name):
            self.store.set("persona.user_name", name)
        self.store.set("speech.tts_model", TTS_FOR[self.profile.voice])
        self.store.set("system.autostart", bool(autostart))
        voice = self.voices_dir / self.store.config.speech.tts_voice / "voice.json"
        if not voice.exists():  # senza una voce Jarvis non parte: meglio dirlo qui che cadere all'avvio
            return {"ok": False, "problem": "voce_mancante"}
        self.done_path.parent.mkdir(parents=True, exist_ok=True)
        self.done_path.write_text(json.dumps({"profile": self.profile.key, "when": datetime.now().isoformat(
            timespec="seconds")}, indent=1), encoding="utf-8")
        self.finished.set()
        return {"ok": True}

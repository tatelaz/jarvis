"""Gestione dei processi audio.cpp: "orecchie" (STT) e "voce" (TTS) in due processi separati.

Separati perché un crash della sintesi non deve rendere Jarvis sordo, e viceversa. Ogni processo:
- riceve una configurazione generata qui (modelli, opzioni di memoria misurate, voce di Jarvis);
- è legato a Jarvis con un Job Object (muore con lui, niente orfani che occupano la VRAM);
- è sorvegliato: se cade viene riavviato con attese crescenti, e chi è in ascolto viene avvisato.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .. import paths, winjob
from .http import request_json

log = logging.getLogger("jarvis.engine")

# Arene di memoria per Parakeet: i valori predefiniti prenotano ~4,3 GB di memoria virtuale per un
# modello da 0,9 GB. Questi bastano per turni fino a 40 s (misurato: stesso WER, 3,7 GB di commit in meno).
PARAKEET_SESSION = {
    "parakeet_tdt.weight_context_mb": 64,
    "parakeet_tdt.encoder_graph_arena_mb": 128,
    "parakeet_tdt.decoder_graph_arena_mb": 16,
}

MODEL_FILES = {
    "parakeet-v3": "Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-q8_0.gguf",
    # moondream/parakeet-ultra convertito con audiocpp_gguf (--family parakeet_tdt --type q8_0); i file accessori
    # mancanti (processor/tokenizer/generation_config) sono quelli del Parakeet v3 di NVIDIA, identici
    "parakeet-ultra": "Parakeet-Ultra-GGUF/parakeet-ultra-q8_0.gguf",
    "qwen3-1.7b": "Qwen3-TTS-12Hz-1.7B-Base-GGUF/qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf",
    "qwen3-0.6b": "Qwen3-TTS-12Hz-0.6B-Base-GGUF/qwen3-tts-12hz-0.6b-base-q8_0.gguf",
    "omnivoice": "OmniVoice-GGUF/omnivoice-bf16.gguf",
    "supertonic": "Supertonic-3-GGUF/supertonic-3-orig.gguf",
}


class EngineError(RuntimeError):
    pass


@dataclass
class EngineSpec:
    name: str
    port: int
    models: list[dict]
    host: str = "127.0.0.1"
    max_loaded_models: int = 0
    extra: dict = field(default_factory=dict)

    def config(self) -> dict:
        cfg = {"host": self.host, "port": self.port, "backend": "cuda", "device": 0, "threads": 4,
               "lazy_load": True, "busy_timeout_ms": 60000, "models": self.models}
        if self.max_loaded_models:
            cfg["max_loaded_models"] = self.max_loaded_models
        cfg.update(self.extra)
        return cfg


def model_path(key: str) -> str:
    p = paths.AUDIOCPP_MODELS / MODEL_FILES[key]
    if not p.exists():
        raise EngineError(f"modello mancante: {p}")
    return p.as_posix()


def ears_spec(port: int, device: str = "cpu", threads: int = 6, model: str = "parakeet-ultra") -> EngineSpec:
    """Motore delle orecchie. Sul processore di proposito: vedi SpeechConfig.ears_device."""
    return EngineSpec("orecchie", port, [
        {"id": "stt", "family": "parakeet_tdt", "path": model_path(model), "task": "asr",
         "mode": "offline", "session_options": PARAKEET_SESSION},
    ], extra={"backend": device, "threads": threads})


def load_voice(name: str) -> dict:
    d = paths.DATA_DIR / "voices" / name
    meta = json.loads((d / "voice.json").read_text(encoding="utf-8"))
    meta["ref_path"] = (d / meta["ref"]).resolve().as_posix()
    meta["dir"] = d
    return meta


def voice_spec(port: int, voice: dict, tts_model: str) -> EngineSpec:
    """Motore della voce: il modello principale clona la voce di Jarvis; Supertonic è il ripiego
    istantaneo (voce preimpostata) se il principale non risponde."""
    family = {"qwen3-1.7b": "qwen3_tts", "qwen3-0.6b": "qwen3_tts", "omnivoice": "omnivoice"}[tts_model]
    preset = {"voice_ref": voice["ref_path"], "reference_text": voice["ref_text"]}
    return EngineSpec("voce", port, [
        {"id": "tts", "family": family, "path": model_path(tts_model), "task": "tts", "mode": "offline",
         "voice_presets": {"jarvis": preset}, "default_voice_preset": "jarvis"},
        {"id": "tts-fallback", "family": "supertonic", "path": model_path("supertonic"), "task": "tts",
         "mode": "offline", "default_voice_preset": {"voice_id": "M1"}},
    ], max_loaded_models=1)


def _find_orphans(port: int) -> list[psutil.Process]:
    """Processi audio.cpp rimasti in ascolto sulla nostra porta (es. dopo un crash brutale)."""
    found = []
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
            with contextlib.suppress(psutil.Error):
                p = psutil.Process(c.pid)
                if Path(p.exe()).resolve() == paths.AUDIOCPP_SERVER.resolve():
                    found.append(p)
                else:
                    raise EngineError(f"la porta {port} è occupata da un altro programma: {p.name()} (pid {p.pid})")
    return found


EventCallback = Callable[[str, dict], Awaitable[None] | None]


class EngineProcess:
    def __init__(self, spec: EngineSpec, on_event: EventCallback | None = None) -> None:
        self.spec = spec
        self.on_event = on_event
        self.proc: asyncio.subprocess.Process | None = None
        self.restarts = 0
        self.started_at = 0.0
        self._stopping = False
        self._watch_task: asyncio.Task | None = None
        self.ready = asyncio.Event()

    @property
    def host(self) -> str:
        return self.spec.host

    @property
    def port(self) -> int:
        return self.spec.port

    async def _emit(self, kind: str, **data) -> None:
        if self.on_event:
            r = self.on_event(kind, {"engine": self.spec.name, **data})
            if asyncio.iscoroutine(r):
                await r

    async def start(self, timeout: float = 30.0) -> None:
        self._stopping = False  # anche dopo uno stop voluto (riposo) il motore può ripartire
        if not paths.AUDIOCPP_SERVER.exists():
            raise EngineError(f"motore audio.cpp non trovato in {paths.AUDIOCPP_SERVER}")
        for p in _find_orphans(self.port):
            log.warning("chiudo un motore %s rimasto orfano (pid %s)", self.spec.name, p.pid)
            p.kill()
            p.wait(5)
        cfg_dir = paths.DATA_DIR / "engine"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / f"{self.spec.name}.json"
        cfg_path.write_text(json.dumps(self.spec.config(), indent=1, ensure_ascii=False), encoding="utf-8")
        log_path = paths.logs_dir() / f"engine_{self.spec.name}.log"
        # il file resta aperto finché vive il processo (è il suo stdout/stderr)
        self._log_fh = log_path.open("ab", buffering=0)
        self._log_fh.write(f"\n===== avvio {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
        self.proc = await asyncio.create_subprocess_exec(
            str(paths.AUDIOCPP_SERVER), "--config", str(cfg_path), "--no-ui",
            stdout=self._log_fh, stderr=self._log_fh, cwd=str(paths.AUDIOCPP_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        winjob.adopt(self.proc.pid)
        self.started_at = time.monotonic()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                raise EngineError(f"il motore {self.spec.name} si è chiuso all'avvio (codice {self.proc.returncode}); "
                                  f"vedi {log_path}")
            try:
                await request_json(self.host, self.port, "GET", "/v1/models", timeout=2)
                self.ready.set()
                log.info("motore %s pronto su %s:%s (pid %s)", self.spec.name, self.host, self.port, self.proc.pid)
                await self._emit("engine_ready", pid=self.proc.pid)
                return
            except (OSError, TimeoutError):
                await asyncio.sleep(0.25)
        raise EngineError(f"il motore {self.spec.name} non risponde dopo {timeout:.0f} s")

    def supervise(self) -> None:
        """Avvia la sorveglianza: riavvio automatico se il processo muore."""
        if self._watch_task is None:
            self._watch_task = asyncio.create_task(self._watch(), name=f"watch-{self.spec.name}")

    async def _watch(self) -> None:
        backoff = 1.0
        while not self._stopping:
            assert self.proc is not None
            code = await self.proc.wait()
            if self._stopping:
                return
            self.ready.clear()
            self.restarts += 1
            lived = time.monotonic() - self.started_at
            if lived > 60:
                backoff = 1.0
            log.error("motore %s terminato (codice %s) dopo %.0f s: riavvio tra %.0f s", self.spec.name, code, lived,
                      backoff)
            await self._emit("engine_crashed", code=code, restart_in=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            try:
                await self.start()
                await self._emit("engine_recovered", restarts=self.restarts)
            except EngineError as e:
                log.error("riavvio del motore %s fallito: %s", self.spec.name, e)

    async def health(self) -> bool:
        if self.proc is None or self.proc.returncode is not None:
            return False
        try:
            await request_json(self.host, self.port, "GET", "/v1/models", timeout=3)
            return True
        except (OSError, TimeoutError):
            return False

    async def unload_all(self) -> None:
        with contextlib.suppress(OSError, TimeoutError):
            await request_json(self.host, self.port, "POST", "/v1/tasks/unload_all_models", timeout=10)

    async def stop(self) -> None:
        self._stopping = True
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None  # alla ripartenza la sorveglianza si riaccende
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        self.ready.clear()
        if getattr(self, "_log_fh", None):
            self._log_fh.close()

    async def kill_for_test(self) -> None:
        """Solo per i test di auto-riparazione: simula un crash."""
        assert self.proc is not None
        self.proc.kill()

"""OpenCode messo insieme per Jarvis: server, ponte degli strumenti, scelta del modello gratis, esame, sotto-agenti.

Tutto si accende alla prima richiesta (quando Claude è esaurito, o se l'utente sceglie "cervello opencode") e si
spegne dopo mezz'ora senza uso. Le impostazioni (sezione brain, opencode_*) si rileggono a ogni uso: valgono subito.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from . import paths
from .brain.opencode import TEXT_AGENT, ApiKeyStore, ModelProblem, OpenCodeBrain, OpenCodeHub
from .brain.tools import ToolRegistry
from .brain.zen import Examiner, ModelPicker
from .config import ConfigStore
from .tasks.hands import DesktopLease
from .tasks.manager import Task
from .tasks.opencode_worker import ConfirmDesk, OpenCodeWorker, opencode_hands, worker_tools
from .tasks.safety import SafetyPolicy

log = logging.getLogger("jarvis.opencode")

EXAM_IDLE_S = 120.0  # l'esame parte solo dopo due minuti di quiete (mai mentre si parla con Jarvis)
EXAM_RETRY_S = 3600.0  # al più un tentativo d'esame all'ora (OpenCode spento, limite gratuito, rete giù)


class OpenCodeKit:
    def __init__(self, store: ConfigStore, base: Path | None = None, hub: OpenCodeHub | None = None,
                 adopt: Callable[[int], bool] | None = None) -> None:
        self.store = store
        cfg = store.config
        self.base = base or paths.DATA_DIR / "opencode"
        self.picker = ModelPicker(self.base / "esame.json", fixed=lambda: self.store.config.brain.opencode_model,
                                  privacy=lambda: self.store.config.brain.opencode_privacy)
        self.hub = hub or OpenCodeHub(self.base, paths.logs_dir() / "opencode.log", self.picker,
                                      keys=ApiKeyStore(self.base), adopt=adopt,
                                      extra_mcp=lambda: self._hands()[0], worker_tools=worker_tools)
        self.examiner = Examiner(self.hub, self.picker, cfg.persona.user_name)
        self.desk = ConfirmDesk()
        self.hub.mount("esame", self.examiner.registry)
        self.hub.mount("lavoro", self._worker_registry(), self.desk.intercept)
        self._exam_task: asyncio.Task | None = None
        self._last_try = float("-inf")  # ultimo esame partito da solo

    # ------------------------------------------------------------ impostazioni

    @property
    def enabled(self) -> bool:
        return self.store.config.brain.opencode

    def _hands(self) -> tuple[dict, list[str]]:
        cfg = self.store.config
        return opencode_hands(cfg.hands, cfg.persona.user_name)

    def mount_front(self, tools: ToolRegistry) -> None:
        """Gli strumenti di Jarvis a voce, per il cervello su OpenCode."""
        self.hub.mount("jarvis", tools)

    def _on_problem(self, model: str, problem: ModelProblem) -> None:
        self.picker.mark_problem(model, problem)

    # ------------------------------------------------------------ cervelli e sotto-agenti

    def brain(self, persona: str, tag: str = "") -> OpenCodeBrain:
        return OpenCodeBrain(persona, self.hub, self.picker.ranking, enabled=lambda: self.enabled,
                             name=f"opencode{tag}", on_model_problem=self._on_problem)

    def text_brain(self, system: str) -> OpenCodeBrain:
        """Un cervello senza strumenti (il custode della memoria): risponde e basta."""
        return OpenCodeBrain(system, self.hub, self.picker.ranking, enabled=lambda: self.enabled, name="custode-opencode",
                             agent=TEXT_AGENT, prefix="nessuno_", on_model_problem=self._on_problem, ephemeral=True)

    def worker(self, task: Task, safety: SafetyPolicy, desktop: DesktopLease | None, note: str = "") -> OpenCodeWorker:
        cfg = self.store.config
        fixed = cfg.brain.opencode_worker_model.strip()

        def models() -> list[str]:
            ranked = self.picker.ranking()
            return ([fixed] + [m for m in ranked if m != fixed]) if fixed and fixed != "auto" else ranked

        return OpenCodeWorker(task, cfg.persona.user_name, self.hub, safety, models, self.desk,
                              guide=self._hands()[1], desktop=desktop, note=note)

    @staticmethod
    def _worker_registry() -> ToolRegistry:
        """Gli strumenti `lavoro_*` dei sotto-agenti. Il lavoro vero lo fanno ConfirmDesk e il sotto-agente."""
        from .brain.tools import schema

        reg = ToolRegistry()
        reg.register("riferisci", "Manda a JARVIS un aggiornamento importante sul compito (una frase).",
                     schema({"testo": {"type": "string"}}, ["testo"]), read_only=True)(lambda _a: "ricevuto")
        reg.register("chiedi_conferma", "Chiede all'utente il permesso per un'azione delicata. Restituisce APPROVATA "
                     "o NEGATA: rispettala.",
                     schema({"azione": {"type": "string", "description": "cosa vuoi fare, in breve"},
                             "motivo": {"type": "string"}}, ["azione"]))(lambda _a: "NEGATA")
        return reg

    # ------------------------------------------------------------ manutenzione (ogni minuto, da app.py)

    async def tick(self, quiet_for: float, claude_exhausted: bool) -> None:
        """Spegne il server inutilizzato; rifà l'esame quando serve, solo in silenzio e senza rubare l'uso gratuito
        quando OpenCode sta facendo da cervello (Claude esaurito), tranne la prima volta."""
        with contextlib.suppress(Exception):
            await self.hub.stop_if_idle()
        cfg = self.store.config.brain
        if not cfg.opencode or quiet_for < EXAM_IDLE_S or self.examiner.running:
            return
        never = not self.picker.results
        if not self.picker.exam_due(cfg.opencode_exam_hours) or (claude_exhausted and not never):
            return
        if time.monotonic() - self._last_try < EXAM_RETRY_S or not getattr(self.hub, "installed", lambda: True)():
            return
        self._last_try = time.monotonic()
        self.start_exam()

    def start_exam(self) -> str:
        if self.examiner.running or (self._exam_task is not None and not self._exam_task.done()):
            return "l'esame dei modelli gratis è già in corso"
        self._exam_task = asyncio.ensure_future(self._exam())
        return "esame dei modelli gratis avviato: ci vuole circa un minuto"

    async def _exam(self) -> None:
        t = time.perf_counter()
        try:
            results = await self.examiner.run()
            log.info("esame dei modelli gratis: %d esaminati in %.0f s, in uso %s", len(results),
                     time.perf_counter() - t, self.picker.best())
        except Exception as e:  # OpenCode assente o spento: si riproverà al prossimo giro
            log.warning("esame dei modelli gratis non riuscito: %s", e)

    def describe(self) -> str:
        cfg = self.store.config.brain
        state = "acceso" if cfg.opencode else "spento nelle impostazioni"
        key = "con la chiave dell'utente" if self.hub.keys is not None and self.hub.keys.get() else "senza chiave"
        return f"OpenCode {state}, {key}. " + self.picker.describe()

    async def stop(self) -> None:
        if self._exam_task is not None:
            self._exam_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._exam_task
        await self.hub.stop()

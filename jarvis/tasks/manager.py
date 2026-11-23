"""Compiti in background affidati a sotto-agenti usa e getta.

Ogni compito ha un obiettivo, uno stato, un diario di avanzamento e, se serve, una domanda in
sospeso (conferma di un'azione delicata). Il gestore:
- limita i compiti in parallelo (gli altri aspettano in coda);
- inoltra i messaggi dell'utente al sotto-agente mentre lavora;
- avvisa Jarvis quando c'è qualcosa da dire (fine, errore, richiesta di conferma), non per ogni passo;
- se nessuno risponde a una richiesta di conferma, l'azione viene negata (mai un sì implicito).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..core.bus import Bus

log = logging.getLogger("jarvis.tasks")

ACTIVE = ("in coda", "in corso", "attende conferma")


@dataclass
class Question:
    id: int
    text: str
    future: asyncio.Future
    asked_at: float = field(default_factory=time.time)


@dataclass
class Task:
    id: int
    title: str
    goal: str
    cwd: Path
    status: str = "in coda"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    progress: list[str] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    question: Question | None = None
    inbox: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    kind: str = "lavoro"  # "lavoro" (qualunque compito), "miglioria" (modifica al codice di Jarvis) o "aggiornamento"

    def summary(self) -> str:
        age = int(time.time() - (self.started or self.created))
        mins = f"{age // 60} min" if age >= 60 else f"{age} s"
        last = f"; ultimo aggiornamento: {self.progress[-1]}" if self.progress else ""
        q = f"; ATTENDE CONFERMA: {self.question.text}" if self.question else ""
        return f"#{self.id} \"{self.title}\" — {self.status} da {mins}{last}{q}"

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "goal": self.goal, "status": self.status,
                "progress": self.progress[-8:], "result": self.result, "error": self.error,
                "question": self.question.text if self.question else None, "created": self.created,
                "started": self.started, "finished": self.finished, "cwd": str(self.cwd), "kind": self.kind}


class Worker(Protocol):
    async def run(self, task: Task, hooks: WorkerHooks) -> str: ...
    async def send(self, text: str) -> None: ...
    async def cancel(self) -> None: ...


@dataclass
class WorkerHooks:
    """Ciò che il gestore offre al sotto-agente."""
    progress: Callable[[str], None]
    confirm: Callable[[str], Awaitable[bool]]


WorkerFactory = Callable[[Task], Worker]


class TaskManager:
    def __init__(self, bus: Bus, notify: Callable[[str], None], worker_factory: WorkerFactory, workspace: Path,
                 max_parallel: int = 3, confirm_timeout_s: float = 300.0) -> None:
        self.bus = bus
        self.notify = notify
        self.worker_factory = worker_factory
        self.workspace = workspace
        self.max_parallel = max_parallel
        self.confirm_timeout_s = confirm_timeout_s
        self.tasks: dict[int, Task] = {}
        self._workers: dict[int, Worker] = {}
        self._runners: dict[int, asyncio.Task] = {}
        self._ids = itertools.count(1)
        self._qids = itertools.count(1)
        self._slots = asyncio.Semaphore(max_parallel)

    # ------------------------------------------------------------ comandi (usati dagli strumenti di Jarvis)

    def spawn(self, goal: str, title: str | None = None, cwd: str | None = None, kind: str = "lavoro") -> Task:
        tid = next(self._ids)
        folder = Path(cwd).expanduser() if cwd else self.workspace / f"compito_{tid:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        task = Task(tid, (title or goal)[:70], goal, folder, kind=kind)
        self.tasks[tid] = task
        self._runners[tid] = asyncio.ensure_future(self._run(task))
        self._emit(task)
        return task

    async def message(self, tid: int, text: str) -> str:
        task = self._get(tid)
        if task.status not in ACTIVE:
            return f"Errore: il compito #{tid} è {task.status}, non accetta più messaggi"
        worker = self._workers.get(tid)
        if worker is None:  # ancora in coda: il messaggio entrerà all'avvio
            task.inbox.append(text)
            return f"messaggio accodato per il compito #{tid} (partirà a breve)"
        await worker.send(text)
        task.progress.append(f"[istruzione dell'utente] {text}")
        self._emit(task)
        return f"messaggio inoltrato al compito #{tid}"

    async def cancel(self, tid: int) -> str:
        task = self._get(tid)
        if task.status not in ACTIVE:
            return f"il compito #{tid} era già {task.status}"
        if task.question and not task.question.future.done():
            task.question.future.set_result(False)
        worker = self._workers.get(tid)
        if worker is not None:
            with contextlib.suppress(Exception):
                await worker.cancel()
        runner = self._runners.get(tid)
        if runner and not runner.done():
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await runner
        task.status, task.finished = "annullato", time.time()
        self._emit(task)
        return f"compito #{tid} annullato"

    def answer(self, tid: int, approved: bool, note: str = "") -> str:
        task = self._get(tid)
        if not task.question or task.question.future.done():
            return f"Errore: il compito #{tid} non ha domande in sospeso"
        task.question.future.set_result(approved)
        task.progress.append(f"[conferma] {'approvata' if approved else 'negata'}{': ' + note if note else ''}")
        return f"risposta inoltrata al compito #{tid}: {'approvata' if approved else 'negata'}"

    def summary(self, include_done_s: float = 600) -> str:
        now = time.time()
        rows = [t.summary() for t in self.tasks.values()
                if t.status in ACTIVE or (t.finished and now - t.finished < include_done_s)]
        return "\n".join(rows)

    def _get(self, tid: int) -> Task:
        try:
            return self.tasks[int(tid)]
        except (KeyError, ValueError):
            raise KeyError(f"nessun compito #{tid}") from None

    def _emit(self, task: Task) -> None:
        self.bus.emit("task", **task.public())

    # ------------------------------------------------------------ esecuzione

    async def _run(self, task: Task) -> None:
        async with self._slots:
            if task.status == "annullato":
                return
            task.status, task.started = "in corso", time.time()
            self._emit(task)
            worker = self.worker_factory(task)
            self._workers[task.id] = worker
            hooks = WorkerHooks(progress=lambda text: self._on_progress(task, text),
                                confirm=lambda text: self._ask(task, text))
            try:
                for text in task.inbox:  # messaggi arrivati mentre era in coda
                    task.goal += f"\n\nAggiunta dell'utente: {text}"
                task.inbox.clear()
                result = await worker.run(task, hooks)
                task.status, task.result = "completato", result
                self.notify(f"Il compito #{task.id} \"{task.title}\" è completato. Esito: {result}")
            except asyncio.CancelledError:
                task.status = "annullato"
                raise
            except Exception as e:
                log.exception("compito #%s fallito", task.id)
                task.status, task.error = "fallito", f"{type(e).__name__}: {e}"
                self.notify(f"Il compito #{task.id} \"{task.title}\" è fallito: {task.error}")
            finally:
                task.finished = time.time()
                self._workers.pop(task.id, None)
                self._emit(task)

    def _on_progress(self, task: Task, text: str) -> None:
        task.progress.append(text)
        self._emit(task)

    async def _ask(self, task: Task, text: str) -> bool:
        """Domanda di conferma: passa da Jarvis all'utente e torna con un sì o un no."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        task.question = Question(next(self._qids), text, fut)
        prev, task.status = task.status, "attende conferma"
        self._emit(task)
        self.notify(f"Il compito #{task.id} \"{task.title}\" chiede conferma prima di procedere: {text}. "
                    f"Chiedi all'utente e rispondi con conferma_compito.")
        try:
            return await asyncio.wait_for(fut, self.confirm_timeout_s)
        except TimeoutError:
            task.progress.append("[conferma] nessuna risposta: azione negata")
            return False
        finally:
            task.question = None
            if task.status == "attende conferma":
                task.status = prev
            self._emit(task)

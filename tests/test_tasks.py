"""Gestore dei compiti con sotto-agenti finti: ciclo di vita, messaggi, conferme, coda, errori."""

import asyncio

import pytest

from jarvis.core.bus import Bus
from jarvis.tasks.manager import TaskManager


class FakeWorker:
    def __init__(self, task, script):
        self.task = task
        self.script = script
        self.received = []
        self.cancelled = False

    async def run(self, task, hooks):
        return await self.script(self, task, hooks)

    async def send(self, text):
        self.received.append(text)

    async def cancel(self):
        self.cancelled = True


WORKSPACE: list = []  # la cartella temporanea di pytest del test in corso (si pulisce da sola)


@pytest.fixture(autouse=True)
def workspace(tmp_path):
    """Prima c'era tempfile.mkdtemp(), mai cancellata: con TEMP sulla cartella del progetto lasciava tmp*/ lì."""
    WORKSPACE[:] = [tmp_path]
    yield tmp_path
    WORKSPACE.clear()


def make(script, **kw):
    notes, workers = [], []
    bus = Bus()

    def factory(task):
        w = FakeWorker(task, script)
        workers.append(w)
        return w

    tm = TaskManager(bus, notes.append, factory, WORKSPACE[0], **kw)
    return tm, notes, workers


async def wait_for(cond, timeout=3):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condizione non raggiunta")


async def test_task_completes_and_jarvis_is_notified_once():
    async def script(w, task, hooks):
        hooks.progress("passo 1")
        hooks.progress("passo 2")
        return "Ho creato il report sul desktop."

    tm, notes, _ = make(script)
    t = tm.spawn("prepara un report", "report")
    await wait_for(lambda: t.status == "completato")
    assert t.result == "Ho creato il report sul desktop."
    assert len(notes) == 1 and "#1" in notes[0] and "completato" in notes[0]  # i passi non disturbano a voce
    assert t.progress == ["passo 1", "passo 2"]
    assert "report" not in tm.summary(include_done_s=0)
    assert "completato" in tm.summary()


async def test_messages_reach_the_running_worker():
    gate = asyncio.Event()

    async def script(w, task, hooks):
        await gate.wait()
        return "ok " + ",".join(w.received)

    tm, _, workers = make(script)
    t = tm.spawn("compito lungo")
    await wait_for(lambda: workers)
    out = await tm.message(t.id, "aggiungi anche la parte B")
    assert "inoltrato" in out
    gate.set()
    await wait_for(lambda: t.status == "completato")
    assert t.result == "ok aggiungi anche la parte B"


async def test_confirmation_approved_denied_and_timeout():
    answers = []

    async def script(w, task, hooks):
        answers.append(await hooks.confirm("cancellare la cartella build"))
        return "fine"

    tm, notes, _ = make(script, confirm_timeout_s=0.3)
    t = tm.spawn("pulizia")
    await wait_for(lambda: t.status == "attende conferma")
    assert "chiede conferma" in notes[-1] and "cancellare la cartella build" in tm.summary()
    assert "approvata" in tm.answer(t.id, True)
    await wait_for(lambda: t.status == "completato")
    t2 = tm.spawn("pulizia 2")
    await wait_for(lambda: t2.status == "attende conferma")
    tm.answer(t2.id, False)
    await wait_for(lambda: t2.status == "completato")
    t3 = tm.spawn("pulizia 3")  # nessuno risponde: si nega
    await wait_for(lambda: t3.status == "completato", timeout=3)
    assert answers == [True, False, False]
    assert any("nessuna risposta" in p for p in t3.progress)
    assert tm.answer(t3.id, True).startswith("Errore")


async def test_parallel_limit_queues_extra_tasks():
    gate = asyncio.Event()

    async def script(w, task, hooks):
        await gate.wait()
        return "ok"

    tm, _, workers = make(script, max_parallel=2)
    ts = [tm.spawn(f"compito {i}") for i in range(3)]
    await wait_for(lambda: len(workers) == 2)
    await asyncio.sleep(0.1)
    assert [t.status for t in ts] == ["in corso", "in corso", "in coda"]
    msg = await tm.message(ts[2].id, "fai presto")  # accodato per quando partirà
    assert "accodato" in msg
    gate.set()
    await wait_for(lambda: all(t.status == "completato" for t in ts))
    assert "Aggiunta dell'utente: fai presto" in ts[2].goal


async def test_cancel_running_task():
    async def script(w, task, hooks):
        await asyncio.sleep(30)

    tm, notes, workers = make(script)
    t = tm.spawn("lunghissimo")
    await wait_for(lambda: workers)
    assert "annullato" in await tm.cancel(t.id)
    assert t.status == "annullato" and workers[0].cancelled
    assert "già annullato" in await tm.cancel(t.id)
    assert (await tm.message(t.id, "x")).startswith("Errore")
    assert notes == []  # annullare non genera un "è fallito"


async def test_worker_crash_is_reported():
    async def script(w, task, hooks):
        raise RuntimeError("API in errore")

    tm, notes, _ = make(script)
    t = tm.spawn("compito sfortunato")
    await wait_for(lambda: t.status == "fallito")
    assert "API in errore" in t.error and "fallito" in notes[0]


async def test_unknown_task_id():
    tm, _, _ = make(None)
    with pytest.raises(KeyError):
        await tm.message(42, "ciao")

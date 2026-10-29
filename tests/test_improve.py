"""Auto-miglioramento spontaneo: diario degli errori, proposte rare, un no per sempre (regole di Andrea del 7/10)."""

from types import SimpleNamespace

import pytest
from test_everyday_tools import FakeTasks

from jarvis.config import Config, ConfigStore
from jarvis.core.bus import Bus
from jarvis.improve.diary import Diary, Entry, Recorder, crash_entries, norm
from jarvis.memory.store import Memory
from jarvis.tools.front import Services, build_front_tools


def tool_failed(name="apri", why="Errore: app non trovata: Spotifi"):
    return {"type": "tool_result", "name": name, "ok": False, "preview": why}


def test_signature_ignores_numbers_and_paths():
    assert norm(r"Errore: file C:\Users\Andrea\a.txt non trovato (riga 12)") == \
        norm(r"Errore: file D:\altro\b.txt non trovato (riga 99)")


def test_only_errors_repeated_three_times_become_proposals():
    d = Diary()
    r = Recorder(d)
    r.handle(tool_failed())
    r.handle(tool_failed())
    assert d.candidates() == []  # due volte non bastano: niente proposte per un caso isolato
    # stessa firma (numeri a parte), e come arriva davvero da Claude
    r.handle(tool_failed(why="[{'type': 'text', 'text': 'Errore: app non trovata: Spotifi 2'}]"))
    found = d.candidates()
    assert len(found) == 1 and found[0].count == 3 and "«apri»" in found[0].title
    assert found[0].examples[0] == "apri: Errore: app non trovata: Spotifi 2"  # leggibile, senza l'involucro


def test_problems_outside_jarvis_are_not_counted():
    d = Diary()
    r = Recorder(d)
    for _ in range(5):
        r.handle({"type": "error", "where": "brain", "message": "rate_limit"})
        r.handle(tool_failed("WebFetch", "connection timeout"))
        r.handle({"type": "tool_result", "name": "apri", "ok": True, "preview": "aperto"})
    assert d.candidates() == []


def test_corrections_right_after_a_reply_are_noted_with_what_was_done():
    d = Diary()
    r = Recorder(d, "Andrea")
    t = 1000.0
    for _ in range(3):
        r.handle({"type": "tool", "name": "apri"}, t)
        r.handle({"type": "jarvis_done", "text": "Ho aperto Spotify."}, t + 1)
        r.handle({"type": "user", "text": "No, intendevo il sito di Spotify", "owner": True}, t + 5)
        t += 100
    r.handle({"type": "user", "text": "No, intendevo un'altra cosa", "owner": True}, t + 500)  # troppo dopo
    r.handle({"type": "jarvis_done", "text": "Certo."}, t + 600)
    r.handle({"type": "user", "text": "No, intendevo altro", "owner": False}, t + 601)  # un ospite non corregge
    r.handle({"type": "jarvis_done", "text": "Ecco."}, t + 700)
    r.handle({"type": "user", "text": "Grazie, perfetto", "owner": True}, t + 701)  # non è una correzione
    found = d.candidates(now=t + 800)
    assert len(found) == 1 and found[0].count == 3
    assert "dopo lo strumento «apri»" in found[0].title and "intendevo il sito" in found[0].examples[0]


def test_a_failed_task_counts_once_even_if_reported_twice():
    d = Diary()
    r = Recorder(d)
    ev = {"type": "task", "id": 7, "status": "fallito", "title": "Foto", "error": "RuntimeError: niente schede"}
    r.handle(ev)
    r.handle(ev)  # il gestore manda l'evento più volte
    assert d.count("compito:" + norm("RuntimeError: niente schede")) == 1


def test_at_most_three_proposals_and_a_no_is_forever():
    d = Diary()
    for k in range(5):
        for _ in range(3 + k):
            d.note(Entry("strumento", f"k{k}", f"errore {k}", "esempio"))
    assert [p.key for p in d.candidates()] == ["k4", "k3", "k2"]  # le più frequenti, al massimo 3
    d.decide("k4", "rifiutata")
    for _ in range(10):  # anche se continua a succedere
        d.note(Entry("strumento", "k4", "errore 4", "esempio"))
    assert "k4" not in [p.key for p in d.candidates(limit=50)]


def test_one_summary_per_period_and_it_survives_restarts(tmp_path):
    day, now = 86400, 1_000_000.0
    d = Diary(tmp_path / "d.db")
    assert not d.due("settimana", now)  # primo avvio: si comincia a contare da adesso
    assert not d.due("settimana", now + 6 * day)
    assert d.due("settimana", now + 7 * day)
    d.summary_done(now + 7 * day)
    assert not d.due("settimana", now + 8 * day) and d.due("giorno", now + 8 * day)
    assert not d.due("mai", now + 100 * day)
    d.close()
    assert not Diary(tmp_path / "d.db").due("settimana", now + 8 * day)


def test_crash_reports_become_entries(tmp_path):
    a = tmp_path / "1.txt"
    a.write_text("motivo: exit\ncodice d'uscita: 1\n\n--- ultime righe ---\nTraceback\nRuntimeError: boom 0x12\n",
                 encoding="utf-8")
    b = tmp_path / "2.txt"
    b.write_text("motivo: exit\ncodice d'uscita: 1\nora: ieri\n", encoding="utf-8")  # caduta senza messaggio
    first, second = crash_entries([a, b])
    assert first.key == "caduta:" + norm("RuntimeError: boom 0x12")
    assert second.key == "caduta:" + norm("motivo: exit, codice d'uscita: 1")


# ------------------------------------------------------------ strumenti


@pytest.fixture
def tools(tmp_path):
    mem = Memory(tmp_path / "m.db")
    s = Services(Bus(), ConfigStore(tmp_path / "config.yaml"), mem, tasks=FakeTasks(), diary=Diary())
    yield build_front_tools(s), s
    mem.close()


async def test_proposals_on_request_and_answers(tools):
    reg, s = tools
    d = s.diary
    assert "Nessuna proposta" in await reg.call("proposte", {})
    for _ in range(3):
        d.note(Entry("strumento", "a", "lo strumento «apri» non riesce", "apri: app non trovata"))
    for _ in range(4):
        d.note(Entry("correzione", "b", "Andrea mi corregge spesso dopo la risposta", "esempio"))
    text = await reg.call("proposte", {})
    assert text.startswith("1) Andrea mi corregge") and "2) lo strumento «apri» non riesce: 3 volte" in text
    assert "non la proporrò più" in await reg.call("rispondi_proposta", {"numero": 1, "accetta": False})
    assert d.decision("b") == "rifiutata"
    out = await reg.call("rispondi_proposta", {"numero": 2, "accetta": True})
    assert "miglioria #1" in out and d.decision("a") == "accettata"
    _, goal, _, _, kind = s.tasks.calls[-1]
    assert kind == "miglioria" and "app non trovata" in goal and "3 volte" in goal  # le prove vere dal diario
    assert (await reg.call("rispondi_proposta", {"numero": 9, "accetta": True})).startswith("Errore")
    assert (await reg.call("rispondi_proposta", {"numero": 2, "accetta": True})).startswith("Errore")  # già decisa


async def test_frequency_can_be_said_in_plain_words(tmp_path):
    store, mem = ConfigStore(tmp_path / "config.yaml"), Memory(tmp_path / "m.db")
    reg = build_front_tools(Services(Bus(), store, mem))
    out = await reg.call("impostazione", {"chiave": "system.proposals", "valore": "ogni giorno"})
    assert store.get("system.proposals") == "giorno" and "vale subito" in out
    await reg.call("impostazione", {"chiave": "system.proposals", "valore": "mai più"})
    assert store.get("system.proposals") == "mai"
    mem.close()


def test_crashes_count_from_the_first_start_onwards(tmp_path, monkeypatch):
    import os
    import time

    from jarvis.app import paths

    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)
    (tmp_path / "cadute").mkdir()
    old = tmp_path / "cadute" / "vecchia.txt"
    old.write_text("motivo: hang\ncodice d'uscita: 1\n", encoding="utf-8")
    j = quiet_app()
    j._note_crashes()  # primo avvio: le cadute di prima del diario non contano
    assert j.diary.count("caduta:" + norm("motivo: hang, codice d'uscita: 1")) == 0
    new = tmp_path / "cadute" / "nuova.txt"
    new.write_text("motivo: exit\ncodice d'uscita: 1\n\nRuntimeError: boom\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(new, (later, later))
    j._note_crashes()
    assert j.diary.count("caduta:" + norm("RuntimeError: boom")) == 1
    j._note_crashes()  # già vista: non si conta due volte
    assert j.diary.count("caduta:" + norm("RuntimeError: boom")) == 1


# ------------------------------------------------------------ il momento giusto


def quiet_app():
    from jarvis.app import Jarvis

    j = Jarvis.__new__(Jarvis)
    j.cfg, j.resting, j.tasks = Config(), False, None
    j.diary = Diary()
    j.recorder = Recorder(j.diary)
    notes = []
    j.conv = SimpleNamespace(jarvis_active=False, thinking=False, notify=notes.append, notes=notes)
    return j


def test_proposals_wait_for_a_quiet_moment_with_the_owner_there():
    j, day, now = quiet_app(), 86400, 2_000_000.0
    for _ in range(3):
        j.diary.note(Entry("strumento", "a", "lo strumento «apri» non riesce", "x"), now - 100)
    j.diary.set_meta("ultimo_riepilogo", str(now - 8 * day))
    assert not j._maybe_propose(now)  # Andrea non c'è: a una stanza vuota non si propone niente
    j.recorder.last_owner_at = j.recorder.last_activity_at = now - 10
    assert not j._maybe_propose(now)  # conversazione appena finita: si aspetta
    j.recorder.last_activity_at = now - 60
    j.resting = True
    assert not j._maybe_propose(now)  # a riposo (gioca)
    j.resting = False
    j.tasks = SimpleNamespace(tasks={1: SimpleNamespace(status="in corso")})
    assert not j._maybe_propose(now)  # sta lavorando
    j.tasks = None
    j.conv.jarvis_active = True
    assert not j._maybe_propose(now)  # Jarvis sta parlando
    j.conv.jarvis_active = False
    assert j._maybe_propose(now) and "Riepilogo" in j.conv.notes[0] and "1) lo strumento" in j.conv.notes[0]
    assert j.diary.listed == ["a"]  # "la numero 1" nella risposta di Andrea
    assert not j._maybe_propose(now + 3600)  # al massimo uno per periodo
    j.cfg.system.proposals = "mai"
    assert not j._maybe_propose(now + 30 * day)
    assert len(j.conv.notes) == 1

"""Il custode della memoria: a conversazione finita aggiorna i ricordi, con regole che il codice fa rispettare."""

import json
from typing import ClassVar

import pytest

from jarvis.brain.base import BrainUnavailable, Done
from jarvis.memory.keeper import CHECKPOINT, MAX_OPS, MAX_TURNS, MemoryKeeper, parse_operations
from jarvis.memory.store import Memory


class Brain:
    """Cervello finto: restituisce le risposte preparate dal test e registra cosa gli è stato chiesto."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[tuple[str, str]] = []

    async def __call__(self, system, message):
        self.asked.append((system, message))
        a = self.answers.pop(0) if self.answers else '{"operazioni": []}'
        if isinstance(a, Exception):
            raise a
        return a


def ops(*items):
    return json.dumps({"operazioni": list(items)}, ensure_ascii=False)


@pytest.fixture
def mem(tmp_path):
    m = Memory(tmp_path / "m.db")
    yield m
    m.close()


def talk(mem, *lines):
    for i, text in enumerate(lines):
        mem.log_turn("user" if i % 2 == 0 else "jarvis", text)


def keeper(mem, brain, changes=None):
    return MemoryKeeper(mem, brain, user="Andrea", on_change=(changes.append if changes is not None else None))


async def test_a_finished_conversation_becomes_memories(mem):
    talk(mem, "Sai, da lunedì comincio il corso di giapponese il sabato mattina", "Ottima idea, signore.",
         "E ricordati che la mia ragazza si chiama Giulia", "Certo, signore.")
    brain = Brain(ops({"tipo": "aggiungi", "fatto": "Andrea studia giapponese il sabato mattina",
                       "categoria": "abitudini", "importanza": 2},
                      {"tipo": "aggiungi", "fatto": "La ragazza di Andrea si chiama Giulia", "categoria": "persone",
                       "importanza": "3"}))
    changes = []
    run = await keeper(mem, brain, changes).run_once(idle_s=0)
    assert run.status == "fatto" and len(run.applied) == 2 and not run.rejected
    assert {f.text for f in mem.search("Giulia ragazza")} >= {"La ragazza di Andrea si chiama Giulia"}
    assert mem.search("Giulia")[0].importance == 3
    assert [c["op"] for c in changes] == ["ricordato", "ricordato"]  # la finestra lo mostra nel registro
    system, message = brain.asked[0]
    assert "custode della memoria" in system and "Andrea: Sai, da lunedì" in message and "Jarvis: Ottima idea" in message
    assert mem.get_meta(CHECKPOINT) == str(mem.last_turn_id())
    again = await keeper(mem, brain).run_once(idle_s=0)
    assert again.status == "niente" and len(brain.asked) == 1  # già letta: non si rilegge


async def test_waits_until_the_conversation_is_over(mem):
    talk(mem, "Sto pensando di cambiare macchina, forse una Golf")
    brain = Brain()
    run = await keeper(mem, brain).run_once(idle_s=3600)
    assert run.status == "attendo" and not brain.asked


async def test_starts_from_now_not_from_old_test_lines(mem):
    talk(mem, "Jarvis, quanto fa 17 per 23?", "391, signore.")  # l'arretrato del 5/10: prove automatiche
    k = keeper(mem, Brain())
    k.start_from_now()
    assert (await k.run_once(idle_s=0)).status == "niente"
    talk(mem, "da domani vado a correre ogni mattina alle sette")  # detta prima di un riavvio di Jarvis
    k.start_from_now()  # al riavvio non si salta quello che non è ancora stato letto
    assert mem.get_meta(CHECKPOINT) == "2"


async def test_lines_after_clearing_the_archive_are_not_skipped(mem):
    """Svuotate le conversazioni (7/10) i numeri ripartono da 1: un segnalibro rimasto a 187 le salterebbe tutte."""
    mem.set_meta(CHECKPOINT, "187")
    talk(mem, "mi sono trasferito a Prato con la mia ragazza", "Auguri per la casa nuova, signore.")
    brain = Brain(ops({"tipo": "aggiungi", "fatto": "Andrea vive a Prato con la sua ragazza"}))
    assert (await keeper(mem, brain).run_once(idle_s=0)).status == "fatto" and mem.count() == 1


async def test_only_short_lines_are_skipped_without_asking(mem):
    talk(mem, "ok", "Bene, signore.", "sì grazie")
    brain = Brain()
    assert (await keeper(mem, brain).run_once(idle_s=0)).status == "niente" and not brain.asked
    assert mem.get_meta(CHECKPOINT) == "3"


async def test_update_and_delete_only_memories_it_was_shown(mem):
    old_car, _ = mem.remember("Andrea guida una Fiat Panda grigia")
    unrelated, _ = mem.remember("Andrea tifa per la Fiorentina")
    talk(mem, "Ho venduto la Panda, adesso guido una Golf blu", "Congratulazioni, signore.")
    brain = Brain(ops({"tipo": "aggiorna", "id": old_car, "fatto": "Andrea guida una Golf blu"},
                      {"tipo": "elimina", "id": unrelated, "motivo": "inventato"},  # non mostrato: rifiutato
                      {"tipo": "aggiorna", "id": 999, "fatto": "x"}))
    run = await keeper(mem, brain).run_once(idle_s=0)
    shown = brain.asked[0][1]
    assert f"[{old_car}] Andrea guida una Fiat Panda grigia" in shown and f"[{unrelated}]" not in shown
    assert mem.get(old_car).text == "Andrea guida una Golf blu"
    assert mem.get(unrelated) is not None and len(run.rejected) == 2


async def test_a_memory_that_became_false_is_deleted(mem):
    fid, _ = mem.remember("Andrea ha un gatto di nome Briciola")
    talk(mem, "Purtroppo il gatto Briciola è scappato e non è più tornato", "Mi dispiace molto, signore.")
    changes = []
    run = await keeper(mem, Brain(ops({"tipo": "elimina", "id": fid, "motivo": "il gatto non c'è più"})), changes)\
        .run_once(idle_s=0)
    assert mem.get(fid) is None and run.applied[0]["op"] == "dimenticato"
    assert changes[0]["text"] == "Andrea ha un gatto di nome Briciola" and "non c'è più" in changes[0]["reason"]


@pytest.mark.parametrize("secret", [
    "La password del Wi-Fi di Andrea è Gattino123", "Il PIN del bancomat di Andrea è 4512",
    "La parola d'ordine di Andrea è aquila", "L'IBAN di Andrea è IT60X0542811101000000123456",
    "Il numero della carta di Andrea finisce con 1234", "Andrea ha il conto 123456789012345",
])
async def test_secrets_are_never_stored(mem, secret):
    talk(mem, "frase qualsiasi con dentro un segreto da non salvare", "Va bene.")
    run = await keeper(mem, Brain(ops({"tipo": "aggiungi", "fatto": secret}))).run_once(idle_s=0)
    assert mem.count() == 0 and run.rejected and "riservato" in run.rejected[0]


async def test_too_many_operations_are_capped(mem):
    talk(mem, "ti racconto un sacco di cose su di me oggi", "Ascolto, signore.")
    many = [{"tipo": "aggiungi", "fatto": f"Andrea ha visitato la città numero {i} in vacanza"} for i in range(20)]
    run = await keeper(mem, Brain(ops(*many))).run_once(idle_s=0)
    assert mem.count() == MAX_OPS and any("oltre il limite" in r for r in run.rejected)


async def test_brain_down_means_retry_later_without_losing_lines(mem):
    talk(mem, "il mio colore preferito è il verde bosco", "Annotato, signore.")
    brain = Brain(ConnectionError("rete giù"), ops({"tipo": "aggiungi", "fatto": "Il colore preferito di Andrea è il verde bosco"}))
    k = keeper(mem, brain)
    first = await k.run_once(idle_s=0)
    assert first.status == "errore" and mem.get_meta(CHECKPOINT) is None and mem.count() == 0
    second = await k.run_once(idle_s=0)
    assert second.status == "fatto" and mem.count() == 1
    assert brain.asked[0][1] == brain.asked[1][1]  # le stesse frasi, non perse


async def test_unreadable_answers_are_retried_then_skipped(mem):
    talk(mem, "oggi ho finito il primo livello di FERRO", "Complimenti, signore.")
    k = keeper(mem, Brain("non so", "ecco: {rotto", "ancora niente"))
    assert (await k.run_once(idle_s=0)).status == "errore"
    assert (await k.run_once(idle_s=0)).status == "errore"
    third = await k.run_once(idle_s=0)  # la terza volta si va oltre: niente ciclo infinito che consuma
    assert third.status == "fatto" and not third.applied and mem.get_meta(CHECKPOINT) == "2"


async def test_a_long_backlog_is_read_in_pieces_without_waiting(mem):
    talk(mem, *[f"frase numero {i} della lunga giornata di prove" for i in range(MAX_TURNS + 10)])
    brain = Brain()
    k = keeper(mem, brain)
    assert (await k.run_once(idle_s=3600)).status == "fatto"  # blocco pieno: non serve aspettare la fine
    assert mem.get_meta(CHECKPOINT) == str(MAX_TURNS)
    assert (await k.run_once(idle_s=3600)).status == "attendo"  # il resto è recente: si aspetta


def test_answers_wrapped_in_text_or_code_blocks_are_read():
    wrapped = 'Ecco le operazioni:\n```json\n{"operazioni": [{"tipo": "aggiungi", "fatto": "a"}]}\n```'
    assert parse_operations(wrapped) == [{"tipo": "aggiungi", "fatto": "a"}]
    for bad in ("niente", '{"altro": 1}', '{"operazioni": "no"}'):
        with pytest.raises(ValueError):
            parse_operations(bad)


async def test_odd_values_from_the_brain_are_tamed(mem):
    talk(mem, "il mio piatto preferito è la parmigiana di melanzane", "Ottima scelta.")
    run = await keeper(mem, Brain(ops({"tipo": "aggiungi", "fatto": "Il piatto preferito di Andrea è la parmigiana",
                                        "categoria": "cucina", "importanza": "altissima"},
                                       {"tipo": "aggiungi", "fatto": "   "}, {"tipo": "boh"}, "non un oggetto")))\
        .run_once(idle_s=0)
    f = mem.search("parmigiana")[0]
    assert f.category == "generale" and f.importance == 1 and len(run.applied) == 1 and len(run.rejected) == 2


# ------------------------------------------------------------------ il cervello usa e getta del custode (app)


class FakeBrain:
    made: ClassVar[list] = []
    behaviour: ClassVar[dict] = {}

    def __init__(self, system, tools, *args, name="?", **kw):
        self.kind = "claude" if name == "custode" else "cloud"
        self.system, self.started, self.stopped = system, False, False
        FakeBrain.made.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def respond(self, message):
        what = FakeBrain.behaviour[self.kind]
        if isinstance(what, Exception):
            raise what
        yield Done(what) if what != "errore" else Done("", error="limite raggiunto")


@pytest.fixture
def app(monkeypatch, tmp_path):
    import jarvis.app as japp
    from jarvis.config import Config

    monkeypatch.setattr(japp, "ClaudeBrain", FakeBrain)
    monkeypatch.setattr(japp, "LocalBrain", FakeBrain)
    FakeBrain.made = []
    j = japp.Jarvis.__new__(japp.Jarvis)
    j.cfg = Config()
    return j


@pytest.mark.parametrize("provider, keeper_brain, behaviour, expected, tried", [
    ("claude", "auto", {"claude": "da claude", "cloud": "da cloud"}, "da claude", ["claude"]),
    ("claude", "auto", {"claude": BrainUnavailable("limite"), "cloud": "da cloud"}, "da cloud", ["claude", "cloud"]),
    ("claude", "auto", {"claude": "errore", "cloud": "da cloud"}, "da cloud", ["claude", "cloud"]),
    ("ollama-cloud", "auto", {"claude": "da claude", "cloud": "da cloud"}, "da cloud", ["cloud"]),
    ("claude", "cloud", {"claude": "da claude", "cloud": "da cloud"}, "da cloud", ["cloud"]),
])
async def test_keeper_brain_order_and_fallback(app, tmp_path, provider, keeper_brain, behaviour, expected, tried):
    app.cfg.brain.provider, app.cfg.memory.keeper_brain = provider, keeper_brain
    FakeBrain.behaviour = behaviour
    assert await app._ask_once("istruzioni", "messaggio", tmp_path) == expected
    assert [b.kind for b in FakeBrain.made] == tried
    assert all(b.started and b.stopped for b in FakeBrain.made)  # usa e getta: sempre chiuso
    assert FakeBrain.made[0].system == "istruzioni"


async def test_keeper_uses_opencode_between_claude_and_cloud(app, tmp_path):
    """Claude esaurito: il custode prova un modello gratis di OpenCode prima del cloud di Ollama."""
    class Kit:
        def text_brain(self, system):
            b = FakeBrain(system, None, name="custode-opencode")
            b.kind = "opencode"
            return b

    app.opencode = Kit()
    FakeBrain.behaviour = {"claude": BrainUnavailable("limite"), "opencode": "da opencode", "cloud": "da cloud"}
    assert await app._ask_once("istruzioni", "m", tmp_path) == "da opencode"
    assert [b.kind for b in FakeBrain.made] == ["claude", "opencode"] and all(b.stopped for b in FakeBrain.made)
    FakeBrain.made = []
    FakeBrain.behaviour = {"claude": "da claude", "opencode": "errore", "cloud": "da cloud"}
    app.cfg.brain.provider = "opencode"  # "cervello opencode": il custode segue
    assert await app._ask_once("i", "m", tmp_path) == "da claude"
    assert [b.kind for b in FakeBrain.made] == ["opencode", "claude"]
    FakeBrain.made = []
    app.cfg.brain.provider, app.cfg.brain.opencode = "claude", False  # spento a voce: saltato
    FakeBrain.behaviour = {"claude": BrainUnavailable("limite"), "opencode": "mai", "cloud": "da cloud"}
    assert await app._ask_once("i", "m", tmp_path) == "da cloud"
    assert [b.kind for b in FakeBrain.made] == ["claude", "cloud"]


async def test_keeper_brain_gives_up_cleanly(app, tmp_path):
    app.cfg.brain.cloud_fallback = False  # senza cloud resta solo Claude
    FakeBrain.behaviour = {"claude": BrainUnavailable("limite"), "cloud": "mai"}
    with pytest.raises(BrainUnavailable, match="limite"):
        await app._ask_once("i", "m", tmp_path)
    assert [b.kind for b in FakeBrain.made] == ["claude"] and FakeBrain.made[0].stopped


def test_tests_never_write_into_the_real_memory():
    """Il 5/10 ~150 frasi di prova ("quanto fa 17 per 23?") erano finite nelle conversazioni vere di Andrea."""
    from jarvis.app import Jarvis
    from jarvis.audio.devices import SimulatedRoom

    j = Jarvis(backend=SimulatedRoom())
    try:
        assert j.memory.path.name == "memoria_prove.db" and j.memory.embedder is None
        assert j.aec_memory is None  # nemmeno la scelta della cancellazione d'eco per i dispositivi veri
    finally:
        j.memory.close()


def test_memory_changes_reach_the_window_log():
    from types import SimpleNamespace

    from jarvis.config import Config
    from jarvis.core.bus import Bus
    from jarvis.ui.server import UiServer

    bus = Bus()
    bus.emit("memory", op="ricordato", id=1, text="La ragazza di Andrea si chiama Giulia")
    snap = UiServer(SimpleNamespace(bus=bus, cfg=Config(), tasks=None))._snapshot()
    assert snap["history"][-1]["type"] == "memory"  # anche chi apre la finestra dopo vede cosa ha ricordato

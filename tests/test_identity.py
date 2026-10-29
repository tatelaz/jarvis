"""Chi sta parlando: parola d'ordine, persone e permessi, registrazione delle voci, sessioni separate del cervello.
Il modello dell'impronta è finto (vettori scelti dal test); quello vero ha i suoi test in fondo (marcatore hardware no:
gira sul processore, ma serve il file del modello)."""

import json

import numpy as np
import pytest

from jarvis.brain.base import Done, TextDelta
from jarvis.brain.selector import BrainSelector
from jarvis.brain.tools import ToolRegistry
from jarvis.identity import passphrase
from jarvis.identity.admin import VoiceAdmin
from jarvis.identity.enroll import SENTENCES, Enrollment
from jarvis.identity.guard import BASE, EVENT, LEGACY, OWNER_ONLY, TYPED, Identity, Voice
from jarvis.identity.model import SpeakerModel
from jarvis.identity.people import KNOWN, OWNER, People

DIM = 192  # come il modello vero: due voci a caso non si somigliano per sbaglio (a 16 succedeva)


def vec(seed: int, noise: float = 0.0, base: int | None = None) -> np.ndarray:
    """Una "voce": vettore fisso per persona; con rumore, un'altra frase della stessa persona."""
    v = np.random.default_rng(seed if base is None else base).standard_normal(DIM)
    if noise:
        v = v + np.random.default_rng(seed).standard_normal(DIM) * noise
    return (v / np.linalg.norm(v)).astype(np.float32)


class FakeModel:
    available = True

    def __init__(self):
        self.voices: dict[int, np.ndarray | None] = {}

    def embed(self, audio):
        return self.voices.get(round(float(np.asarray(audio).reshape(-1)[0])))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def people(tmp_path):
    p = People(tmp_path / "voci.db")
    yield p
    p.close()


@pytest.fixture
def ident(people):
    clock = Clock()
    i = Identity(people, FakeModel(), clock=clock)
    i.test_clock = clock
    return i


ANDREA, MARCO, TV = 1, 2, 3


def enroll(people, who=ANDREA, name="Andrea", role=OWNER):
    return people.enroll(name, role, [vec(100 + i, 0.3, base=who) for i in range(5)])


# ------------------------------------------------------------------ parola d'ordine (testo)

@pytest.mark.parametrize("said, pw", [
    ("La parola d'ordine è Aquila Reale.", "aquila reale"),
    ("Jarvis, la parola d'ordine è aquila reale", "aquila reale"),
    ("parola d'ordine: aquila reale!", "aquila reale"),
    ("La mia parola d’ordine è aquila", "aquila"),  # apostrofo curvo
    ("la parola d ordine e aquila reale", "aquila reale"),  # trascrizione senza accenti né apostrofo
])
def test_passphrase_is_recognised_only_as_a_statement(said, pw):
    assert passphrase.extract(said) == pw


@pytest.mark.parametrize("said", [
    "qual è la parola d'ordine?", "non ti dico la parola d'ordine", "ho cambiato la parola d'ordine del wifi ieri",
    "dimmi una parola", "aquila reale", "dimmi che la parola d'ordine è sbagliata",
])
def test_ordinary_sentences_are_not_passphrase_attempts(said):
    assert passphrase.extract(said) is None


@pytest.mark.parametrize("said, shown", [
    ("la parola d'ordine è aquila reale", "la parola d'ordine [riservata]"),
    ("ti dico che la parola d'ordine del wifi è pippo123", "ti dico che la parola d'ordine [riservata]"),
    ("che tempo fa domani?", "che tempo fa domani?"),
])
def test_whatever_follows_passphrase_is_hidden(said, shown):
    assert passphrase.redact(said) == shown


def test_passphrase_hash():
    salt, digest = passphrase.make_hash("Aquila reale")
    assert passphrase.check("aquila, REALE!", salt, digest) and not passphrase.check("aquila", salt, digest)
    assert passphrase.make_hash("Aquila reale")[1] != digest  # sale nuovo ogni volta
    assert "aquila" not in salt + digest


# ------------------------------------------------------------------ persone

def test_people_enroll_identify_and_groups(people, tmp_path):
    assert people.owner() is None
    enroll(people)
    enroll(people, MARCO, "Marco", KNOWN)
    p, score = people.best(vec(7, 0.3, base=ANDREA))
    assert p.name == "Andrea" and p.owner and score > 0.8
    assert people.best(vec(TV))[1] < 0.5
    assert people.set_groups("Marco", {"pc"}) and people.get("Marco").groups == {"pc"}
    people.close()
    again = People(tmp_path / "voci.db")  # resta dopo un riavvio
    assert again.get("Marco").groups == {"pc"} and again.owner().name == "Andrea"
    assert again.forget("Marco") and again.get("Marco") is None
    again.close()


def test_only_one_owner(people):
    enroll(people)
    enroll(people, MARCO, "Marco", OWNER)
    assert people.owner().name == "Marco" and people.get("Andrea").role == KNOWN


def test_voiceprint_follows_the_voice(people):
    enroll(people)
    before = people.best(vec(9, base=TV))[1]
    for _ in range(30):
        people.adapt("Andrea", vec(9, base=TV))
    assert people.best(vec(9, base=TV))[1] > before


def test_passphrase_storage(people):
    assert not people.has_passphrase() and not people.check_passphrase("aquila")
    with pytest.raises(ValueError):
        people.set_passphrase("ab")
    people.set_passphrase("Aquila Reale")
    assert people.has_passphrase() and people.check_passphrase("aquila reale")
    stored = people._db.execute("SELECT value FROM meta").fetchone()[0]
    assert "aquila" not in stored.lower()  # mai in chiaro


# ------------------------------------------------------------------ guardiano

def test_off_until_the_owner_registers(ident):
    assert not ident.active
    ident.model.voices[1] = vec(ANDREA)
    assert ident.judge(None).via == "voce"


async def test_who_is_speaking(ident, people):
    enroll(people)
    enroll(people, MARCO, "Marco", KNOWN)
    people.set_groups("Marco", {"pc"})
    m = ident.model.voices
    m[1], m[2], m[3], m[4] = vec(11, 0.3, base=ANDREA), vec(12, 0.3, base=MARCO), vec(TV), None
    andrea, marco, tv, short = [await ident.who(np.full(16000, k, np.float32)) for k in (1, 2, 3, 4)]
    assert andrea.owner and andrea.label == "Andrea" and andrea.groups() >= OWNER_ONLY
    assert not marco.owner and marco.known and marco.groups() == {BASE, "pc"}
    assert not tv.known and tv.label == "ospite" and tv.groups() == {BASE}
    assert not short.owner and short.embedding is None  # troppo breve: nel dubbio ospite


def test_permissions_are_refused_politely_without_hints(ident):
    guest = Voice(None)
    why = Identity.refusal("memoria", guest)
    assert why.startswith("Errore: permesso negato") and "parola" not in why.lower()
    assert Identity.refusal(BASE, guest) is None
    for owner in (LEGACY, TYPED, EVENT):
        assert all(Identity.refusal(g, owner) is None for g in OWNER_ONLY | {BASE, "pc", "compiti"})


def test_passphrase_unlocks_that_voice_for_a_while(ident, people):
    enroll(people)
    people.set_passphrase("aquila reale")
    stranger = vec(5, 0.0, base=TV)  # Andrea raffreddato: la voce non somiglia all'impronta
    v = ident.judge(stranger)
    assert not v.owner
    assert ident.try_passphrase("aquila reale", v) == "ok"
    later = ident.judge(vec(6, 0.2, base=TV))
    assert later.owner and later.via == "parola" and later.person.name == "Andrea"
    assert not ident.judge(vec(MARCO)).owner  # un'altra voce no
    ident.test_clock.t += 1700
    assert ident.judge(vec(7, 0.2, base=TV)).owner  # la conversazione continua: il tempo riparte
    ident.test_clock.t += 1801
    assert not ident.judge(vec(8, 0.2, base=TV)).owner  # mezz'ora di silenzio: finito


def test_too_many_wrong_passphrases_lock_for_a_while(ident, people):
    enroll(people)
    people.set_passphrase("aquila reale")
    v = Voice(None, embedding=vec(TV))
    assert [ident.try_passphrase("gatto", v) for _ in range(3)] == ["sbagliata"] * 3
    assert ident.try_passphrase("aquila reale", v) == "bloccata"  # anche quella giusta, per un po'
    ident.test_clock.t += 601
    assert ident.try_passphrase("aquila reale", v) == "ok"


def test_unlock_improves_the_owner_voiceprint(ident, people):
    enroll(people)
    people.set_passphrase("aquila reale")
    cold = vec(5, base=TV)
    before = people.best(cold)[1]
    ident.try_passphrase("aquila reale", Voice(None, embedding=cold))
    assert people.best(cold)[1] > before  # la prossima volta lo riconosce meglio


def test_same_person(ident):
    a, b, c = Voice(None, embedding=vec(1, 0.2, base=TV)), Voice(None, embedding=vec(2, 0.2, base=TV)), \
        Voice(None, embedding=vec(MARCO))
    assert ident.same_person(a, b) and not ident.same_person(a, c)
    assert ident.same_person(LEGACY, c) and not ident.same_person(a, Voice(None))


# ------------------------------------------------------------------ registrazione

def test_enrollment_rules():
    owner = Voice(None, embedding=vec(ANDREA), via="voce")
    owner.person = type("P", (), {"owner": True, "name": "Andrea"})()
    e = Enrollment("Marco", KNOWN)
    assert e.need_s == 25.0 and Enrollment("Andrea", OWNER).need_s == 40.0
    assert e.add(Voice(None, embedding=vec(MARCO)), 0.8) == "breve"
    assert e.add(owner, 5.0) == "è l'utente"  # mentre si registra Marco, Andrea che parla non conta
    for i in range(4):
        assert e.add(Voice(None, embedding=vec(20 + i, 0.2, base=MARCO)), 7.0) == "preso"
    assert e.done and e.progress == 100 and e.consistency() > 0.8
    assert all(s in e.card() for s in SENTENCES[:3]) and "annulla" in e.card()
    mixed = Enrollment("X", KNOWN, samples=[vec(1), vec(2), vec(3)], seconds=30)
    assert mixed.consistency() < 0.45


def test_voice_admin(people):
    started = []
    admin = VoiceAdmin(people, "Andrea", lambda e: started.append(e) or "avviata")
    assert admin.learn("la mia") == "avviata" and started[-1].role == OWNER and started[-1].name == "Andrea"
    admin.learn("marco", "è il fratello di Andrea")
    assert started[-1].name == "Marco" and started[-1].role == KNOWN and started[-1].note
    assert "nessuna voce" in admin.known()
    enroll(people)
    enroll(people, MARCO, "Marco", KNOWN)
    assert "può" in admin.grant("Marco", ["computer", "compiti"]) and people.get("Marco").groups == {"pc", "compiti"}
    assert admin.grant("Marco", ["sistema"]).startswith("Errore") and people.get("Marco").groups == {"pc", "compiti"}
    assert admin.grant("Marco", []) and people.get("Marco").groups == set()
    assert admin.grant("Luca", ["pc"]).startswith("Errore") and "già fare tutto" in admin.grant("Andrea", ["pc"])
    assert "Andrea (proprietario" in admin.known() and "Marco (conosciuto" in admin.known()
    assert admin.forget("Andrea").startswith("Errore") and "ospite" in admin.forget("Marco")


# ------------------------------------------------------------------ permessi nel registro degli strumenti

async def test_tool_gate_is_enforced_in_code():
    reg = ToolRegistry()
    calls = []
    reg.register("apri", "apre", group="pc")(lambda a: calls.append("apri") or "aperto")
    reg.register("musica", "musica")(lambda a: calls.append("musica") or "ok")
    speaking = {"v": Voice(None)}
    reg.set_gate(lambda tool: Identity.refusal(tool.group, speaking["v"]))
    reg.register("dopo", "registrato dopo il cancello", group="memoria")(lambda a: "x")
    assert (await reg.call("apri", {})).startswith("Errore: permesso negato") and calls == []
    assert await reg.call("musica", {}) == "ok"
    assert (await reg.call("dopo", {})).startswith("Errore: permesso negato")
    speaking["v"] = TYPED
    assert await reg.call("apri", {}) == "aperto"


def test_every_front_tool_has_a_permission_decision(tmp_path):
    from jarvis.config import ConfigStore
    from jarvis.core.bus import Bus
    from jarvis.memory.store import Memory
    from jarvis.scheduler import Scheduler
    from jarvis.tools.front import BASE_TOOLS, TOOL_GROUPS, Services, build_front_tools

    class Tasks:
        def __init__(self):
            self.tasks = {}

    mem = Memory(tmp_path / "m.db")
    s = Services(Bus(), ConfigStore(tmp_path / "c.yaml"), mem, tasks=Tasks(), restart=lambda: "", shutdown=lambda: "",
                 rest=lambda: "", voices=object(), free_models=object(), schedule=Scheduler(None, lambda p, d: None))
    reg = build_front_tools(s)
    names = set(reg.names())
    assert names <= set(TOOL_GROUPS) | BASE_TOOLS, names - set(TOOL_GROUPS) - BASE_TOOLS  # nessuno senza decisione
    assert {t.name for t in reg if t.group == BASE} == BASE_TOOLS
    assert {t.group for t in reg if t.name in ("ricorda", "impostazione", "impara_voce")} <= OWNER_ONLY
    mem.close()


# ------------------------------------------------------------------ due sessioni del cervello

class Brain:
    def __init__(self, name):
        self.name, self.got, self.started, self.stopped, self.interrupted, self.recap = name, [], 0, 0, 0, None

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1

    async def interrupt(self):
        self.interrupted += 1

    async def respond(self, message):
        self.got.append(message)
        yield TextDelta(self.name)
        yield Done(self.name)


async def test_guests_talk_to_a_separate_session():
    owner, made = Brain("andrea"), []
    who = {"v": "owner"}
    sel = BrainSelector(owner, lambda: made.append(Brain("ospiti")) or made[-1], lambda: who["v"])
    await sel.start()
    assert [e async for e in sel.respond("ciao")][-1].text == "andrea" and not made
    who["v"] = "guest"
    assert [e async for e in sel.respond("chi sei?")][-1].text == "ospiti" and made[0].started == 1
    await sel.interrupt()
    assert made[0].interrupted == 1 and owner.interrupted == 0  # si ferma chi sta parlando
    [e async for e in sel.respond("e poi?")]
    assert len(made) == 1 and made[0].got == ["chi sei?", "e poi?"]  # la sessione ospiti resta
    assert owner.got == ["ciao"]  # quella di Andrea non ha mai visto le frasi degli ospiti
    sel.recap = lambda: "riassunto"
    assert owner.recap() == "riassunto" and sel.name == "andrea"
    await sel.stop()
    assert owner.stopped == 1 and made[0].stopped == 1


# ------------------------------------------------------------------ il modello vero

REAL = SpeakerModel()


@pytest.mark.skipif(not REAL.available, reason="modello ReDimNet2+ non convertito")
def test_real_model_tells_two_italian_readers_apart():
    import soundfile as sf

    from jarvis import paths

    folder = paths.MODELS_DIR / "testdata" / "mls_it"
    if not (folder / "voci.json").exists():
        pytest.skip("voci di prova MLS non estratte")
    meta = json.loads((folder / "voci.json").read_text())
    by: dict[str, list] = {}
    for m in meta:
        by.setdefault(m["speaker"], []).append(m["file"])
    a, b = [s for s, files in by.items() if len(files) >= 4][:2]
    clip = lambda f, s=0: sf.read(folder / "wav" / f, dtype="float32")[0][8000 + s:8000 + s + 32000]  # noqa: E731
    ea1, ea2, eb = REAL.embed(clip(by[a][0])), REAL.embed(clip(by[a][1])), REAL.embed(clip(by[b][0]))
    assert float(ea1 @ ea2) > float(ea1 @ eb) + 0.2  # stessa persona molto più vicina di un'altra
    assert REAL.embed(np.zeros(4000, np.float32)) is None  # 0,25 s: troppo poco per dirlo
    assert abs(float(np.linalg.norm(ea1)) - 1) < 1e-4 and ea1.shape == (192,)


def test_a_guest_gets_no_private_context():
    from jarvis.core.context import ContextBuilder

    ctx = ContextBuilder(lambda: "#1 Riassunto PDF (in corso)", lambda: {"title": "Mail a Giulia", "process": "x"},
                         lambda q: ["La ragazza di Andrea si chiama Giulia"])
    mine = ctx.build("chi è Giulia?")
    assert "Giulia" in mine and "Riassunto PDF" in mine and "Mail a Giulia" in mine
    theirs = ctx.build("chi è Giulia?", owner=False)
    assert theirs.startswith("Adesso è") and "Giulia" not in theirs and "Riassunto" not in theirs


def test_keeper_never_takes_guest_lines_as_the_owner(tmp_path):
    from jarvis.memory.keeper import MemoryKeeper
    from jarvis.memory.store import Memory

    mem = Memory(tmp_path / "m.db")
    mem.log_turn("ospite", "io sono allergico ai gatti")
    mem.log_turn("jarvis", "Me lo segno, signore.")
    msg = MemoryKeeper(mem, None)._message(mem.turns_after(0), [])
    assert "Ospite: io sono allergico ai gatti" in msg and "Andrea: io" not in msg
    mem.close()


async def test_guest_lines_are_archived_as_guest(tmp_path):
    import asyncio

    from jarvis.app import Jarvis
    from jarvis.core.bus import Bus
    from jarvis.memory.store import Memory

    j = Jarvis.__new__(Jarvis)
    j.bus, j.memory = Bus(), Memory(tmp_path / "m.db")
    task = asyncio.ensure_future(j._journal_turns())
    await asyncio.sleep(0)
    j.bus.emit("user", text="sono allergico ai gatti", speaker="ospite", owner=False)
    j.bus.emit("user", text="io invece no", speaker="Andrea", owner=True)
    for _ in range(50):
        if len(j.memory.recent_turns()) == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert [r for _, r, _ in j.memory.recent_turns()] == ["ospite", "user"]
    j.memory.close()


async def test_window_never_logs_the_passphrase(caplog):
    from types import SimpleNamespace

    from jarvis.core.bus import Bus
    from jarvis.ui.server import UiServer

    got = []
    app = SimpleNamespace(bus=Bus(), conv=None, set_passphrase=lambda t: got.append(t) or "ok")
    srv = UiServer(app)
    with caplog.at_level("DEBUG"):
        await srv._command({"cmd": "passphrase", "value": "aquila reale"})
        await srv._command({"cmd": "comando_strano", "value": "aquila reale"})
    assert got == ["aquila reale"] and app.bus.history[-1]["info"]["ok"] is True
    assert "aquila" not in caplog.text and "aquila" not in json.dumps(list(app.bus.history))

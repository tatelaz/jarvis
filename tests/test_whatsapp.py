"""WhatsApp nascosto (jarvis/apps/whatsapp.py): il protocollo col ponte, accensione e collegamento, ogni azione.

Il ponte vero (Node + Chrome + WhatsApp Web) qui non parte mai: per il protocollo c'è un ponte finto in Python
(tests/fixtures/fake_wa_bridge.py) lanciato come processo vero; per le azioni un ponte finto in memoria con una
rubrica e dei messaggi inventati. Niente viene inviato a nessuno.
"""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from jarvis.apps import whatsapp as W
from jarvis.apps.base import AppError, Apps

FAKE = Path(__file__).parent / "fixtures" / "fake_wa_bridge.py"
NOW = time.mktime((2026, 10, 27, 15, 0, 0, 0, 0, -1))
# date esplicite: il 25/10/2026 finisce l'ora legale, "3 giorni fa" non è NOW - 3 * 86400
OCT24 = time.mktime((2026, 10, 24, 15, 0, 0, 0, 0, -1))
OCT22 = time.mktime((2026, 10, 22, 15, 0, 0, 0, 0, -1))


# ------------------------------------------------------------------ il protocollo, con un processo vero


def fake_bridge(mode="ready", on_message=None):
    def command(visible):
        return [sys.executable, str(FAKE)], {"FAKE_MODE": mode, "WA_VISIBLE": "1" if visible else "0"}
    return W.Bridge(command, on_message=on_message)


async def test_bridge_answers_errors_events_and_stops_cleanly():
    got = []
    b = fake_bridge(on_message=got.append)
    await b.start()
    assert await b.wait(10) == "pronto" and b.me["name"] == "Andrea" and b.me["visible"] == "0"
    assert await b.call("echo", chat="luca@c.us", text="àèì 😀") == {"chat": "luca@c.us", "text": "àèì 😀"}
    with pytest.raises(AppError, match="chat non trovata: x"):
        await b.call("fail")
    await b.call("notify")
    assert got == [{"author": "Luca", "chat_name": "Luca", "body": "ciao", "group": False}]
    await b.stop()
    assert (b.state, b.error, b.running) == ("spento", "", False)  # chiusura voluta: non è un errore
    with pytest.raises(AppError, match="non è acceso"):
        await b.call("echo")


async def test_bridge_timeout_and_crash_mid_request():
    b = fake_bridge()
    await b.start(visible=True)
    assert await b.wait(10) == "pronto" and b.me["visible"] == "1"
    with pytest.raises(AppError, match=r"non ha risposto in 0\.5 secondi"):
        await b.call("slow", 0.5)
    with pytest.raises(AppError, match="WhatsApp si è chiuso"):
        await b.call("die", 10)
    assert await b.wait(5, until=("errore",)) == "errore"
    assert "codice 3" in b.error and "qualcosa si è rotto" in b.error and not b.running
    await b.stop()


@pytest.mark.parametrize("mode,state,error", [("qr", "da collegare", ""), ("fatal", "errore", "Chrome non trovato")])
async def test_bridge_qr_and_fatal(mode, state, error):
    b = fake_bridge(mode)
    await b.start()
    assert await b.wait(10) == state and b.error == error
    await b.stop()


def test_chrome_user_agent(tmp_path):
    chrome = tmp_path / "Application" / "chrome.exe"
    chrome.parent.mkdir()
    chrome.write_text("")
    for v in ("154.0.7000.1", "155.0.8059.85", "SetupMetrics"):
        (chrome.parent / v).mkdir()
    assert W.user_agent(chrome) == ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/155.0.0.0 Safari/537.36")
    assert W.user_agent(None) is None
    cmd, env = W.bridge_command(tmp_path / "wa", node="node.exe", chrome=chrome)(True)
    assert cmd == ["node.exe", str(W.BRIDGE_DIR / "bridge.js")]
    assert env["WA_VISIBLE"] == "1" and env["WA_CHROME"] == str(chrome) and env["WA_DATA_DIR"] == str(tmp_path / "wa")


# ------------------------------------------------------------------ un WhatsApp finto in memoria

CHATS = [
    {"id": "393401112233@c.us", "name": "Luca Rossi", "group": False, "unread": 2, "ts": NOW - 600,
     "last": {"from_me": False, "type": "chat", "body": "ci sei stasera?"}},
    {"id": "393409998877@c.us", "name": "Luca B", "group": False, "unread": 0, "ts": NOW - 90000,
     "last": {"from_me": True, "type": "chat", "body": "ok"}},
    {"id": "120363@g.us", "name": "Calcetto 🏆", "group": True, "unread": 7, "muted": True, "ts": NOW - 300,
     "last": {"from_me": False, "type": "image", "body": "che gol"}},
    {"id": "393471234567@c.us", "name": "Mamma", "group": False, "unread": 0, "pinned": True, "ts": OCT24,
     "last": {"from_me": False, "type": "ptt", "body": ""}},
    {"id": "393470000000@c.us", "name": "Vecchia chat", "group": False, "unread": 0, "archived": True, "ts": 1},
]
W_CHATS_UNREAD_OFF = [c for c in CHATS if c["name"] == "Luca Rossi"]
CONTACTS = [{"id": "393405550000@c.us", "name": "Giulia Verdi", "pushname": "Giuli", "number": "393405550000"},
            {"id": "393401112233@c.us", "name": "Luca Rossi", "pushname": "Lollo", "number": "393401112233"}]


def msg(i, from_me, body, ts, type_="chat", author="Luca", **extra):
    return {"id": f"m{i}", "from_me": from_me, "author": "" if from_me else author, "type": type_, "body": body,
            "ts": ts, "has_media": type_ in ("image", "ptt", "document", "video"), **extra}


LUCA = [msg(1, True, "ciao!", NOW - 86400 - 60), msg(2, False, "ehi", NOW - 86400),
           msg(3, False, "", NOW - 3000, "e2e_notification"), msg(4, False, "", NOW - 2000, "ptt", duration=72),
           msg(5, False, "contratto", NOW - 1500, "document", filename="contratto.pdf"),
           msg(6, True, "arrivo tra 10 minuti", NOW - 1200), msg(7, False, "ci sei stasera?", NOW - 600)]


def save(d: Path, name: str) -> str:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"x")
    return str(d / name)


class FakeBridge:
    """Stessa interfaccia di W.Bridge, senza processi: registra ogni operazione."""

    def __init__(self):
        self.state, self.running, self.error, self.visible = "spento", False, "", False
        self.me = None
        self.on_message = None
        self.calls, self.starts = [], []
        self.next_start = "pronto"  # cosa succede alla prossima accensione
        self.registered = {"393335556666"}
        self.msgs = {"393401112233@c.us": LUCA, "120363@g.us": [msg(9, False, "che gol", NOW - 300, "image",
                                                                       author="Marco")]}
        self.session = None  # la cartella che WhatsApp Web crea quando si collega
        self._changed = asyncio.Event()

    async def start(self, visible=False):
        self.session.mkdir(parents=True, exist_ok=True)  # come Chrome: la cartella c'è da subito, anche senza QR
        self.starts.append(visible)
        self.running, self.visible = True, visible
        self._set(self.next_start)
        if self.state == "pronto":
            self.me = {"name": "Andrea", "number": "393331112222"}
        if self.state == "errore":
            self.error = "Chrome non trovato"

    def _set(self, state):
        self.state = state
        self._changed.set()

    def scan(self):  # Andrea inquadra il QR: QR accettato, poi WhatsApp Web carica le chat
        self._set("autenticato")

    def synced(self):  # prima sincronizzazione finita: WhatsApp pronto
        self.me = {"name": "Andrea", "number": "393331112222"}
        self._set("pronto")

    async def wait(self, timeout_s, until=("pronto", "da collegare", "scollegato", "errore", "spento")):
        end = asyncio.get_running_loop().time() + timeout_s
        while self.state not in until and asyncio.get_running_loop().time() < end:
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), end - asyncio.get_running_loop().time())
            except TimeoutError:
                break
        return self.state

    async def stop(self):
        self.running = False
        self._set("spento")

    async def call(self, op, timeout_s=60.0, **args):
        if not self.running:
            raise AppError("WhatsApp non è acceso")
        self.calls.append((op, args))
        if op == "directory":
            return {"chats": CHATS, "contacts": CONTACTS}
        if op == "messages":
            ms = self.msgs.get(args["chat"], [])
            return {"chat": {}, "messages": ms[-args.get("limit", 20):]}
        if op == "search":
            return [{**m, "chat": "393401112233@c.us"} for m in LUCA if args["query"] in m["body"]]
        if op == "number_id":
            return f"{args['number']}@c.us" if args["number"] in self.registered else None
        if op == "download":
            return {"file": save(Path(args["dir"]), "vocale.ogg" if args["msg"] == "m4" else "contratto.pdf"),
                    "mimetype": "x", "size": 1}
        if op == "info":
            if args["chat"].endswith("@g.us"):
                return {"group": True, "name": "Calcetto 🏆", "description": "giovedì 21",
                        "participants": [{"id": "393401112233@c.us", "admin": True}, {"id": "393999@c.us"}]}
            return {"group": False, "name": "Luca Rossi", "number": "393401112233", "about": "in palestra",
                    "blocked": False, "business": False, "my_contact": True}
        if op == "group_invite":
            return "https://chat.whatsapp.com/ABC"
        if op == "set_name":
            return True
        return True


@pytest.fixture
def wa(tmp_path):
    fb = FakeBridge()
    fb.session = tmp_path / "wa" / "session"
    mode, alerts, notes, opened, heard = ["su richiesta"], [False], [], [], []
    clock = [1000.0]
    installed = [True]  # i pacchetti Node veri non servono ai test (nelle copie separate non ci sono)
    w = W.WhatsApp(fb, tmp_path / "wa", mode=lambda: mode[0], alerts=lambda: alerts[0], notify=notes.append,
                   clock=lambda: clock[0], installed=lambda: installed[0])
    w.LINK_S = 2.0

    async def transcribe(path):
        heard.append(Path(path).name)
        return "ciao, ti chiamo dopo"

    app = W.build(w, open_uri=opened.append, transcribe=transcribe, downloads=tmp_path / "Download",
                  now=lambda: NOW)
    turns = {"n": 0, "text": ""}
    apps = Apps([app], turns=lambda: turns["n"], last_text=lambda: turns["text"])

    class Ctx:
        pass

    c = Ctx()
    c.fb, c.wa, c.app, c.apps, c.mode, c.alerts, c.notes, c.opened, c.heard, c.clock, c.tmp = \
        fb, w, app, apps, mode, alerts, notes, opened, heard, clock, tmp_path
    c.installed = installed

    def say(text):
        turns["n"] += 1
        turns["text"] = text

    c.say = say
    return c


def linked(c):
    c.fb.session.mkdir(parents=True, exist_ok=True)
    c.wa.mark.write_text("collegato", encoding="utf-8")


def code_of(out):
    assert out.startswith("DA CONFERMARE (codice "), out
    return out.split("codice ")[1].split(")")[0]


async def confirmed(c, action, params):
    """Chiede, l'utente dice sì, conferma: restituisce (testo da confermare, esito)."""
    ask = await c.apps.use("whatsapp", action, params)
    c.say("sì, vai")
    return ask, await c.apps.use("whatsapp", action, {}, code_of(ask))


# ------------------------------------------------------------------ accensione, collegamento, avvisi


async def test_not_linked_only_link_and_status_work(wa):
    assert await wa.apps.use("whatsapp", "leggi", {"chat": "mamma"}) == (
        "Errore: WhatsApp (nascosto, in background) non è pronta: non è collegato al telefono: usa l'azione collega "
        "(una volta sola)")
    assert await wa.apps.use("whatsapp", "stato") == "WhatsApp non è collegato (azione collega)"
    wa.mode[0] = "spento"
    linked(wa)
    assert "spento nelle impostazioni (apps.whatsapp)" in await wa.apps.use("whatsapp", "chat")
    assert await wa.apps.use("whatsapp", "collega") == (
        "Errore: WhatsApp è spento nelle impostazioni (apps.whatsapp): prima va acceso")
    assert wa.fb.starts == []


async def test_without_the_node_packages_it_says_to_link_and_installs_nothing_by_itself(wa):
    linked(wa)
    wa.installed[0] = False
    assert await wa.apps.use("whatsapp", "chat") == (
        "Errore: WhatsApp (nascosto, in background) non è pronta: il ponte non è ancora installato: usa l'azione "
        "collega (lo prepara da solo, una volta sola)")
    assert wa.fb.starts == [] and wa.wa.installing is None  # leggere le chat non scarica niente


def fake_install(wa, problem=None, works=True):
    """npm ci finto: aspetta il via del test, poi installa (o no)."""
    gate, runs = asyncio.Event(), []

    async def install():
        runs.append(1)
        await gate.wait()
        if problem is None and works:
            wa.installed[0] = True
        return problem

    wa.wa.install = install
    return gate, runs


async def test_the_first_link_prepares_the_bridge_then_opens_the_qr(wa):
    wa.installed[0] = False
    gate, runs = fake_install(wa)
    wa.fb.next_start = "da collegare"
    out = await wa.apps.use("whatsapp", "collega")
    assert out.startswith("Preparo WhatsApp: la prima volta servono un paio di minuti")
    assert "sto preparando il ponte" in await wa.apps.use("whatsapp", "chat")
    assert (await wa.apps.use("whatsapp", "collega")).startswith("Preparo WhatsApp")
    await asyncio.sleep(0.01)
    assert runs == [1] and wa.fb.starts == []  # un secondo "collega" non fa partire un secondo npm ci
    gate.set()
    await wa.wa.installing
    assert wa.fb.starts == [True]  # poi da solo la finestra col QR
    assert wa.notes[-1].startswith("Si è aperta una finestra del browser con WhatsApp Web e il QR")
    assert wa.notes[-1].endswith("Dillo in breve.")


@pytest.mark.parametrize(("problem", "works", "said"), [
    ("manca Node.js", True, "manca Node.js"),
    (None, False, "npm ci è finito ma i pacchetti del ponte non ci sono"),
])
async def test_a_bridge_that_does_not_install_is_said_and_can_be_retried(wa, problem, works, said):
    wa.installed[0] = False
    gate, runs = fake_install(wa, problem, works)
    gate.set()
    await wa.apps.use("whatsapp", "collega")
    await wa.wa.installing
    assert wa.fb.starts == []
    assert wa.notes[-1] == f"Il ponte di WhatsApp non si è installato: {said}. Dillo in una frase."
    await wa.apps.use("whatsapp", "collega")  # si può riprovare
    await wa.wa.installing
    assert runs == [1, 1]


async def test_a_bridge_that_installs_but_whatsapp_web_does_not_open(wa):
    wa.installed[0] = False
    gate, _runs = fake_install(wa)
    gate.set()
    wa.fb.next_start = "errore"
    await wa.apps.use("whatsapp", "collega")
    await wa.wa.installing
    assert wa.notes[-1] == ("Il ponte di WhatsApp è pronto, ma WhatsApp Web non si apre: WhatsApp Web non si apre: "
                            "Chrome non trovato. Dillo in breve.")


class FakeNpm:
    def __init__(self, code=0, out=b"added 120 packages", hang=False):
        self.code, self.out, self.hang = code, out, hang
        self.args, self.kw, self.killed = None, None, False
        self.returncode = None

    async def __call__(self, *args, **kw):
        self.args, self.kw = args, kw
        return self

    async def communicate(self):
        if self.hang:
            await asyncio.sleep(3600)
        self.returncode = self.code
        return self.out, None

    def kill(self):
        self.killed = True

    async def wait(self):
        return -9


async def test_install_bridge_runs_npm_ci_in_the_bridge_folder(tmp_path):
    npm = FakeNpm()
    assert await W.install_bridge("C:/node/npm.cmd", chrome=tmp_path / "chrome.exe", run=npm) is None
    assert npm.args == ("C:/node/npm.cmd", "ci", "--omit=dev", "--no-audit", "--no-fund")
    assert npm.kw["cwd"] == str(W.BRIDGE_DIR) and npm.kw["creationflags"] == W.CREATE_NO_WINDOW
    assert npm.kw["env"]["PUPPETEER_SKIP_DOWNLOAD"] == "1"  # c'è un browser: niente Chrome in più


async def test_install_bridge_without_a_browser_lets_puppeteer_bring_its_own(monkeypatch):
    monkeypatch.setattr(W, "find_chrome", lambda: None)
    npm = FakeNpm()
    assert await W.install_bridge("npm.cmd", run=npm) is None
    assert "PUPPETEER_SKIP_DOWNLOAD" not in npm.kw["env"]


async def test_install_bridge_problems(monkeypatch):
    monkeypatch.setattr(W.shutil, "which", lambda name: None)
    assert (await W.install_bridge(run=FakeNpm())).startswith("manca Node.js")
    assert await W.install_bridge("npm.cmd", run=FakeNpm(code=1, out=b"npm ERR! network")) == (
        "npm ci è finito con il codice 1 (rete assente?)")

    async def broken(*args, **kw):
        raise FileNotFoundError("npm.cmd")

    assert (await W.install_bridge("npm.cmd", run=broken)).startswith("npm non parte")
    monkeypatch.setattr(W, "NPM_TIMEOUT_S", 0.05)
    stuck = FakeNpm(hang=True)
    assert await W.install_bridge("npm.cmd", run=stuck) == "npm ci non ha finito in 0.000833333 minuti"
    assert stuck.killed


def test_edge_is_the_browser_when_chrome_is_missing(tmp_path, monkeypatch):
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    monkeypatch.setattr(W, "CHROME_PATHS", [tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe", edge])
    assert W.find_chrome() is None
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")
    (edge.parent / "142.0.3595.53").mkdir()
    assert W.find_chrome() == edge
    assert "Chrome/142.0.0.0" in W.user_agent(edge)
    assert any(p.name == "msedge.exe" for p in W.CHROME_PATHS[-2:])  # quelli veri: Edge dopo Chrome
    assert W.CHROME_PATHS[0].name == "chrome.exe"


def test_a_separate_copy_uses_the_main_folder_node_packages(tmp_path, monkeypatch):
    """Nelle copie separate (auto-miglioramento, versione sana) node_modules non c'è: si usa quello principale."""
    monkeypatch.setattr(W, "BRIDGE_DIR", tmp_path / "copia" / "jarvis" / "apps" / "whatsapp_bridge")
    monkeypatch.setattr(W.paths, "ENGINE_DIR", tmp_path / "principale" / "engine")
    main = tmp_path / "principale" / "jarvis" / "apps" / "whatsapp_bridge" / "node_modules"
    assert W.bridge_modules() == main
    _cmd, env = W.bridge_command(tmp_path / "wa", node="node.exe")(False)
    assert env["NODE_PATH"] == str(main)
    (W.BRIDGE_DIR / "node_modules").mkdir(parents=True)
    assert W.bridge_modules() == W.BRIDGE_DIR / "node_modules"


async def test_linking_opens_a_window_then_works_invisible(wa):
    wa.fb.next_start = "da collegare"
    out = await wa.apps.use("whatsapp", "collega")
    assert out.startswith("Si è aperta una finestra del browser con WhatsApp Web e il QR") and wa.fb.starts == [True]
    wa.fb.next_start = "pronto"
    assert "si sta collegando" in await wa.apps.use("whatsapp", "chat")
    wa.fb.scan()
    await asyncio.sleep(0.05)
    assert not wa.wa.linking.done() and not wa.wa.linked()  # QR accettato non basta: aspetta che sia pronto
    wa.fb.synced()
    await asyncio.wait_for(wa.wa.linking, 5)
    assert wa.wa.linked() and wa.fb.starts == [True, False] and wa.fb.state == "pronto" and not wa.fb.visible
    assert wa.notes == ["WhatsApp è collegato (account Andrea): d'ora in poi lavora invisibile. Dillo in una frase."]
    assert await wa.apps.use("whatsapp", "stato") == "WhatsApp collegato e acceso (account Andrea); modalità su richiesta"
    assert await wa.apps.use("whatsapp", "collega") == "WhatsApp è già collegato (account Andrea)"


async def test_a_window_that_opened_is_not_a_link(wa):
    """La cartella della sessione la crea Chrome appena parte: non vuol dire collegato (27/10)."""
    wa.fb.next_start = "da collegare"
    wa.wa.LINK_S = 0.1
    await wa.apps.use("whatsapp", "collega")
    await asyncio.wait_for(wa.wa.linking, 5)
    assert wa.fb.session.exists() and not wa.wa.linked()
    assert "non è collegato al telefono" in await wa.apps.use("whatsapp", "chat")


async def test_the_first_sync_after_the_qr_gets_its_own_time(wa):
    """Il 27/10 la prima sincronizzazione andava oltre il tempo per inquadrare il QR: ora ha un tempo suo."""
    wa.fb.next_start = "da collegare"
    wa.wa.LINK_S = 0.3
    await wa.apps.use("whatsapp", "collega")
    wa.fb.next_start = "pronto"
    wa.fb.scan()
    await asyncio.sleep(0.6)  # oltre il tempo del QR, la sincronizzazione continua
    assert not wa.wa.linking.done()
    wa.fb.synced()
    await asyncio.wait_for(wa.wa.linking, 5)
    assert wa.wa.linked() and wa.notes[-1].startswith("WhatsApp è collegato")


async def test_a_sync_that_never_ends_is_reported(wa):
    wa.fb.next_start = "da collegare"
    wa.wa.LINK_S, wa.wa.FIRST_SYNC_S = 0.2, 0.2
    await wa.apps.use("whatsapp", "collega")
    wa.fb.scan()
    await asyncio.wait_for(wa.wa.linking, 5)
    assert "il QR è stato inquadrato ma WhatsApp Web non ha finito di caricarsi" in wa.notes[-1]
    assert not wa.wa.linked() and not wa.fb.running


async def test_linking_not_finished_in_time_says_so(wa):
    wa.fb.next_start = "da collegare"
    await wa.apps.use("whatsapp", "collega")
    await asyncio.wait_for(wa.wa.linking, 5)
    assert not wa.fb.running and "non è riuscito" in wa.notes[-1]


async def test_a_lost_session_is_detected_and_forgotten(wa):
    linked(wa)
    wa.fb.next_start = "da collegare"  # il telefono ha tolto il dispositivo: WhatsApp Web chiede di nuovo il QR
    assert await wa.apps.use("whatsapp", "chat") == (
        "Errore: WhatsApp si è scollegato dal telefono: va ricollegato con l'azione collega")
    assert not wa.fb.session.exists() and not wa.wa.linked() and not wa.fb.running
    linked(wa)
    wa.fb.next_start = "errore"
    assert await wa.apps.use("whatsapp", "chat") == "Errore: WhatsApp non parte: Chrome non trovato"


async def test_on_demand_sleeps_when_idle_or_off_always_stays_on(wa):
    linked(wa)
    await wa.apps.use("whatsapp", "chat")
    assert wa.fb.running and wa.fb.starts == [False]
    wa.clock[0] += W.WhatsApp.IDLE_S - 10
    await wa.wa.tick()
    assert wa.fb.running
    wa.clock[0] += 20
    await wa.wa.tick()
    assert not wa.fb.running  # 15 minuti senza usarlo: si spegne
    wa.mode[0] = "sempre"
    await wa.wa.tick()
    assert wa.fb.running
    wa.clock[0] += 10 * W.WhatsApp.IDLE_S
    await wa.wa.tick()
    assert wa.fb.running
    wa.mode[0] = "spento"
    await wa.wa.tick()
    assert not wa.fb.running


async def test_the_idle_check_never_stops_a_starting_bridge(wa):
    """27/10: appena riavviato "ultimo uso" era zero e il controllo dei 15 minuti spegneva WhatsApp mentre partiva."""
    linked(wa)
    wa.clock[0] = 1_000_000.0  # tanto tempo dall'accensione del PC, mai usato
    wa.fb.next_start = "avvio"  # Chrome e WhatsApp Web ci mettono qualche secondo
    starting = asyncio.create_task(wa.wa.ensure())
    await asyncio.sleep(0.05)
    await wa.wa.tick()
    assert wa.fb.running and not starting.done()
    wa.clock[0] += W.WhatsApp.IDLE_S + 1  # anche se l'accensione durasse più dei 15 minuti, non la si interrompe
    await wa.wa.tick()
    assert wa.fb.running and not starting.done()
    wa.fb.synced()
    await asyncio.wait_for(starting, 5)
    await wa.wa.tick()
    assert wa.fb.running  # appena acceso: i 15 minuti partono da adesso
    wa.clock[0] += W.WhatsApp.IDLE_S + 1
    await wa.wa.tick()
    assert not wa.fb.running


async def test_unlink_asks_then_logs_out_and_forgets(wa):
    linked(wa)
    await wa.apps.use("whatsapp", "chat")
    ask, out = await confirmed(wa, "scollega", {})
    assert "scollegare WhatsApp da Jarvis" in ask and out.startswith("WhatsApp scollegato")
    assert ("logout", {}) in wa.fb.calls and not wa.fb.session.exists() and not wa.wa.linked()


async def test_new_message_alerts_say_who_never_what(wa):
    m = {"author": "Luca", "chat_name": "Luca Rossi", "group": False, "body": "il mio PIN è 1234"}
    wa.fb.on_message(m)
    assert wa.notes == []  # avvisi spenti di base
    wa.alerts[0] = True
    wa.fb.on_message(m)
    wa.fb.on_message({"author": "Marco", "chat_name": "Calcetto", "group": True, "body": "gol"})
    wa.fb.on_message({"author": "Pippo", "chat_name": "Rumore", "group": True, "muted": True, "body": "spam"})
    assert len(wa.notes) == 2 and "1234" not in wa.notes[0] and "gol" not in wa.notes[1]
    assert wa.notes[0].startswith("Nuovo messaggio WhatsApp da Luca. Dillo in una frase, senza leggerlo")
    assert wa.notes[1].startswith("Nuovo messaggio WhatsApp da Marco nel gruppo Calcetto.")


async def test_messages_are_only_for_the_owner(wa):
    linked(wa)
    wa.apps.may = lambda group: "Errore: permesso negato." if group == "messaggi" else None
    assert await wa.apps.use("whatsapp", "non_lette") == "Errore: permesso negato."
    assert wa.fb.calls == []


# ------------------------------------------------------------------ trovare le chat


@pytest.mark.parametrize("said,found", [("luca rossi", "393401112233@c.us"), ("mamma", "393471234567@c.us"),
                                        ("calcetto", "120363@g.us"), ("giulia", "393405550000@c.us"),
                                        ("3335556666", "393335556666@c.us"), ("+39 333 555 6666", "393335556666@c.us"),
                                        ("me stesso", "393331112222@c.us")])
async def test_resolve_names_numbers_and_me(wa, said, found):
    linked(wa)
    await wa.wa.ensure()
    assert (await wa.wa.resolve(said))["id"] == found


async def test_resolve_asks_when_unsure(wa):
    linked(wa)
    await wa.wa.ensure()
    with pytest.raises(AppError, match=r"più chat corrispondono a 'luca': Luca Rossi, Luca B\. Chiedi quale\."):
        await wa.wa.resolve("luca")
    with pytest.raises(AppError, match="il numero 3471112222 non è su WhatsApp"):
        await wa.wa.resolve("3471112222")
    with pytest.raises(AppError, match="non trovo una chat o un contatto chiamato 'zio pino'"):
        await wa.wa.resolve("zio pino")
    assert (await wa.wa.resolve("calcetto", groups=True))["id"] == "120363@g.us"


# ------------------------------------------------------------------ leggere


async def test_chat_list_unread_and_read(wa):
    linked(wa)
    out = await wa.apps.use("whatsapp", "chat")
    assert out.split("; ")[:2] == ["Mamma (fissata) — [vocale] (24/10 15:00)",
                                   "Calcetto 🏆 (7 non letti, gruppo, silenziata) — [foto] che gol (14:55)"]
    assert "Luca Rossi (2 non letti) — ci sei stasera? (14:50)" in out and "Vecchia chat" not in out
    assert await wa.apps.use("whatsapp", "chat", {"archiviate": "sì"}) == "Vecchia chat"
    unread = await wa.apps.use("whatsapp", "non_lette")
    assert unread == ("Luca Rossi (2): [14:35] Luca: [documento contratto.pdf] contratto | "
                      "[14:50] Luca: ci sei stasera?\n"
                      "in più 7 non letti in chat silenziate (Calcetto 🏆 7): con tutte=sì li leggo")
    every = await wa.apps.use("whatsapp", "non_lette", {"tutte": "sì"})
    assert "Calcetto 🏆 (7)" in every and "in più" not in every
    read = await wa.apps.use("whatsapp", "leggi", {"chat": "luca rossi", "quanti": 7})
    assert read == ("Luca Rossi:\n[ieri 14:59] tu: ciao!\n[ieri 15:00] Luca: ehi\n"
                    "[14:26] Luca: [vocale 1:12]\n[14:35] Luca: [documento contratto.pdf] contratto\n"
                    "[14:40] tu: arrivo tra 10 minuti\n[14:50] Luca: ci sei stasera?")
    assert not any(op == "seen" for op, _ in wa.fb.calls)  # leggere non manda le spunte blu
    assert await wa.apps.use("whatsapp", "cerca", {"testo": "stasera"}) == (
        "Luca Rossi [14:50] Luca: ci sei stasera?")
    assert await wa.apps.use("whatsapp", "cerca", {"testo": "pizza"}) == "nessun messaggio contiene «pizza»"


async def test_unread_only_in_muted_chats_is_said_not_hidden(wa):
    """27/10: "nessun messaggio non letto" mentre un gruppo silenziato ne aveva 28."""
    linked(wa)
    for c in W_CHATS_UNREAD_OFF:
        c["unread"] = 0
    try:
        assert await wa.apps.use("whatsapp", "non_lette") == (
            "nessun messaggio non letto nelle chat normali; 7 non letti in chat silenziate (Calcetto 🏆 7): con "
            "tutte=sì li leggo")
    finally:
        for c in W_CHATS_UNREAD_OFF:
            c["unread"] = 2


async def test_info_of_contacts_and_groups(wa):
    linked(wa)
    assert await wa.apps.use("whatsapp", "info", {"chat": "luca rossi"}) == (
        "Luca Rossi, numero +393401112233, info: in palestra")
    assert await wa.apps.use("whatsapp", "info", {"chat": "calcetto"}) == (
        "gruppo Calcetto 🏆: 2 membri: Luca Rossi (admin), +393999; descrizione: giovedì 21")


# ------------------------------------------------------------------ scrivere: sempre con conferma


async def test_send_asks_first_and_sends_to_the_confirmed_chat(wa):
    linked(wa)
    ask = await wa.apps.use("whatsapp", "invia", {"chat": "luca rossi", "testo": "arrivo"})
    assert "inviare a Luca Rossi: «arrivo»" in ask and not any(op == "send" for op, _ in wa.fb.calls)
    wa.say("sì")
    assert await wa.apps.use("whatsapp", "invia", {"chat": "mamma", "testo": "altro"}, code_of(ask)) == (
        "inviato a Luca Rossi")
    assert ("send", {"chat": "393401112233@c.us", "text": "arrivo"}) in wa.fb.calls
    ask, out = await confirmed(wa, "invia", {"chat": "calcetto", "testo": "ci sono"})
    assert "inviare a Calcetto 🏆 (gruppo): «ci sono»" in ask and out == "inviato a Calcetto 🏆"


async def test_reply_react_forward_edit_delete(wa):
    linked(wa)
    ask, out = await confirmed(wa, "rispondi", {"chat": "luca rossi", "testo": "sì!"})
    assert "citando [14:50] Luca: ci sei stasera?" in ask and out == "risposto a Luca Rossi"
    assert ("send", {"chat": "393401112233@c.us", "text": "sì!", "quote": "m7"}) in wa.fb.calls
    ask, _ = await confirmed(wa, "rispondi", {"chat": "luca rossi", "testo": "ok", "messaggio": "contratto"})
    assert "citando [14:35] Luca: [documento contratto.pdf] contratto" in ask
    ask, out = await confirmed(wa, "reagisci", {"chat": "luca rossi", "emoji": "👍"})
    assert "reagire con 👍 a [14:50] Luca: ci sei stasera?" in ask and ("react", {"msg": "m7", "emoji": "👍"}) in wa.fb.calls
    ask, out = await confirmed(wa, "inoltra", {"chat": "luca rossi", "a": "mamma", "messaggio": "contratto"})
    assert "inoltrare a Mamma il messaggio [14:35]" in ask and ("forward", {"msg": "m5", "to": "393471234567@c.us"}) in \
        wa.fb.calls
    ask, out = await confirmed(wa, "modifica_messaggio", {"chat": "luca rossi", "testo": "arrivo tra 20 minuti"})
    assert "correggere il tuo messaggio [14:40] tu: arrivo tra 10 minuti" in ask
    assert ("edit_msg", {"msg": "m6", "text": "arrivo tra 20 minuti"}) in wa.fb.calls
    ask, out = await confirmed(wa, "elimina_messaggio", {"chat": "luca rossi", "per_tutti": "sì"})
    assert "eliminare PER TUTTI il messaggio [14:40] tu: arrivo" in ask and out == "messaggio eliminato per tutti"
    assert ("delete_msg", {"msg": "m6", "everyone": True}) in wa.fb.calls
    ask, out = await confirmed(wa, "elimina_messaggio", {"chat": "luca rossi"})
    assert "eliminare solo per te il messaggio [14:50] Luca: ci sei stasera?" in ask
    assert await wa.apps.use("whatsapp", "reagisci", {"chat": "luca rossi", "emoji": "❤️", "messaggio": "pizza"}) == (
        "Errore: non trovo il messaggio con «pizza» negli ultimi messaggi")


async def test_send_file_checks_the_file_and_says_its_size(wa):
    linked(wa)
    f = wa.tmp / "foto mare.jpg"
    f.write_bytes(b"0" * 2_500_000)
    ask, out = await confirmed(wa, "invia_file", {"chat": "mamma", "percorso": str(f), "didascalia": "guarda!"})
    assert "inviare a Mamma il file foto mare.jpg (2,4 MB) con la didascalia «guarda!»" in ask
    assert out == "inviato foto mare.jpg a Mamma"
    assert ("send_file", {"chat": "393471234567@c.us", "file": str(f), "caption": "guarda!", "document": False}) in \
        wa.fb.calls
    assert (await wa.apps.use("whatsapp", "invia_file", {"chat": "mamma", "percorso": "C:/non/esiste.pdf"})).startswith(
        "Errore: il file C:")


async def test_download_and_transcribe_voice_messages(wa):
    linked(wa)
    out = await wa.apps.use("whatsapp", "scarica", {"chat": "luca rossi"})
    assert out == f"salvato {wa.tmp / 'Download' / 'contratto.pdf'} (documento)"
    assert await wa.apps.use("whatsapp", "trascrivi", {"chat": "luca rossi"}) == (
        "vocale di Luca (14:26, 1:12): ciao, ti chiamo dopo")
    assert wa.heard == ["vocale.ogg"]
    assert await wa.apps.use("whatsapp", "trascrivi", {"chat": "mamma"}) == (
        "Errore: nessun vocale negli ultimi messaggi di Mamma")


async def test_tidy_actions_need_no_confirmation(wa):
    linked(wa)
    for action, op, args in (("segna_letta", "seen", {}), ("segna_non_letta", "mark_unread", {}),
                             ("archivia", "archive", {"on": True}), ("ripristina", "archive", {"on": False}),
                             ("fissa", "pin", {"on": True}), ("togli_fissata", "pin", {"on": False}),
                             ("riattiva_audio", "unmute", {}), ("sblocca", "block", {"on": False})):
        out = await wa.apps.use("whatsapp", action, {"chat": "mamma"})
        assert out.startswith("Mamma: ") and wa.fb.calls[-1] == (op, {"chat": "393471234567@c.us", **args}), action
    assert await wa.apps.use("whatsapp", "silenzia", {"chat": "mamma", "ore": "8"}) == "Mamma: silenziata per 8 ore"
    assert wa.fb.calls[-1] == ("mute", {"chat": "393471234567@c.us", "until": int(NOW + 8 * 3600)})
    assert await wa.apps.use("whatsapp", "silenzia", {"chat": "mamma"}) == "Mamma: silenziata per sempre"


async def test_destructive_and_visible_things_ask_first(wa):
    linked(wa)
    for action, words in (("svuota_chat", "cancellare TUTTI i messaggi della chat Mamma"),
                          ("elimina_chat", "eliminare la chat Mamma"), ("blocca", "bloccare Mamma")):
        ask, _ = await confirmed(wa, action, {"chat": "mamma"})
        assert words in ask
    ask, out = await confirmed(wa, "crea_gruppo", {"nome": "Pizza", "persone": "giulia, luca rossi e mamma"})
    assert "creare il gruppo «Pizza» con Giulia Verdi, Luca Rossi, Mamma" in ask
    assert ("group_create", {"name": "Pizza", "participants": ["393405550000@c.us", "393401112233@c.us",
                                                               "393471234567@c.us"]}) in wa.fb.calls
    ask, out = await confirmed(wa, "gruppo_aggiungi", {"chat": "calcetto", "persone": "giulia"})
    assert "aggiungere al gruppo Calcetto 🏆: Giulia Verdi" in ask
    assert ("group_members", {"chat": "120363@g.us", "participants": ["393405550000@c.us"], "action": "add"}) in \
        wa.fb.calls
    ask, _ = await confirmed(wa, "gruppo_nome", {"chat": "calcetto", "testo": "Calcetto giovedì"})
    assert "cambiare il nome del gruppo Calcetto 🏆 in «Calcetto giovedì»" in ask
    assert await wa.apps.use("whatsapp", "gruppo_link", {"chat": "calcetto"}) == (
        "link d'invito di Calcetto 🏆: https://chat.whatsapp.com/ABC")
    assert (await wa.apps.use("whatsapp", "gruppo_nome", {"chat": "mamma", "testo": "x"})).startswith(
        "Errore: non trovo una chat o un contatto chiamato 'mamma'")  # tra i gruppi Mamma non c'è
    ask, out = await confirmed(wa, "pubblica_stato", {"testo": "in vacanza"})
    assert "pubblicare come stato, visibile ai contatti: «in vacanza»" in ask and out == "stato pubblicato"
    ask, out = await confirmed(wa, "cambia_info", {"testo": "occupato"})
    assert out == "info del profilo cambiata"


async def test_calls_go_through_the_real_app(wa):
    assert await wa.apps.use("whatsapp", "apri_app") == "aperta l'app di WhatsApp"
    linked(wa)
    out = await wa.apps.use("whatsapp", "apri_app", {"chat": "mamma"})
    assert out.startswith("aperta l'app di WhatsApp sulla chat di Mamma")
    assert wa.opened == ["whatsapp:", "whatsapp://send?phone=393471234567"]
    assert await wa.apps.use("whatsapp", "apri_app", {"chat": "calcetto"}) == (
        "Errore: l'app si apre sulle chat delle persone, non dei gruppi")


# ------------------------------------------------------------------ vocali veri: decodifica e trascrizione a pezzi


async def test_voice_messages_are_decoded_and_transcribed_in_chunks(tmp_path):
    f = tmp_path / "vocale.ogg"
    t = np.arange(48000 * 65) / 48000
    sf.write(f, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 48000, format="OGG", subtype="OPUS")
    audio = W.voice_audio(f)
    assert audio.dtype == np.float32 and abs(len(audio) - 16000 * 65) < 16000 * 0.1
    sizes = []

    async def stt(chunk):
        sizes.append(len(chunk))
        return f"pezzo{len(sizes)}"

    assert await W.transcribe_with(stt, f) == "pezzo1 pezzo2 pezzo3"
    assert sizes[:2] == [16000 * 30, 16000 * 30] and 16000 * 4 < sizes[2] < 16000 * 6


def test_message_lines():
    assert W.message_line(msg(1, False, "", NOW - 60, "ptt", duration="7"), NOW) == "[14:59] Luca: [vocale 0:07]"
    assert W.message_line(msg(1, False, "Via Roma", NOW, "location"), NOW) == "[15:00] Luca: [posizione]"
    assert W.message_line(msg(1, False, "", NOW, "gp2"), NOW) is None
    reply = msg(2, True, "bell' audio", NOW, quoted_text="trucco piu vecchio della bibbia", quoted_author="leonardo")
    assert W.message_line(reply, NOW) == ("[15:00] tu: bell' audio (in risposta a leonardo: «trucco piu vecchio "
                                          "della bibbia»)")
    assert W.message_line(msg(1, True, "ok", OCT22), NOW) == "[22/10 15:00] tu: ok"

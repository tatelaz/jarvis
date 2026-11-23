"""Gli strumenti che Jarvis usa da solo mentre parla: apri, volume, musica, appunti, finestra, schermo, memoria,
compiti. Il PC è finto (browser, app, volume, tasti) tranne dove il test è marcato `desktop`.

Ogni strumento del cervello frontale deve avere un test: test_every_tool_is_tested lo controlla.
"""

import time
from dataclasses import dataclass

import pytest

from jarvis.apps.base import App, Apps
from jarvis.config import ConfigStore
from jarvis.core.bus import Bus
from jarvis.improve.diary import Diary
from jarvis.memory.store import Memory
from jarvis.scheduler import Scheduler
from jarvis.setup.welcome import Welcome
from jarvis.tools import system
from jarvis.tools.front import Services, build_front_tools

# ------------------------------------------------------------------ PC finto


class FakeUser32:
    def __init__(self):
        self.keys: list[tuple[int, int]] = []
        self.locked = 0

    def keybd_event(self, vk, scan, flags, extra):
        self.keys.append((vk, flags))

    def LockWorkStation(self):
        self.locked += 1
        return 1


class FakeEndpoint:
    def __init__(self, level=0.5, muted=False):
        self.level, self.muted = level, muted

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, value, _ctx):
        self.level = value

    def GetMute(self):
        return int(self.muted)

    def SetMute(self, value, _ctx):
        self.muted = bool(value)


APPS = [{"name": n, "id": f"id.{n}"} for n in (
    "Google Chrome", "Comet", "Visual Studio Code", "Calcolatrice", "Blocco note", "Spotify", "Steam", "Discord",
    "Microsoft Edge", "Paint.NET", "Impostazioni", "Gestione attività")]


@pytest.fixture
def pc(monkeypatch, tmp_path):
    """Browser, app, cartelle, tasti e volume finti: si registra cosa Jarvis avrebbe fatto."""

    class PC:
        def __init__(self):
            self.sites: list[str] = []
            self.opened: list[str] = []
            self.launched: list[str] = []
            self.user32 = FakeUser32()
            self.speakers = FakeEndpoint()

    pc = PC()
    monkeypatch.setattr(system.webbrowser, "open", lambda url: pc.sites.append(url))
    monkeypatch.setattr(system.os, "startfile", lambda p: pc.opened.append(str(p)), raising=False)
    monkeypatch.setattr(system.AppIndex, "launch", staticmethod(lambda app: pc.launched.append(app["name"])))
    apps = system.AppIndex(cache=tmp_path / "apps.json")
    apps.apps = list(APPS)
    monkeypatch.setattr(system, "_apps", apps)
    monkeypatch.setattr(system, "user32", pc.user32)
    monkeypatch.setattr(system, "_endpoint", lambda: pc.speakers)
    return pc


# ------------------------------------------------------------------ servizi finti per i compiti


@dataclass
class FakeTask:
    id: int
    title: str
    status: str = "in corso"
    result: str = ""
    error: str = ""

    def summary(self):
        return f"#{self.id} {self.title} ({self.status})"


class FakeTasks:
    def __init__(self):
        self.tasks: dict[int, FakeTask] = {}
        self.calls: list[tuple] = []

    def spawn(self, goal, title=None, cwd=None, kind="lavoro"):
        t = FakeTask(len(self.tasks) + 1, title or goal[:20])
        self.tasks[t.id] = t
        self.calls.append(("spawn", goal, title, cwd, kind))
        return t

    async def message(self, tid, text):
        self.calls.append(("message", tid, text))
        return f"messaggio inoltrato al compito #{tid}"

    async def cancel(self, tid):
        self.calls.append(("cancel", tid))
        return f"compito #{tid} annullato"

    def answer(self, tid, approved, note=""):
        self.calls.append(("answer", tid, approved, note))
        return f"risposta data al compito #{tid}"


class FakeMedia:
    """guarda_video ha i suoi test in tests/test_media.py: qui basta che lo strumento esista."""

    async def look(self, args):
        return "guardato"


@pytest.fixture
def tools(tmp_path):
    bus, tasks = Bus(), FakeTasks()
    mem = Memory(tmp_path / "m.db")
    s = Services(bus, ConfigStore(tmp_path / "config.yaml"), mem, tasks=tasks,
                 restart=lambda: "riavvio in arrivo", shutdown=lambda: "spegnimento in arrivo",
                 rest=lambda: "riposo in arrivo", diary=Diary(), apps=Apps([App("prova", "Prova", "pc")]),
                 media=FakeMedia(), free_models=object(), schedule=Scheduler(None, lambda p, due: None),
                 welcome=Welcome(tmp_path / "presentazione.json", tmp_path / "fatto.json"), updates=object(), voice_maker=object())
    yield build_front_tools(s), s
    mem.close()


# ------------------------------------------------------------------ apri


@pytest.mark.parametrize("said, kind, expected", [
    ("youtube.com", "sito", "https://youtube.com"),
    ("www.google.it", "sito", "https://www.google.it"),
    ("https://example.com/a?b=1", "sito", "https://example.com/a?b=1"),
    ("github.com/anthropics/claude-code", "sito", "https://github.com/anthropics/claude-code"),
    ("Paint.NET", "app", "Paint.NET"),  # l'app installata, non il sito paint.net
    ("chrome", "app", "Google Chrome"),
    ("vs code", "app", "Visual Studio Code"),
    ("browser", "app", "Comet"),
    ("spoti", "app", "Spotify"),
    ("CALCOLATRICE", "app", "Calcolatrice"),
    ("task manager", "app", "Gestione attività"),
    ("download", "cartella", "Downloads"),
    ("Scrivania", "cartella", "Desktop"),
])
async def test_open_finds_the_right_thing(pc, tools, said, kind, expected):
    reg, _ = tools
    out = await reg.call("apri", {"cosa": said})
    assert not out.startswith("Errore"), out
    done = {"sito": pc.sites, "app": pc.launched, "cartella": pc.opened}
    assert len(pc.sites) + len(pc.launched) + len(pc.opened) == 1  # una cosa sola
    assert done[kind] and done[kind][0].endswith(expected) if kind == "cartella" else done[kind] == [expected]


@pytest.mark.parametrize("said", [
    "relazione.docx", "setup.exe", "foto.jpg",  # file senza percorso: prima diventavano siti (https://setup.exe)
    "cerca gatti su google", "programma inesistente xyz", "", "   ",
])
async def test_open_refuses_what_it_cannot_find(pc, tools, said):
    reg, _ = tools
    out = await reg.call("apri", {"cosa": said})
    assert out.startswith("Errore") and not (pc.sites or pc.launched or pc.opened)
    if said.endswith((".docx", ".exe", ".jpg")):
        assert "percorso completo" in out


async def test_a_name_that_happens_to_be_a_folder_of_jarvis_is_not_opened(pc, tools, tmp_path, monkeypatch):
    """"apri data" non deve aprire la cartella data di Jarvis (relativa alla sua cartella di lavoro)."""
    reg, _ = tools
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    assert (await reg.call("apri", {"cosa": "data"})).startswith("Errore") and not pc.opened


async def test_open_a_real_file_by_full_path(pc, tools, tmp_path, monkeypatch):
    reg, _ = tools
    f = tmp_path / "relazione.docx"
    f.write_text("x")
    assert (await reg.call("apri", {"cosa": f'"{f}"'})).startswith("aperto")
    monkeypatch.setenv("CARTELLA_PROVA", str(tmp_path))
    await reg.call("apri", {"cosa": r"%CARTELLA_PROVA%\relazione.docx"})
    assert pc.opened == [str(f), str(f)] and not pc.sites


def test_app_list_comes_from_windows_and_is_cached(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            stdout = '{"Name": "Steam", "AppID": "Valve.Steam"}'  # una sola app: PowerShell dà un oggetto, non una lista
        return R()

    monkeypatch.setattr(system.subprocess, "run", fake_run)
    idx = system.AppIndex(cache=tmp_path / "apps.json")
    assert idx.load() == [{"name": "Steam", "id": "Valve.Steam"}]
    again = system.AppIndex(cache=tmp_path / "apps.json")
    assert again.load() == idx.apps and len(calls) == 1  # dalla cache, senza rilanciare PowerShell
    assert again.load(force=True) and len(calls) == 2


def test_fuzzy_app_matching_does_not_invent_apps():
    idx = system.AppIndex()
    idx.apps = list(APPS)
    assert idx.find("word") is None  # Word non c'è: non deve diventare "Discord"
    assert idx.find("note")["name"] == "Blocco note"
    assert idx.find_exact("google chrome")["name"] == "Google Chrome" and idx.find_exact("chro") is None


# ------------------------------------------------------------------ volume e musica


@pytest.mark.parametrize("args, level, muted, said", [
    ({"azione": "leggi"}, 0.5, False, "volume al 50%"),
    ({"azione": "alza"}, 0.6, False, "volume dal 50% al 60%"),
    ({"azione": "abbassa", "livello": 20}, 0.3, False, "volume dal 50% al 30%"),
    ({"azione": "alza", "livello": "20"}, 0.7, False, "volume dal 50% al 70%"),  # numero come testo
    ({"azione": "abbassa", "livello": -10}, 0.4, False, "volume dal 50% al 40%"),
    ({"azione": "alza", "livello": 80}, 1.0, False, "volume dal 50% al 100%"),  # mai oltre 100
    ({"azione": "abbassa", "livello": 90}, 0.0, False, "volume dal 50% al 0%"),
    ({"azione": "imposta", "livello": "35%"}, 0.35, False, "volume dal 50% al 35%"),
    ({"azione": "imposta", "livello": 150}, 1.0, False, "volume dal 50% al 100%"),
    ({"azione": "muto"}, 0.5, True, "audio silenziato"),
])
async def test_volume(pc, tools, args, level, muted, said):
    reg, _ = tools
    assert await reg.call("volume", args) == said
    assert pc.speakers.level == pytest.approx(level) and pc.speakers.muted is muted


async def test_volume_unmutes_when_raised_and_reports_mute(pc, tools):
    reg, _ = tools
    pc.speakers.muted = True
    assert await reg.call("volume", {"azione": "leggi"}) == "volume al 50% (muto)"
    await reg.call("volume", {"azione": "alza"})
    assert not pc.speakers.muted  # alzare il volume a muto non deve lasciare tutto in silenzio
    pc.speakers.muted = True
    assert await reg.call("volume", {"azione": "riattiva"}) == "audio riattivato, volume al 60%"


@pytest.mark.parametrize("args", [{"azione": "imposta"}, {"azione": "fortissimo"}, {"azione": "alza", "livello": "tanto"}])
async def test_volume_bad_requests(pc, tools, args):
    reg, _ = tools
    assert "Errore" in await reg.call("volume", args)
    assert pc.speakers.level == 0.5  # niente cambiato


@pytest.mark.parametrize("action, vk", [("play_pausa", 0xB3), ("successivo", 0xB0), ("precedente", 0xB1), ("stop", 0xB2)])
async def test_music_keys_press_and_release(pc, tools, action, vk):
    reg, _ = tools
    assert "inviato" in await reg.call("musica", {"azione": action})
    assert pc.user32.keys == [(vk, 0), (vk, 2)]  # premuto e rilasciato, altrimenti il tasto resta giù


async def test_music_unknown_action(pc, tools):
    reg, _ = tools
    assert (await reg.call("musica", {"azione": "volume"})).startswith("Errore") and not pc.user32.keys


# ------------------------------------------------------------------ appunti, finestra, schermo


async def test_clipboard_tool(monkeypatch, tools):
    reg, _ = tools
    box = {"text": "x" * 5000}
    monkeypatch.setattr(system, "clipboard_read", lambda: box["text"])
    monkeypatch.setattr(system, "clipboard_write", lambda t: box.update(text=t))
    assert await reg.call("appunti", {"azione": "leggi"}) == "x" * 4000  # testi enormi accorciati per il cervello
    assert await reg.call("appunti", {"azione": "scrivi", "testo": "ciao"}) == "testo copiato negli appunti"
    assert box["text"] == "ciao"
    for missing in ({"azione": "scrivi"}, {"azione": "scrivi", "testo": ""}):  # prima svuotava gli appunti
        assert await reg.call("appunti", missing) == "Errore: serve il testo da copiare"
    assert box["text"] == "ciao"
    box["text"] = ""
    assert "vuoti" in await reg.call("appunti", {"azione": "leggi"})


@pytest.mark.desktop
def test_real_clipboard_round_trip_keeps_accents_and_emoji():
    """Sugli appunti veri di Windows (poi rimessi com'erano)."""
    import ctypes

    u32 = ctypes.windll.user32
    if u32.CountClipboardFormats() and not u32.IsClipboardFormatAvailable(system.CF_UNICODETEXT):
        pytest.skip("negli appunti c'è qualcosa che non è testo (es. un'immagine): non lo tocco")
    before = system.clipboard_read()
    try:
        text = "Perché no? Città — 😀 «virgolette»\nseconda riga"
        system.clipboard_write(text)
        assert system.clipboard_read() == text
    finally:
        system.clipboard_write(before)


def test_real_active_window_never_fails():
    w = system.active_window()
    assert isinstance(w["title"], str) and isinstance(w["process"], str) and isinstance(w["pid"], int)


async def test_active_window_tool(monkeypatch, tools):
    reg, _ = tools
    monkeypatch.setattr(system, "active_window", lambda: {"title": "Jarvis - Mark III", "process": "pythonw.exe", "pid": 1})
    assert await reg.call("finestra_attiva", {}) == "Jarvis - Mark III (pythonw.exe)"


async def test_lock_screen(pc, tools):
    reg, _ = tools
    assert await reg.call("blocca_schermo", {}) == "schermo bloccato" and pc.user32.locked == 1


async def test_show_on_screen_and_stay_silent(tools):
    reg, s = tools
    assert await reg.call("mostra", {"titolo": "Codice", "contenuto": "```py\nprint(1)\n```"}) == "mostrato a schermo"
    ev = s.bus.history[-1]
    assert ev["type"] == "show" and ev["title"] == "Codice" and "print(1)" in ev["markdown"]
    out = await reg.call("nessuna_risposta", {})  # niente frase da ripetere: il 26/10 diceva "resto in silenzio"
    assert "non aggiungere testo" in out and "resto in silenzio" not in out


# ------------------------------------------------------------------ memoria


async def test_remember_search_forget(tools):
    reg, s = tools
    assert await reg.call("ricorda", {"fatto": "Andrea preferisce il caffè senza zucchero", "categoria": "preferenze",
                                      "importanza": 9}) == "memorizzato"
    assert s.memory.search("caffè")[0].importance == 3  # fuori scala: portata a 3, non salvata a 9
    assert await reg.call("ricorda", {"fatto": "Andrea preferisce il caffè senza zucchero."}) == \
        "aggiornato un ricordo già esistente"
    s.memory.log_turn("utente", "domani devo portare la macchina dal meccanico")
    out = await reg.call("cerca_memoria", {"domanda": "caffè zucchero"})
    assert out.startswith("- Andrea preferisce il caffè senza zucchero")
    turns = await reg.call("cerca_memoria", {"domanda": "meccanico"})
    assert "utente: domani devo portare la macchina dal meccanico" in turns
    assert time.strftime("%d/%m") in turns  # con la data, per dire "me l'hai detto martedì"
    assert await reg.call("cerca_memoria", {"domanda": "astronomia"}) == "nessun ricordo pertinente"
    assert (await reg.call("dimentica", {"cosa": "caffè"})).startswith("dimenticato: Andrea preferisce il caffè")
    assert s.memory.count() == 0
    assert await reg.call("dimentica", {"cosa": "caffè"}) == "non ho trovato niente del genere in memoria"


async def test_forget_asks_which_one_when_ambiguous(tools):
    reg, s = tools
    s.memory.remember("Andrea ha un gatto di nome Briciola")
    s.memory.remember("Andrea ha un gatto di nome Tigre")
    out = await reg.call("dimentica", {"cosa": "il gatto"})
    assert out.startswith("Più ricordi simili, non ho cancellato niente") and "Briciola" in out and "Tigre" in out
    assert s.memory.count() == 2
    assert await reg.call("dimentica", {"cosa": "Andrea ha un gatto di nome Tigre"}) == \
        "dimenticato: Andrea ha un gatto di nome Tigre"


# ------------------------------------------------------------------ compiti


async def test_task_tools(tools):
    reg, s = tools
    tm = s.tasks
    assert await reg.call("avvia_compito", {"obiettivo": "Riassumi i PDF in Download", "titolo": "Riassunto PDF",
                                            "cartella": "C:/x"}) == "compito #1 avviato (in corso)"
    assert "ti chiederò conferma" in await reg.call("migliora_jarvis", {"richiesta": "Saluta per nome",
                                                                     "titolo": "Saluto"})
    assert tm.calls[0] == ("spawn", "Riassumi i PDF in Download", "Riassunto PDF", "C:/x", "lavoro")
    assert tm.calls[1][-1] == "miglioria"  # le modifiche a Jarvis passano dalla strada con i test e la conferma
    assert await reg.call("messaggio_compito", {"id": "1", "messaggio": "solo quelli di ottobre"}) == \
        "messaggio inoltrato al compito #1"
    assert await reg.call("conferma_compito", {"id": 1, "approvata": True}) == "risposta data al compito #1"
    assert await reg.call("annulla_compito", {"id": 2}) == "compito #2 annullato"
    assert tm.calls[2:] == [("message", 1, "solo quelli di ottobre"), ("answer", 1, True, ""), ("cancel", 2)]
    tm.tasks[1].status, tm.tasks[1].result = "finito", "r" * 500
    tm.tasks[2].status, tm.tasks[2].error = "fallito", "e" * 500
    rows = (await reg.call("stato_compiti", {})).splitlines()
    assert rows[0] == "#1 Riassunto PDF (finito) — esito: " + "r" * 300
    assert rows[1] == "#2 Saluto (fallito) — errore: " + "e" * 200


async def test_no_tasks(tools):
    reg, s = tools
    s.tasks.tasks.clear()
    assert await reg.call("stato_compiti", {}) == "nessun compito"


async def test_rest_tool(tools):
    reg, _ = tools
    assert await reg.call("riposo", {}) == "riposo in arrivo"


async def test_missing_arguments_and_crashes_become_clear_errors(pc, tools, monkeypatch):
    reg, _ = tools
    assert (await reg.call("apri", {})).startswith("Errore: argomenti mancanti per apri: cosa")

    def broken():
        raise OSError("servizio audio non risponde")

    monkeypatch.setattr(system, "_endpoint", broken)
    out = await reg.call("volume", {"azione": "leggi"})
    assert out == "Errore nello strumento volume: OSError: servizio audio non risponde"


# ------------------------------------------------------------------ tutti coperti

TESTED_HERE = {"apri", "volume", "musica", "appunti", "finestra_attiva", "blocca_schermo", "mostra", "nessuna_risposta",
               "ricorda", "cerca_memoria", "dimentica", "avvia_compito", "migliora_jarvis", "messaggio_compito",
               "conferma_compito", "annulla_compito", "stato_compiti", "riposo"}
TESTED_ELSEWHERE = {"impostazioni", "impostazione", "riavvia", "spegniti", "modelli_gratis",  # test_settings_tools.py
                    "usa_app",  # tests/test_apps.py
                    "guarda_video",  # tests/test_media.py
                    "proposte", "rispondi_proposta",  # tests/test_improve.py
                    "programma", "programmi", "togli_programma",  # tests/test_scheduler.py
                    "presentazione",  # tests/test_welcome.py
                    "aggiornamenti",  # tests/test_updates.py
                    "crea_voce",  # tests/test_voice_maker.py
                    "guarda_schermo"}  # tests/test_screen.py


def test_every_tool_is_tested(tools):
    """Uno strumento nuovo senza test fa fallire questo test."""
    reg, _ = tools
    assert set(reg.names()) == TESTED_HERE | TESTED_ELSEWHERE

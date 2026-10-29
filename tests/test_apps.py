"""Le abilità (jarvis/apps): catalogo, permessi, conferme che il cervello non può darsi da solo, e i giochi.

Steam, Epic e Xbox sono finti: cartelle e file costruiti qui come li scrivono i launcher veri, processi e Store finti.
Niente viene avviato davvero: gli indirizzi aperti si registrano.
"""

import json
import time
from pathlib import Path

import pytest

from jarvis.apps import games as G
from jarvis.apps.base import App, AppError, Apps, affirmative
from jarvis.config import ConfigStore
from jarvis.core.bus import Bus
from jarvis.memory.store import Memory
from jarvis.tools.front import Services, build_front_tools

# ------------------------------------------------------------------ il meccanismo comune


class Turns:
    """Chi parla e cosa dice: il contatore dei turni come quello di Conversation."""

    def __init__(self):
        self.n, self.text, self.refuse = 0, "", None

    def say(self, text):
        self.n += 1
        self.text = text


def toy(log):
    app = App("posta", "Posta finta", "messaggi", notes="note della posta", aliases=("mail",))

    def conferma(p):
        if p["a"] == "nessuno":
            raise AppError("non conosco 'nessuno'")
        p["a"] = p["a"].title()  # la risoluzione (es. la chat esatta) viaggia con la conferma
        return f"inviare a {p['a']}: {p['testo']}"

    @app.action("invia", "manda un messaggio", {"a": "destinatario", "testo": "il testo", "file": "? allegato"},
                confirm=conferma)
    def invia(p):
        log.append(("invia", p["a"], p["testo"]))
        return f"inviato a {p['a']}"

    @app.action("leggi", "legge")
    async def leggi(_p):
        log.append(("leggi",))
        return "nessun messaggio"

    @app.action("rompi", "fallisce bene")
    def rompi(_p):
        raise AppError("la posta non risponde")

    return app


@pytest.fixture
def setup():
    log, t = [], Turns()
    clock = [1000.0]
    confirm = [True]
    apps = Apps([toy(log)], may=lambda g: t.refuse, turns=lambda: t.n, last_text=lambda: t.text,
                confirm=lambda: confirm[0], clock=lambda: clock[0])
    return apps, log, t, clock, confirm


def code_of(out):
    assert out.startswith("DA CONFERMARE (codice "), out
    return out.split("codice ")[1].split(")")[0]


async def test_catalog_help_and_aliases(setup):
    apps, log, *_ = setup
    assert apps.catalog() == "posta (solo l'utente): invia(a, testo, [file]), leggi, rompi"
    help_ = await apps.use("mail", "aiuto")
    assert "note della posta" in help_ and "- invia(a, testo, [file]): manda un messaggio [chiede conferma]" in help_
    assert "    file: allegato" in help_
    assert await apps.use("MAIL", "Leggi") == "nessun messaggio" and log == [("leggi",)]


async def test_clear_errors(setup):
    apps, log, *_ = setup
    assert (await apps.use("fax", "leggi")).startswith("Errore: app sconosciuta 'fax'. Ci sono: posta")
    assert (await apps.use("posta", "stampa")).startswith("Errore: posta non ha l'azione 'stampa'. Azioni: invia")
    assert await apps.use("posta", "invia", {"a": "luca"}) == "Errore: per posta.invia servono: testo (vedi aiuto)"
    assert await apps.use("posta", "rompi") == "Errore: la posta non risponde"
    assert log == []


async def test_permission_is_checked_by_code(setup):
    apps, log, t, *_ = setup
    t.refuse = "Errore: permesso negato."
    assert await apps.use("posta", "leggi") == "Errore: permesso negato."
    assert await apps.use("posta", "aiuto") == "Errore: permesso negato."
    assert log == []


async def test_not_ready_only_lets_through_what_does_not_need_it():
    app = App("chat", "Chat", "pc", ready=lambda: "va collegata con collega")

    @app.action("leggi", "legge")
    def leggi(_p):
        return "letto"

    @app.action("collega", "collega", needs_ready=False)
    def collega(_p):
        return "QR mostrato"

    apps = Apps([app])
    assert await apps.use("chat", "leggi") == "Errore: Chat non è pronta: va collegata con collega"
    assert await apps.use("chat", "collega") == "QR mostrato"
    assert "NON PRONTA: va collegata con collega" in await apps.use("chat", "aiuto")


async def test_a_confirmation_needs_a_later_yes_from_the_user(setup):
    apps, log, t, *_ = setup
    t.say("Jarvis, scrivi a luca che arrivo")
    out = await apps.use("posta", "invia", {"a": "luca", "testo": "arrivo"})
    code = code_of(out)
    assert "inviare a Luca: arrivo" in out and log == []
    # lo stesso turno: il cervello non può confermarsi da solo
    assert "non ha ancora risposto" in await apps.use("posta", "invia", {"a": "luca", "testo": "x"}, code)
    assert log == []
    t.say("sì, mandalo")
    # parametri cambiati dopo il sì: parte quello confermato
    assert await apps.use("posta", "invia", {"a": "marco", "testo": "altro"}, code) == "inviato a Luca"
    assert log == [("invia", "Luca", "arrivo")]
    # un codice vale una volta sola
    assert "sconosciuto o scaduto" in await apps.use("posta", "invia", {"a": "x", "testo": "y"}, code)


@pytest.mark.parametrize("answer", ["no", "no, aspetta", "sì ma cambia il testo", "aspetta un attimo",
                                    "che ore sono?", "non mandarlo", "certo che no"])
async def test_anything_but_a_clear_yes_cancels(setup, answer):
    apps, log, t, *_ = setup
    t.say("scrivi a luca")
    code = code_of(await apps.use("posta", "invia", {"a": "luca", "testo": "arrivo"}))
    t.say(answer)
    out = await apps.use("posta", "invia", {}, code)
    assert out.startswith("Errore: l'ultima risposta dell'utente non è un sì chiaro") and log == []
    t.say("sì")  # la richiesta è annullata: un sì dopo non la riporta in vita
    assert "sconosciuto o scaduto" in await apps.use("posta", "invia", {}, code)
    assert log == []


async def test_codes_expire_belong_to_one_action_and_resolution_errors_stop_early(setup):
    apps, log, t, clock, _ = setup
    code = code_of(await apps.use("posta", "invia", {"a": "luca", "testo": "arrivo"}))
    t.say("sì")
    other = App("altra", "Altra", "pc")

    @other.action("invia", "x", confirm=lambda p: "y")
    def _invia(_p):
        return "no"

    apps.apps["altra"] = other
    assert "è per posta.invia, non per altra.invia" in await apps.use("altra", "invia", {}, code)
    clock[0] += Apps.CONFIRM_TTL_S + 1
    assert "sconosciuto o scaduto" in await apps.use("posta", "invia", {}, code)
    assert await apps.use("posta", "invia", {"a": "nessuno", "testo": "x"}) == "Errore: non conosco 'nessuno'"
    assert apps.pending == {} and log == []


async def test_with_confirmations_off_it_acts_at_once(setup):
    apps, log, _t, _c, confirm = setup
    confirm[0] = False
    assert await apps.use("posta", "invia", {"a": "luca", "testo": "arrivo"}) == "inviato a luca"
    assert log == [("invia", "luca", "arrivo")]


@pytest.mark.parametrize("text,yes", [("sì", True), ("si", True), ("Sì, invia pure", True), ("ok", True),
                                      ("vai", True), ("certo", True), ("confermo", True), ("va bene", True),
                                      ("chiudilo pure", True), ("Va benissimo.", True), ("Se", True),
                                      ("se.", True), ("perfetto, vai", True), ("se vuoi", False),
                                      ("non va bene", False), ("se ci penso", False),
                                      ("no", False), ("No grazie", False), ("", False),
                                      ("forse", False), ("sì, ma invece scrivi domani", False),
                                      ("aspetta", False), ("non chiudere", False)])
def test_affirmative(text, yes):
    assert affirmative(text) is yes


# ------------------------------------------------------------------ lo strumento del cervello


@pytest.fixture
def front(tmp_path, setup):
    apps, log, t, *_ = setup
    store = ConfigStore(tmp_path / "config.yaml")
    memory = Memory(tmp_path / "memoria.db")
    reg = build_front_tools(Services(Bus(), store, memory, apps=apps))
    yield reg, log, t
    memory.close()


async def test_usa_app_tool(front):
    reg, log, t = front
    tool = reg.get("usa_app")
    assert tool.group == "pc" and "posta (solo l'utente): invia(a, testo, [file])" in tool.description
    assert tool.parameters["properties"]["app"]["enum"] == ["posta"]
    assert await reg.call("usa_app", {"app": "posta", "azione": "leggi"}) == "nessun messaggio"
    # oggetto mandato come testo JSON: si accetta; testo che non è JSON: errore chiaro
    out = await reg.call("usa_app", {"app": "posta", "azione": "invia",
                                     "parametri": json.dumps({"a": "luca", "testo": "ciao"})})
    code = code_of(out)
    t.say("sì")
    assert await reg.call("usa_app", {"app": "posta", "azione": "invia", "conferma": code}) == "inviato a Luca"
    assert (await reg.call("usa_app", {"app": "posta", "azione": "invia", "parametri": "a=luca"})
            ).startswith("Errore: parametri va scritto come oggetto")
    assert log == [("leggi",), ("invia", "Luca", "ciao")]


# ------------------------------------------------------------------ giochi: un PC finto

ACCOUNT_A, ACCOUNT_B = 111, 222  # id brevi; nello steamid64 si aggiunge la base


def vdf(d, depth=0):
    tab = "\t" * depth
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            out += [f'{tab}"{k}"', f"{tab}{{", vdf(v, depth + 1), f"{tab}}}"]
        else:
            out.append(f'{tab}"{k}"\t\t"{v}"'.replace("\\", "\\\\") if isinstance(v, str) and "\\" in v
                       else f'{tab}"{k}"\t\t"{v}"')
    return "\n".join(out)


def manifest(folder: Path, appid, name, flags=4, size=0, last_played=0):
    folder.joinpath("steamapps").mkdir(parents=True, exist_ok=True)
    (folder / "steamapps" / "common" / name).mkdir(parents=True, exist_ok=True)
    folder.joinpath("steamapps", f"appmanifest_{appid}.acf").write_text(vdf({"AppState": {
        "appid": appid, "name": name, "StateFlags": flags, "installdir": name, "SizeOnDisk": size,
        "LastPlayed": last_played}}), encoding="utf-8")


@pytest.fixture
def pc(tmp_path):
    root, second = tmp_path / "Steam", tmp_path / "Giochi2"
    manifest(root, "1118200", "People Playground", size=457_744_240)
    manifest(root, "228980", "Steamworks Common Redistributables")
    manifest(root, "431960", "Wallpaper Engine", size=800_000_000)
    manifest(root, "2000", "Shipping Store Simulator 2037", flags=6, size=3_900_000_000)
    manifest(second, "3000", "Learn to Fly 3", size=45_000_000, last_played=1_700_000_000)
    manifest(second, "4000", "TerraTech", flags=1024 | 4)
    root.joinpath("steamapps", "libraryfolders.vdf").write_text(vdf({"libraryfolders": {
        "0": {"path": str(root)}, "1": {"path": str(second)}}}), encoding="utf-8")
    (root / "config").mkdir()
    users = {str(G.STEAMID64_BASE + ACCOUNT_A): {"AccountName": "a", "PersonaName": "Zia", "timestamp": "100"},
             str(G.STEAMID64_BASE + ACCOUNT_B): {"AccountName": "b", "PersonaName": "Andrea", "timestamp": "200"}}
    (root / "config" / "loginusers.vdf").write_text(vdf({"users": users}), encoding="utf-8")
    for acc, apps in ((ACCOUNT_A, {"1118200": {"Playtime": "5", "LastPlayed": "1600000000"}}),
                      (ACCOUNT_B, {"1118200": {"Playtime": "725", "LastPlayed": "1790000000"},
                                   "2000": {"Playtime": "90", "LastPlayed": "1795000000"}})):
        cfg = root / "userdata" / str(acc) / "config"
        cfg.mkdir(parents=True)
        cfg.joinpath("localconfig.vdf").write_text(vdf({"UserLocalConfigStore": {"Software": {"Valve": {"Steam": {
            "apps": apps}}}}}), encoding="utf-8")
    epic = tmp_path / "Epic"
    epic.mkdir()
    for name, cats, incomplete in (("20 Minutes Till Dawn", ["games", "applications"], False),
                                   ("Discord", ["public", "software", "applications"], False),
                                   ("Fortnite", ["games"], True)):
        epic.joinpath(f"{name}.item").write_text(json.dumps({
            "DisplayName": name, "AppName": name.replace(" ", "").lower(), "CatalogNamespace": "ns",
            "CatalogItemId": "item", "InstallLocation": str(tmp_path / "Epic Games" / name), "InstallSize": 133_450_712,
            "bIsIncompleteInstall": incomplete, "AppCategories": cats}), encoding="utf-8")
    xbox_calls = []

    def xbox():
        xbox_calls.append(1)
        return [G.Game("Minecraft for Windows", "Xbox", "Microsoft.MinecraftUWP", tmp_path / "WindowsApps" / "mc",
                       launch="shell:AppsFolder\\Microsoft.MinecraftUWP_8w!Game"),
                G.Game("Minecraft: Java Edition", "Xbox", "Microsoft.MinecraftJavaEdition",
                       tmp_path / "WindowsApps" / "mcj", launch="shell:AppsFolder\\Microsoft.MinecraftJava_8w!Game")]

    registry = {"ActiveProcess\\ActiveUser": 0, "RunningAppID": 0}
    clock = [0.0]
    lib = G.Library(root, epic, xbox, registry=lambda name: registry.get(name), clock=lambda: clock[0])

    class Procs:
        def __init__(self):
            self.list: list[tuple[int, str]] = []
            self.closed, self.killed = [], []
            self.survivors: list[int] = []
            self.windows = 1

        def running(self):
            return list(self.list)

        def close(self, pids):
            self.closed.append(list(pids))
            return self.windows

        def wait(self, pids, timeout_s):
            return [p for p in pids if p in self.survivors]

        def kill(self, pids):
            self.killed.append(list(pids))

    class PC:
        pass

    p = PC()
    p.root, p.second, p.tmp, p.lib, p.registry, p.clock, p.xbox_calls = root, second, tmp_path, lib, registry, clock, \
        xbox_calls
    p.opened, p.procs, p.searches = [], Procs(), []
    p.store = [("620", "Portal 2"), ("400", "Portal")]

    def search(term):
        p.searches.append(term)
        if p.store is None:
            raise OSError("rete assente")
        return p.store

    t = Turns()
    p.turns = t
    p.app = G.build(lib, open_uri=p.opened.append, procs=p.procs, search=search,
                    disk=lambda d: type("U", (), {"free": 27_200_000_000})(), close_wait_s=0.01,
                    now=lambda: 1_795_100_000.0)
    p.apps = Apps([p.app], turns=lambda: t.n, last_text=lambda: t.text)
    return p


def test_parse_vdf_with_escapes_and_any_case():
    d = G.parse_vdf('"Root"\n{\n\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n\t"quote"\t"a \\"b\\""\n'
                    '\t"Apps" { "10" { "Playtime" "5" } }\n\t"vuoto"\t""\n}\n')
    assert G.vget(d, "root", "PATH") == "C:\\Program Files (x86)\\Steam"
    assert G.vget(d, "root", "quote") == 'a "b"' and G.vget(d, "root", "vuoto") == ""
    assert G.vget(d, "ROOT", "apps", "10", "playtime") == "5" and G.vget(d, "root", "manca", "x") is None


def test_library_reads_steam_epic_and_xbox(pc):
    names = [g.name for g in pc.lib.games()]
    assert names == ["20 Minutes Till Dawn", "Fortnite", "Learn to Fly 3", "Minecraft for Windows",
                     "Minecraft: Java Edition", "People Playground", "Shipping Store Simulator 2037", "TerraTech",
                     "Wallpaper Engine"]  # niente Steamworks Redistributables né Discord (app, non gioco)
    by = {g.name: g for g in pc.lib.games()}
    assert by["Shipping Store Simulator 2037"].state == "da aggiornare"
    assert by["TerraTech"].state == "in aggiornamento" and by["Fortnite"].state == "non completo"
    assert by["Learn to Fly 3"].folder == pc.second / "steamapps" / "common" / "Learn to Fly 3"
    assert by["20 Minutes Till Dawn"].launch == ("com.epicgames.launcher://apps/ns%3Aitem%3A20minutestilldawn"
                                                "?action=launch&silent=true")
    # ore e ultima partita: l'account con l'accesso più recente (Steam chiuso)
    assert by["People Playground"].playtime_min == 725 and by["People Playground"].last_played == 1_790_000_000
    assert by["Learn to Fly 3"].last_played == 1_700_000_000  # dal manifest se l'account non lo ha
    assert pc.lib.steam_account() == (str(ACCOUNT_B), "Andrea")


def test_the_logged_in_account_wins_and_xbox_is_read_rarely(pc):
    pc.registry["ActiveProcess\\ActiveUser"] = ACCOUNT_A
    assert pc.lib.steam_account() == (str(ACCOUNT_A), "Zia")
    assert {g.name: g.playtime_min for g in pc.lib.games()}["People Playground"] == 5
    pc.lib.games(refresh=True)
    pc.clock[0] += 60
    pc.lib.games()
    assert len(pc.xbox_calls) == 1  # Xbox (lento) non si rilegge a ogni ricarica
    pc.clock[0] += 601
    pc.lib.games()
    assert len(pc.xbox_calls) == 2


@pytest.mark.parametrize("said,found", [("people playground", "People Playground"), ("PEOPLE", "People Playground"),
                                        ("peopleplayground", "People Playground"), ("learn to fly tre", "Learn to Fly 3"),
                                        ("fly 3", "Learn to Fly 3"), ("shipping simulator", "Shipping Store Simulator 2037"),
                                        ("terratek", "TerraTech"), ("minecraft java", "Minecraft: Java Edition")])
def test_pick_understands_spoken_names(pc, said, found):
    assert pc.lib.find(said).name == found


def test_pick_asks_instead_of_guessing(pc):
    with pytest.raises(AppError, match="più giochi corrispondono a 'minecraft': Minecraft for Windows, Minecraft: Java"):
        pc.lib.find("minecraft")
    with pytest.raises(AppError, match="non trovo un gioco installato chiamato 'zelda'"):
        pc.lib.find("zelda")
    with pytest.raises(AppError, match="quale gioco"):
        pc.lib.find("  ")


async def test_list_info_and_launch(pc):
    a = pc.apps
    out = await a.use("giochi", "elenco")
    assert out.startswith("9 giochi: 20 Minutes Till Dawn (Epic, 127 MB); Fortnite (Epic, 127 MB, non completo)")
    assert "Shipping Store Simulator 2037 (Steam, 3,6 GB, da aggiornare)" in out
    assert await a.use("giochi", "elenco", {"filtro": "xbox"}) == ("2 giochi: Minecraft for Windows (Xbox); "
                                                                  "Minecraft: Java Edition (Xbox)")
    assert await a.use("giochi", "elenco", {"filtro": "da aggiornare"}) == (
        "1 giochi: Shipping Store Simulator 2037 (Steam, 3,6 GB, da aggiornare)")
    assert await a.use("giochi", "elenco", {"filtro": "zelda"}) == "nessun gioco corrisponde a 'zelda'"
    info = await a.use("giochi", "info", {"gioco": "people"})
    assert info == ("People Playground: Steam, stato installato, occupa 437 MB, giocato 12 ore, ultima partita "
                    f"21/09/2026, cartella {pc.root / 'steamapps' / 'common' / 'People Playground'}")
    assert await a.use("giochi", "avvia", {"gioco": "people"}) == "avviato People Playground da Steam"
    assert await a.use("giochi", "avvia", {"gioco": "shipping"}) == (
        "avviato Shipping Store Simulator 2037 da Steam (Steam lo aggiornerà prima di avviarlo)")
    assert await a.use("steam", "avvia", {"gioco": "minecraft windows"}) == "avviato Minecraft for Windows da Xbox"
    assert await a.use("giochi", "avvia", {"gioco": "20 minutes"}) == "avviato 20 Minutes Till Dawn da Epic"
    assert await a.use("giochi", "avvia", {"gioco": "fortnite"}) == (
        "Errore: Fortnite non è installato del tutto: prima va completata l'installazione")
    assert pc.opened == ["steam://rungameid/1118200", "steam://rungameid/2000",
                         "shell:AppsFolder\\Microsoft.MinecraftUWP_8w!Game",
                         "com.epicgames.launcher://apps/ns%3Aitem%3A20minutestilldawn?action=launch&silent=true"]


async def test_running_games_and_wallpaper_engine_is_not_one(pc):
    a = pc.apps
    wallpaper = str(pc.root / "steamapps" / "common" / "Wallpaper Engine" / "wallpaper64.exe")
    pc.procs.list = [(5, wallpaper), (9, "C:\\Windows\\explorer.exe")]
    assert await a.use("giochi", "in_corso") == "nessun gioco in corso"
    pc.procs.list.append((7, str(pc.second / "steamapps" / "common" / "Learn to Fly 3" / "game.exe")))
    assert await a.use("giochi", "in_corso") == "in corso: Learn to Fly 3"
    pc.registry["RunningAppID"] = 1118200  # Steam lo dice anche se il processo sta altrove
    assert await a.use("giochi", "in_corso") == "in corso: Learn to Fly 3, People Playground"


async def test_closing_a_game_asks_first_then_is_gentle(pc):
    a, t = pc.apps, pc.turns
    game = str(pc.second / "steamapps" / "common" / "Learn to Fly 3" / "game.exe")
    wallpaper = str(pc.root / "steamapps" / "common" / "Wallpaper Engine" / "wallpaper64.exe")
    pc.procs.list = [(7, game), (8, game), (5, wallpaper)]
    t.say("chiudi il gioco")
    out = await a.use("giochi", "chiudi")
    assert "chiudere Learn to Fly 3 (come la X della finestra" in out and pc.procs.closed == []
    t.say("sì chiudilo")
    assert await a.use("giochi", "chiudi", {}, code_of(out)) == "Learn to Fly 3 chiuso"
    assert pc.procs.closed == [[7, 8]] and pc.procs.killed == []
    # non si chiude: si propone la forza, che chiede di nuovo conferma
    pc.procs.survivors = [7]
    t.say("chiudi learn to fly")
    out = await a.use("giochi", "chiudi", {"gioco": "learn to fly"})
    t.say("sì")
    assert "non si è chiuso (dopo 0.01 secondi è ancora aperto)" in await a.use("giochi", "chiudi", {}, code_of(out))
    out = await a.use("giochi", "chiudi", {"gioco": "learn to fly", "forza": "sì"})
    assert "chiudere A FORZA Learn to Fly 3" in out
    t.say("sì")
    assert await a.use("giochi", "chiudi", {}, code_of(out)) == "Learn to Fly 3 chiuso a forza"
    assert pc.procs.killed == [[7, 8]]
    # Wallpaper Engine si chiude solo se lo si nomina
    pc.procs.list = [(5, wallpaper)]
    assert await a.use("giochi", "chiudi") == "Errore: nessun gioco in corso"
    assert "chiudere Wallpaper Engine" in await a.use("giochi", "chiudi", {"gioco": "wallpaper"})


async def test_closing_edge_cases(pc):
    a, t = pc.apps, pc.turns
    pc.procs.list = [(7, str(pc.second / "steamapps" / "common" / "Learn to Fly 3" / "g.exe")),
                     (3, str(pc.root / "steamapps" / "common" / "People Playground" / "pp.exe"))]
    assert await a.use("giochi", "chiudi") == ("Errore: ci sono più giochi aperti: Learn to Fly 3, People Playground. "
                                               "Quale chiudo?")
    assert await a.use("giochi", "chiudi", {"gioco": "terratech"}) == "Errore: TerraTech non è in corso"
    out = await a.use("giochi", "chiudi", {"gioco": "people"})
    pc.procs.list = pc.procs.list[:1]  # nel frattempo l'utente l'ha chiuso da sé
    t.say("sì")
    assert await a.use("giochi", "chiudi", {}, code_of(out)) == (
        "niente da chiudere: People Playground non è in corso")
    pc.procs.windows = 0
    pc.procs.survivors = [7]
    pc.apps.confirm_on = lambda: False
    assert "non si è chiuso (non ha finestre a cui chiederlo)" in await a.use("giochi", "chiudi")
    pc.procs.list, pc.registry["RunningAppID"] = [], 3000
    assert await a.use("giochi", "chiudi") == (
        "Errore: Steam dice che Learn to Fly 3 è in corso ma non trovo il suo processo")


async def test_install_uninstall_verify(pc):
    a, t = pc.apps, pc.turns
    assert await a.use("giochi", "installa", {"gioco": "people"}) == (
        "People Playground è già installato (Steam, installato)")
    out = await a.use("giochi", "installa", {"gioco": "portal"})
    assert out.startswith("Steam ha aperto l'installazione di Portal: si conferma lì")  # il nome esatto vince
    assert await a.use("giochi", "installa", {"gioco": "730"}) != "" and pc.opened[-1] == "steam://install/730"
    assert pc.searches == ["portal"]  # col numero non si cerca
    pc.store = []
    assert await a.use("giochi", "installa", {"gioco": "gioco inventato"}) == (
        "Errore: nello Store di Steam non trovo 'gioco inventato'")
    pc.store = None
    assert (await a.use("giochi", "installa", {"gioco": "gioco inventato"})).startswith(
        "Errore: lo Store di Steam non risponde")
    # disinstallare: conferma con lo spazio liberato, poi decide Steam
    out = await a.use("giochi", "disinstalla", {"gioco": "people"})
    assert "disinstallare People Playground da Steam (libera 437 MB" in out
    t.say("sì")
    assert await a.use("giochi", "disinstalla", {}, code_of(out)) == (
        "Steam ha aperto la disinstallazione di People Playground: l'ultima conferma è nella sua finestra")
    assert await a.use("giochi", "disinstalla", {"gioco": "20 minutes"}) == (
        "Errore: Disinstallare lo so fare solo per i giochi di Steam: 20 Minutes Till Dawn è di Epic (per questo "
        "serve un compito)")
    assert await a.use("giochi", "verifica", {"gioco": "terratech"}) == "Steam sta verificando i file di TerraTech"
    assert pc.opened == ["steam://install/400", "steam://install/730", "steam://uninstall/1118200",
                         "steam://validate/4000"]


async def test_updates_recent_space(pc):
    a = pc.apps
    assert await a.use("giochi", "aggiornamenti") == (
        "Fortnite (Epic, 127 MB, non completo); Shipping Store Simulator 2037 (Steam, 3,6 GB, da aggiornare); "
        "TerraTech (Steam, in aggiornamento)")
    assert await a.use("giochi", "recenti") == (
        "Shipping Store Simulator 2037: ieri, 1,5 ore in tutto; People Playground: 21/09/2026, 12 ore in tutto; "
        "Learn to Fly 3: 14/11/2023")
    out = await a.use("giochi", "spazio")
    assert out.startswith("i giochi occupano 5,1 GB; i più grandi: Shipping Store Simulator 2037 3,6 GB, "
                          "Wallpaper Engine 763 MB, People Playground 437 MB")
    assert "libero 25,3 GB" in out


async def test_steam_pages_sections_status_folders_account(pc):
    a = pc.apps
    assert await a.use("giochi", "negozio", {"gioco": "people"}) == (
        "aperta in Steam la pagina dello Store di People Playground")
    assert await a.use("giochi", "negozio", {"gioco": "portal 2"}) == "aperta in Steam la pagina dello Store di Portal 2"
    assert await a.use("giochi", "pagina", {"gioco": "terratech"}) == "aperta la scheda di TerraTech in Steam"
    assert await a.use("giochi", "steam", {"sezione": "Download"}) == "aperta la sezione download di Steam"
    assert await a.use("giochi", "steam", {"sezione": "esci"}) == "chiusura di Steam richiesta"
    assert (await a.use("giochi", "steam", {"sezione": "garage"})).startswith("Errore: sezione sconosciuta 'garage'")
    assert await a.use("giochi", "stato", {"stato": "Invisibile"}) == "stato di Steam: invisibile"
    assert (await a.use("giochi", "stato", {"stato": "felice"})).startswith("Errore: stato sconosciuto 'felice'")
    assert await a.use("giochi", "cartella", {"gioco": "learn to fly"}) == "aperta la cartella di Learn to Fly 3"
    assert await a.use("giochi", "cartella", {"gioco": "minecraft windows"}) == "Errore: non trovo la cartella di " \
                                                                                 "Minecraft for Windows"
    assert await a.use("giochi", "account") == "account Steam Andrea: l'ultimo usato (Steam è chiuso)"
    pc.registry["ActiveProcess\\ActiveUser"] = ACCOUNT_A
    assert await a.use("giochi", "account") == "account Steam Zia: collegato adesso"
    assert pc.opened == ["steam://store/1118200", "steam://store/620", "steam://nav/games/details/4000",
                         "steam://open/downloads", "steam://exit", "steam://friends/status/invisible",
                         str(pc.second / "steamapps" / "common" / "Learn to Fly 3")]


def test_formats():
    assert [G.hours(m) for m in (45, 60, 66, 90, 600, 725)] == ["45 minuti", "1 ora", "1,1 ore", "1,5 ore", "10 ore",
                                                               "12 ore"]
    assert [G.gb(n) for n in (16_000_000, 3_900_000_000)] == ["15 MB", "3,6 GB"]
    now = time.mktime((2026, 10, 27, 12, 0, 0, 0, 0, -1))
    assert G.when(now - 3600, now) == "oggi" and G.when(now - 86400, now) == "ieri"
    assert G.when(None, now) == "mai" and G.when(now - 10 * 86400, now) == "17/10/2026"

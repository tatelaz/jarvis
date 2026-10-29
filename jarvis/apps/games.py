"""Giochi: Steam, Epic e Xbox letti dai loro file sul PC e comandati con i loro indirizzi (steam://,
com.epicgames.launcher://, shell:AppsFolder). Niente finestre in primo piano: si apre solo ciò che deve aprirsi, cioè il
gioco, oppure Steam quando la conferma la chiede lui (installa, disinstalla).

- I giochi installati sono quelli del PC, uguali per ogni account Steam. Ore di gioco e ultima partita sono dell'account
  collegato adesso (se Steam è chiuso, dell'ultimo usato).
- Chiudere un gioco è gentile, come la X della finestra; a forza solo se l'utente lo conferma (si perde ciò che non è
  salvato).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .base import App, AppError, norm, pick_by_name

STEAM_NOT_GAMES = {"228980"}  # Steamworks Common Redistributables
# programmi distribuiti da Steam che restano sempre accesi: si avviano come i giochi ma non sono "il gioco in corso"
STEAM_ALWAYS_ON = {"431960"}  # Wallpaper Engine
EPIC_MANIFESTS = (Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data"
                  / "Manifests")
STEAMID64_BASE = 76561197960265728
CREATE_NO_WINDOW = 0x08000000

# EAppState di Steam (StateFlags nei manifest)
_UPDATE_REQUIRED, _INSTALLED = 2, 4
_UPDATE_RUNNING, _UPDATE_PAUSED, _UPDATE_STARTED, _DOWNLOADING = 256, 512, 1024, 1048576

STEAM_SECTIONS = {"libreria": "steam://open/games", "download": "steam://open/downloads",
                  "amici": "steam://open/friends", "impostazioni": "steam://open/settings",
                  "screenshot": "steam://open/screenshots", "console": "steam://open/console",
                  "negozio": "steam://store", "esci": "steam://exit"}
STEAM_STATUS = {"online": "online", "in linea": "online", "assente": "away", "occupato": "busy",
                "invisibile": "invisible", "offline": "offline", "non in linea": "offline"}

def parse_vdf(text: str) -> dict[str, Any]:
    """Il formato KeyValues di Valve (libraryfolders.vdf, appmanifest_*.acf, localconfig.vdf, loginusers.vdf)."""
    root: dict[str, Any] = {}
    stack, key = [root], None
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        if m.group(2) == "{":
            child: dict[str, Any] = {}
            stack[-1][key if key is not None else ""] = child
            stack.append(child)
            key = None
        elif m.group(2) == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return root


def vget(d: Any, *path: str) -> Any:
    """d[a][b]... senza badare a maiuscole e minuscole (Valve scrive "apps" o "Apps")."""
    for p in path:
        if not isinstance(d, dict):
            return None
        low = p.lower()
        d = next((v for k, v in d.items() if k.lower() == low), None)
    return d


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@dataclass
class Game:
    name: str
    launcher: str  # Steam, Epic, Xbox
    id: str  # appid di Steam, AppName di Epic, nome del pacchetto Xbox
    folder: Path | None = None
    size: int = 0  # byte su disco
    state: str = "installato"  # installato | da aggiornare | in aggiornamento | aggiornamento in pausa | non completo
    launch: str = ""  # indirizzo che lo avvia
    playtime_min: int | None = None  # solo Steam, account collegato
    last_played: float | None = None  # solo Steam (secondi epoch)


def _steam_state(flags: int) -> str:
    if flags & (_UPDATE_RUNNING | _UPDATE_STARTED | _DOWNLOADING):
        return "in aggiornamento"
    if flags & _UPDATE_PAUSED:
        return "aggiornamento in pausa"
    if flags & _UPDATE_REQUIRED:
        return "da aggiornare"
    return "installato" if flags & _INSTALLED else "non completo"


def steam_path() -> Path | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        default = Path(r"C:\Program Files (x86)\Steam")
        return default if default.exists() else None


def steam_registry(name: str) -> int | None:
    """RunningAppID (gioco in corso, 0 = nessuno) o ActiveProcess\\ActiveUser (account collegato, 0 = nessuno)."""
    try:
        import winreg

        sub, _, value = name.rpartition("\\")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\Valve\\Steam" + ("\\" + sub if sub else "")) as k:
            return int(winreg.QueryValueEx(k, value)[0])
    except (OSError, ValueError):
        return None


def xbox_games() -> list[Game]:
    """I giochi del Microsoft Store / Xbox: i pacchetti con MicrosoftGame.config (1-3 s, si tiene in cache)."""
    cmd = ("$ErrorActionPreference='SilentlyContinue'; Get-AppxPackage | Where-Object { -not $_.IsFramework -and "
           "$_.InstallLocation -and (Test-Path (Join-Path $_.InstallLocation 'MicrosoftGame.config')) } | "
           "ForEach-Object { $m = Get-AppxPackageManifest $_; [pscustomobject]@{ name=[string]$m.Package.Properties."
           "DisplayName; pkg=$_.Name; pfn=$_.PackageFamilyName; app=[string](@($m.Package.Applications.Application)"
           "[0].Id); dir=$_.InstallLocation } } | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                             text=True, timeout=30, creationflags=CREATE_NO_WINDOW).stdout.strip()
        rows = json.loads(out) if out else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    games = []
    for r in rows if isinstance(rows, list) else [rows]:
        name = r.get("name") or ""
        if not name or name.startswith("ms-resource:"):
            name = r.get("pkg") or "?"
        games.append(Game(name, "Xbox", r.get("pkg") or name, Path(r["dir"]) if r.get("dir") else None,
                          launch=f"shell:AppsFolder\\{r.get('pfn')}!{r.get('app')}"))
    return games


class Library:
    """Tutti i giochi installati sul PC, riletti al massimo ogni ttl_s secondi (Xbox ogni 10 minuti: è lento)."""

    def __init__(self, steam_root: Path | None = None, epic_dir: Path = EPIC_MANIFESTS,
                 xbox: Callable[[], list[Game]] = xbox_games, registry: Callable[[str], int | None] = steam_registry,
                 ttl_s: float = 30.0, xbox_ttl_s: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.steam_root = steam_root
        self.epic_dir = epic_dir
        self.xbox = xbox
        self.registry = registry
        self.ttl_s, self.xbox_ttl_s = ttl_s, xbox_ttl_s
        self.clock = clock
        self._games: list[Game] | None = None
        self._at = 0.0
        self._xbox: list[Game] = []
        self._xbox_at: float | None = None

    def games(self, refresh: bool = False) -> list[Game]:
        now = self.clock()
        if refresh or self._games is None or now - self._at > self.ttl_s:
            if self._xbox_at is None or now - self._xbox_at > self.xbox_ttl_s:  # lento: non a ogni ricarica
                self._xbox, self._xbox_at = self.xbox(), now
            self._games = sorted(self._steam() + self._epic() + list(self._xbox), key=lambda g: g.name.lower())
            self._at = now
        return self._games

    # ------------------------------------------------------------------ Steam

    def steam_folders(self) -> list[Path]:
        root = self.steam_root
        if root is None or not (root / "steamapps").is_dir():
            return []
        folders, seen = [], set()
        lf = parse_vdf(_read(root / "steamapps" / "libraryfolders.vdf"))
        extra = [Path(v["path"]) for v in (vget(lf, "libraryfolders") or {}).values()
                 if isinstance(v, dict) and v.get("path")]
        for p in [root, *extra]:
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen and (p / "steamapps").is_dir():
                seen.add(k)
                folders.append(p)
        return folders

    def steam_account(self) -> tuple[str, str] | None:
        """(id dell'account, nome visibile) di chi è collegato; se Steam è chiuso, dell'ultimo usato."""
        if self.steam_root is None:
            return None
        users = vget(parse_vdf(_read(self.steam_root / "config" / "loginusers.vdf")), "users") or {}
        active = self.registry("ActiveProcess\\ActiveUser") or 0
        best: tuple[tuple[bool, int], str, str] | None = None
        for sid, u in users.items():
            if not isinstance(u, dict) or not sid.isdigit():
                continue
            account, persona = str(int(sid) - STEAMID64_BASE), vget(u, "PersonaName") or "?"
            if active and account == str(active):
                return account, persona
            ts = str(vget(u, "timestamp") or "0")
            rank = (vget(u, "MostRecent") == "1", int(ts) if ts.isdigit() else 0)  # MostRecent: Steam più vecchi
            if best is None or rank > best[0]:
                best = (rank, account, persona)
        if active:
            return str(active), "?"
        return (best[1], best[2]) if best else None

    def _steam_stats(self) -> dict[str, tuple[int | None, float | None]]:
        acc = self.steam_account()
        if acc is None or self.steam_root is None:
            return {}
        apps = vget(parse_vdf(_read(self.steam_root / "userdata" / acc[0] / "config" / "localconfig.vdf")),
                    "UserLocalConfigStore", "Software", "Valve", "Steam", "apps") or {}
        out = {}
        for appid, v in apps.items():
            if isinstance(v, dict):
                pt, lp = v.get("Playtime"), v.get("LastPlayed")
                out[appid] = (int(pt) if pt and pt.isdigit() else None, float(lp) if lp and lp.isdigit() else None)
        return out

    def _steam(self) -> list[Game]:
        stats = self._steam_stats()
        games = []
        for folder in self.steam_folders():
            for f in sorted((folder / "steamapps").glob("appmanifest_*.acf")):
                st = vget(parse_vdf(_read(f)), "AppState") or {}
                appid = str(st.get("appid") or "")
                if not appid or appid in STEAM_NOT_GAMES:
                    continue
                pt, lp = stats.get(appid, (None, None))
                own_lp = st.get("LastPlayed")
                if lp is None and own_lp and own_lp.isdigit() and int(own_lp) > 0:
                    lp = float(own_lp)
                inst = st.get("installdir")
                games.append(Game(st.get("name") or appid, "Steam", appid,
                                  folder / "steamapps" / "common" / inst if inst else None,
                                  int(st.get("SizeOnDisk") or 0), _steam_state(int(st.get("StateFlags") or 0)),
                                  f"steam://rungameid/{appid}", pt, lp))
        return games

    # ------------------------------------------------------------------ Epic

    def _epic(self) -> list[Game]:
        games = []
        for f in sorted(self.epic_dir.glob("*.item")) if self.epic_dir.is_dir() else []:
            try:
                d = json.loads(_read(f))
            except ValueError:
                continue
            cats = [c.get("path") if isinstance(c, dict) else c for c in d.get("AppCategories") or []]
            if cats and "games" not in cats:  # Discord installato da Epic non è un gioco
                continue
            ns, item, app = d.get("CatalogNamespace"), d.get("CatalogItemId"), d.get("AppName")
            if not app:
                continue
            games.append(Game(d.get("DisplayName") or app, "Epic", app,
                              Path(d["InstallLocation"]) if d.get("InstallLocation") else None,
                              int(d.get("InstallSize") or 0),
                              "non completo" if d.get("bIsIncompleteInstall") else "installato",
                              f"com.epicgames.launcher://apps/{ns}%3A{item}%3A{app}?action=launch&silent=true"))
        return games

    # ------------------------------------------------------------------ cerca

    def find(self, name: str) -> Game:
        return pick(str(name), self.games())


def pick(name: str, games: list[Game]) -> Game:
    """Il gioco che l'utente intende. Se più giochi vanno bene si chiede quale, mai indovinare."""
    return pick_by_name(name, games, lambda g: g.name, plural="giochi", empty="quale gioco?",
                        missing=f"non trovo un gioco installato chiamato '{name}' (elenco per vederli, installa per "
                                "uno nuovo)")


# ---------------------------------------------------------------------- processi


class Processes:
    """I processi dei giochi: chi gira, chiusura gentile (WM_CLOSE alle finestre), a forza se confermato."""

    def running(self) -> list[tuple[int, str]]:
        import psutil

        out = []
        for p in psutil.process_iter(["pid", "exe"]):
            exe = p.info.get("exe")
            if exe:
                out.append((p.info["pid"], exe))
        return out

    def close(self, pids: list[int]) -> int:
        """Chiede alle finestre visibili di quei processi di chiudersi; quante ne ha trovate."""
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        wanted, hits = set(pids), []
        proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def each(hwnd, _lp):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in wanted and user32.IsWindowVisible(hwnd):
                hits.append(hwnd)
            return True

        user32.EnumWindows(proto(each), 0)
        for hwnd in hits:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return len(hits)

    def wait(self, pids: list[int], timeout_s: float) -> list[int]:
        """I processi ancora vivi dopo timeout_s."""
        import psutil

        procs = []
        for pid in pids:
            try:
                procs.append(psutil.Process(pid))
            except psutil.Error:
                pass
        _gone, alive = psutil.wait_procs(procs, timeout=timeout_s)
        return [p.pid for p in alive]

    def kill(self, pids: list[int]) -> None:
        import psutil

        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass


def store_search(term: str) -> list[tuple[str, str]]:
    """Cerca nello Store di Steam: [(appid, nome)]. API pubblica, niente account."""
    url = ("https://store.steampowered.com/api/storesearch/?" +
           urllib.parse.urlencode({"term": term, "l": "italian", "cc": "IT"}))
    with urllib.request.urlopen(url, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [(str(i["id"]), i.get("name") or str(i["id"])) for i in data.get("items") or [] if i.get("id")]


# ---------------------------------------------------------------------- l'app


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}".replace(".", ",") + " GB" if n >= 1024 ** 3 else f"{round(n / 1024 ** 2)} MB"


def hours(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minuti"
    h = minutes / 60
    if h >= 10:
        return f"{round(h)} ore"
    t = f"{h:.1f}".replace(".", ",").removesuffix(",0")
    return "1 ora" if t == "1" else f"{t} ore"


def when(ts: float | None, now: float) -> str:
    if not ts:
        return "mai"
    days = (date.fromtimestamp(now) - date.fromtimestamp(ts)).days
    return {0: "oggi", 1: "ieri"}.get(days, time.strftime("%d/%m/%Y", time.localtime(ts)))


def build(lib: Library, open_uri: Callable[[str], Any] | None = None, procs: Processes | None = None,
          search: Callable[[str], list[tuple[str, str]]] = store_search,
          disk: Callable[[Path], Any] = shutil.disk_usage, close_wait_s: float = 8.0,
          now: Callable[[], float] = time.time) -> App:
    opener = open_uri or os.startfile
    pr = procs or Processes()
    app = App("giochi", "Giochi (Steam, Epic, Xbox)", "pc", aliases=("steam", "epic", "xbox", "gioco", "videogiochi"),
              notes="I giochi installati sul PC (uguali per ogni account Steam); ore di gioco e ultima partita sono "
                    "dell'account Steam collegato. Lavora in background: compare solo il gioco, o Steam quando chiede "
                    "lui una conferma (installa, disinstalla). Amici, chat, obiettivi, impostazioni dentro un gioco: "
                    "avvia_compito.",
              # controllo leggero: si fa a ogni richiesta, prima di leggere la libreria
              ready=lambda: None if lib.steam_root is not None or lib.epic_dir.is_dir() or lib.xbox is not None
              else "non trovo né Steam né altri launcher su questo PC")

    def line(g: Game) -> str:
        extra = [g.launcher, gb(g.size)] if g.size else [g.launcher]
        if g.state != "installato":
            extra.append(g.state)
        return f"{g.name} ({', '.join(extra)})"

    def running_map(named: bool = False) -> dict[str, tuple[Game, list[int]]]:
        games = lib.games()
        procs_now = pr.running()
        found: dict[str, tuple[Game, list[int]]] = {}
        for g in games:
            if g.folder is None or (g.launcher == "Steam" and g.id in STEAM_ALWAYS_ON and not named):
                continue
            prefix = os.path.normcase(str(g.folder)).rstrip("\\/") + os.sep
            pids = [pid for pid, exe in procs_now if os.path.normcase(exe).startswith(prefix)]
            if pids:
                found[g.name] = (g, pids)
        steam_now = lib.registry("RunningAppID") or 0
        for g in games:
            if g.launcher == "Steam" and g.id == str(steam_now) and g.name not in found:
                found[g.name] = (g, [])
        return found

    def steam_only(g: Game, what: str) -> None:
        if g.launcher != "Steam":
            raise AppError(f"{what} lo so fare solo per i giochi di Steam: {g.name} è di {g.launcher} "
                           "(per questo serve un compito)")

    @app.action("elenco", "i giochi installati, con launcher, spazio e stato",
                {"filtro": "? parte del nome, un launcher (steam, epic, xbox) o uno stato (es. da aggiornare)"})
    def elenco(a):
        games = lib.games(refresh=True)
        f = norm(str(a.get("filtro") or ""))
        if f:
            games = [g for g in games if f in norm(g.name) or f == norm(g.launcher) or f in norm(g.state)]
        if not games:
            return "nessun gioco" + (f" corrisponde a '{a['filtro']}'" if f else " installato")
        return f"{len(games)} giochi: " + "; ".join(line(g) for g in games)

    @app.action("info", "tutto su un gioco: launcher, spazio, stato, cartella, ore giocate, ultima partita",
                {"gioco": "nome, anche approssimativo"})
    def info(a):
        g = lib.find(a["gioco"])
        rows = [f"{g.name}: {g.launcher}", f"stato {g.state}"]
        if g.size:
            rows.append(f"occupa {gb(g.size)}")
        if g.playtime_min is not None:
            rows.append(f"giocato {hours(g.playtime_min)}")
        if g.launcher == "Steam":
            rows.append(f"ultima partita {when(g.last_played, now())}")
        if g.folder:
            rows.append(f"cartella {g.folder}")
        return ", ".join(rows)

    @app.action("avvia", "avvia un gioco", {"gioco": "nome, anche approssimativo"})
    def avvia(a):
        g = lib.find(a["gioco"])
        if g.state == "non completo":
            raise AppError(f"{g.name} non è installato del tutto: prima va completata l'installazione")
        opener(g.launch)
        note = " (Steam lo aggiornerà prima di avviarlo)" if g.state == "da aggiornare" else ""
        return f"avviato {g.name} da {g.launcher}{note}"

    @app.action("in_corso", "quali giochi sono aperti adesso")
    def in_corso(_a):
        found = running_map()
        return ("in corso: " + ", ".join(found)) if found else "nessun gioco in corso"

    def _target(a) -> tuple[Game, list[int]]:
        found = running_map(named=bool(a.get("gioco")))
        if a.get("gioco"):
            g = lib.find(a["gioco"])
            if g.name not in found:
                raise AppError(f"{g.name} non è in corso")
            return found[g.name]
        if not found:
            raise AppError("nessun gioco in corso")
        if len(found) > 1:
            raise AppError(f"ci sono più giochi aperti: {', '.join(found)}. Quale chiudo?")
        return next(iter(found.values()))

    def _force(a) -> bool:
        return str(a.get("forza") or "").strip().lower() in ("sì", "si", "true", "1")

    def chiudi_conferma(a):
        g = _target(a)[0]
        a["gioco"] = g.name  # si chiude quello confermato, anche se nel frattempo se ne apre un altro
        return (f"chiudere A FORZA {g.name}: si perde tutto ciò che non è salvato" if _force(a) else
                f"chiudere {g.name} (come la X della finestra: ciò che non è salvato si perde)")

    @app.action("chiudi", "chiude un gioco aperto (gentile; a forza solo con forza=sì)",
                {"gioco": "? nome; vuoto = l'unico gioco aperto", "forza": "? sì per chiuderlo a forza"},
                confirm=chiudi_conferma)
    def chiudi(a):
        try:
            g, pids = _target(a)
        except AppError as e:
            return f"niente da chiudere: {e}"
        if not pids:
            raise AppError(f"Steam dice che {g.name} è in corso ma non trovo il suo processo")
        if _force(a):
            pr.kill(pids)
            return f"{g.name} chiuso a forza"
        windows = pr.close(pids)
        alive = pr.wait(pids, close_wait_s)
        if not alive:
            return f"{g.name} chiuso"
        why = "non ha finestre a cui chiederlo" if not windows else f"dopo {close_wait_s:g} secondi è ancora aperto"
        return (f"{g.name} non si è chiuso ({why}): forse chiede di salvare. Se l'utente vuole, richiedi chiudi con "
                "forza=sì")

    def _store_pick(name: str) -> tuple[str, str]:
        if str(name).strip().isdigit():
            return str(name).strip(), str(name).strip()
        try:
            hits = search(name)
        except OSError as e:
            raise AppError(f"lo Store di Steam non risponde ({e})") from e
        if not hits:
            raise AppError(f"nello Store di Steam non trovo '{name}'")
        q = norm(name)
        return next((h for h in hits if norm(h[1]) == q), hits[0])

    @app.action("installa", "installa un gioco di Steam (Steam apre la sua finestra per confermare cartella e spazio)",
                {"gioco": "nome o numero (appid) del gioco su Steam"})
    def installa(a):
        name = str(a["gioco"])
        try:
            g = lib.find(name)
            if g.state != "non completo":
                return f"{g.name} è già installato ({g.launcher}, {g.state})"
        except AppError:
            pass
        appid, title = _store_pick(name)
        opener(f"steam://install/{appid}")
        return (f"Steam ha aperto l'installazione di {title}: si conferma lì (cartella e spazio). Se non era il gioco "
                "giusto, dillo all'utente")

    def disinstalla_conferma(a):
        g = lib.find(a["gioco"])
        steam_only(g, "Disinstallare")
        a["gioco"] = g.name
        return f"disinstallare {g.name} da Steam (libera {gb(g.size)}; per giocarci andrà riscaricato)"

    @app.action("disinstalla", "disinstalla un gioco di Steam (Steam chiede a sua volta)", {"gioco": "nome"},
                confirm=disinstalla_conferma)
    def disinstalla(a):
        g = lib.find(a["gioco"])
        steam_only(g, "Disinstallare")
        opener(f"steam://uninstall/{g.id}")
        return f"Steam ha aperto la disinstallazione di {g.name}: l'ultima conferma è nella sua finestra"

    @app.action("verifica", "fa controllare a Steam i file di un gioco (ripara quelli rovinati)", {"gioco": "nome"})
    def verifica(a):
        g = lib.find(a["gioco"])
        steam_only(g, "Verificare i file")
        opener(f"steam://validate/{g.id}")
        return f"Steam sta verificando i file di {g.name}"

    @app.action("aggiornamenti", "giochi da aggiornare, in aggiornamento o non completi")
    def aggiornamenti(_a):
        todo = [line(g) for g in lib.games(refresh=True) if g.state != "installato"]
        return "; ".join(todo) if todo else "tutti i giochi sono aggiornati"

    @app.action("recenti", "gli ultimi giochi giocati su Steam, con le ore")
    def recenti(_a):
        played = sorted((g for g in lib.games() if g.last_played), key=lambda g: g.last_played or 0, reverse=True)
        if not played:
            return "nessuna partita registrata su Steam per l'account collegato"
        return "; ".join(f"{g.name}: {when(g.last_played, now())}" +
                         (f", {hours(g.playtime_min)} in tutto" if g.playtime_min else "") for g in played[:8])

    @app.action("spazio", "quanto occupano i giochi e lo spazio libero sui dischi")
    def spazio(_a):
        games = lib.games(refresh=True)
        total = sum(g.size for g in games)
        big = sorted((g for g in games if g.size), key=lambda g: g.size, reverse=True)[:5]
        drives = []
        for d in sorted({Path(g.folder.anchor) for g in games if g.folder and g.folder.anchor}):
            try:
                drives.append(f"{d} libero {gb(disk(d).free)}")
            except OSError:
                pass
        out = f"i giochi occupano {gb(total)}"
        if big:
            out += "; i più grandi: " + ", ".join(f"{g.name} {gb(g.size)}" for g in big)
        return out + (("; " + ", ".join(drives)) if drives else "")

    @app.action("negozio", "apre la pagina del gioco nello Store di Steam", {"gioco": "nome o appid"})
    def negozio(a):
        try:
            g = lib.find(a["gioco"])
            appid, title = (g.id, g.name) if g.launcher == "Steam" else _store_pick(g.name)
        except AppError:
            appid, title = _store_pick(str(a["gioco"]))
        opener(f"steam://store/{appid}")
        return f"aperta in Steam la pagina dello Store di {title}"

    @app.action("pagina", "apre la scheda del gioco nella libreria di Steam (notizie, obiettivi, impostazioni)",
                {"gioco": "nome"})
    def pagina(a):
        g = lib.find(a["gioco"])
        steam_only(g, "La scheda in libreria")
        opener(f"steam://nav/games/details/{g.id}")
        return f"aperta la scheda di {g.name} in Steam"

    @app.action("cartella", "apre la cartella dei file del gioco", {"gioco": "nome"})
    def cartella(a):
        g = lib.find(a["gioco"])
        if g.folder is None or not g.folder.exists():
            raise AppError(f"non trovo la cartella di {g.name}")
        if g.launcher == "Xbox":
            raise AppError("i giochi Xbox stanno in una cartella protetta di Windows: non si apre")
        opener(str(g.folder))
        return f"aperta la cartella di {g.name}"

    @app.action("steam", "apre una sezione di Steam: " + ", ".join(STEAM_SECTIONS),
                {"sezione": "una tra " + ", ".join(STEAM_SECTIONS)})
    def steam(a):
        key = norm(str(a["sezione"]))
        uri = next((u for k, u in STEAM_SECTIONS.items() if norm(k) == key), None)
        if uri is None:
            raise AppError(f"sezione sconosciuta '{a['sezione']}': {', '.join(STEAM_SECTIONS)}")
        opener(uri)
        return "chiusura di Steam richiesta" if key == "esci" else f"aperta la sezione {key} di Steam"

    @app.action("stato", "lo stato su Steam visibile agli amici: online, assente, occupato, invisibile, offline",
                {"stato": "online, assente, occupato, invisibile oppure offline"})
    def stato(a):
        key = " ".join(str(a["stato"]).lower().split())
        if key not in STEAM_STATUS:
            raise AppError(f"stato sconosciuto '{a['stato']}': online, assente, occupato, invisibile, offline")
        opener(f"steam://friends/status/{STEAM_STATUS[key]}")
        return f"stato di Steam: {key}"

    @app.action("account", "quale account Steam è collegato (le ore di gioco sono le sue)")
    def account(_a):
        acc = lib.steam_account()
        if acc is None:
            return "nessun account Steam trovato"
        on = "collegato adesso" if lib.registry("ActiveProcess\\ActiveUser") else "l'ultimo usato (Steam è chiuso)"
        return f"account Steam {acc[1]}: {on}"

    return app

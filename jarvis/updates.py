"""Aggiornamento automatico dalle release di GitHub, solo per le copie installate con l'installatore.

Una volta al giorno si chiede a GitHub l'elenco delle release del progetto (non "l'ultima": anche `modelli-1` è una
release). Vale solo una release con un tag `vX.Y.Z` più nuovo della versione installata (`pyproject.toml`), non
bozza né anteprima, con l'installatore `Jarvis-Setup-X.Y.Z.exe` e la sua impronta SHA-256 (il campo `digest` di
GitHub): senza impronta non si installa niente. L'installatore si scarica con lo scaricatore del primo avvio
(ripresa, controllo dell'impronta).

Poi dipende da `system.updates`: "automatici" lo installa quando Jarvis è fermo da un po' (nessuna conversazione,
nessun compito, non a riposo: chi gioca non si ritrova i modelli ricaricati), "chiedi" lo dice e aspetta un sì
(strumento `aggiornamenti`), "spenti" non chiede nemmeno a GitHub. Per installare Jarvis si chiude e un processo
a parte (PowerShell, senza finestre) aspetta che Jarvis e il guardiano siano chiusi, lancia l'installatore in
silenzio e riaccende Jarvis, che alla partenza dice com'è andata.

Le copie con `.git` (chi sviluppa) non si aggiornano mai da sole. Finché il repository è privato GitHub risponde
404: nessun aggiornamento e nessun errore.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger("jarvis.updates")

REPO = "tatelaz/jarvis"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
TAG = re.compile(r"v(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
MODES = ("automatici", "chiedi", "spenti")
CHECK_EVERY_S = 24 * 3600.0
RETRY_S = 3 * 3600.0  # dopo un errore (rete, GitHub) si riprova prima del giorno dopo
QUIET_S = 15 * 60.0  # quanto deve essere fermo Jarvis prima di installare da solo
MARK = "aggiornamento.json"  # in data/state: cosa si stava installando, per dirlo alla ripartenza
# CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP, CREATE_BREAKAWAY_FROM_JOB. Non DETACHED_PROCESS: senza console
# PowerShell muore subito (provato il 22/11)
NO_WINDOW, NEW_GROUP, BREAKAWAY = 0x08000000, 0x00000200, 0x01000000

Version = tuple[int, int, int]


@dataclass(frozen=True)
class Release:
    version: Version
    url: str
    sha256: str
    size: int
    notes: str = ""

    @property
    def name(self) -> str:
        return ".".join(map(str, self.version))


def parse_version(text: str) -> Version | None:
    m = re.fullmatch(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})", str(text).strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def current_version(root: Path = paths.ROOT) -> Version:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    v = parse_version(data["project"]["version"])
    if v is None:
        raise ValueError(f"versione illeggibile in pyproject.toml: {data['project']['version']!r}")
    return v


def installed_copy(root: Path = paths.ROOT) -> bool:
    """Installata con l'installatore (niente .git): chi sviluppa aggiorna con git, mai da solo."""
    return not (root / ".git").exists() and (root / "avvia_jarvis.pyw").is_file()


def pick(releases: Any, current: Version) -> Release | None:
    """La release più nuova da installare, se ce n'è una fatta per bene."""
    best: Release | None = None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        m = TAG.fullmatch(str(r.get("tag_name", "")))
        if not m:
            continue
        version = (int(m[1]), int(m[2]), int(m[3]))
        if version <= current or (best is not None and version <= best.version):
            continue
        name = f"Jarvis-Setup-{version[0]}.{version[1]}.{version[2]}.exe"
        asset = next((a for a in r.get("assets") or [] if isinstance(a, dict) and a.get("name") == name), None)
        if asset is None:
            continue
        digest, url, size = str(asset.get("digest") or ""), str(asset.get("browser_download_url") or ""), asset.get("size")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            continue  # senza impronta non si sa cosa si installerebbe
        if url != f"{DOWNLOADS}{m[0]}/{name}" or not isinstance(size, int) or size <= 0:
            continue
        best = Release(version, url, digest.removeprefix("sha256:"), size, str(r.get("body") or "")[:1500])
    return best


async def fetch_releases(url: str = API, timeout_s: float = 20.0) -> Any:
    """L'elenco delle release da GitHub. None se il repository non si vede (privato) o GitHub non risponde."""
    import aiohttp

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Jarvis-aggiornamenti"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as s, \
            s.get(url, headers=headers) as r:
        if r.status == 404:
            return None
        r.raise_for_status()
        return await r.json()


def busy_now(resting: bool, conv: Any, tasks: Any) -> bool:
    """Jarvis sta facendo qualcosa (non si installa): a riposo, mentre parla o pensa, o con compiti in corso."""
    from .tasks.manager import ACTIVE

    if resting or conv is None or conv.jarvis_active or conv.thinking:
        return True
    return tasks is not None and any(t.status in ACTIVE for t in tasks.tasks.values())


def helper_command(pids: list[int], installer: Path, log_file: Path, pythonw: Path, root: Path) -> list[str]:
    """Il processo a parte: aspetta che Jarvis (e il guardiano) siano chiusi, installa in silenzio, riaccende."""
    def q(text: Any) -> str:
        return "'" + str(text).replace("'", "''") + "'"

    waits = "; ".join(f"Wait-Process -Id {int(p)} -Timeout 300 -ErrorAction SilentlyContinue" for p in pids)
    script = (f"{waits}; "
              f"Start-Process -FilePath {q(installer)} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"
              f"{q('/LOG=' + chr(34) + str(log_file) + chr(34))} -Wait; "
              f"Start-Process -FilePath {q(pythonw)} -ArgumentList {q(chr(34) + str(root / 'avvia_jarvis.pyw') + chr(34))}"
              f" -WorkingDirectory {q(root)}")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script]


def _launch(cmd: list[str]) -> None:
    """Staccato da Jarvis: deve sopravvivergli (e uscire da un eventuale job che lo chiuderebbe con lui)."""
    flags = NO_WINDOW | NEW_GROUP
    try:
        subprocess.Popen(cmd, creationflags=flags | BREAKAWAY, close_fds=True)
    except OSError:  # il job in cui gira Jarvis non permette di uscirne: si prova senza
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)


class Updater:
    def __init__(self, mode: Callable[[], str], notify: Callable[[str], Any], shutdown: Callable[[], Any],
                 root: Path = paths.ROOT, state_dir: Path | None = None,
                 fetch: Callable[[], Awaitable[Any]] = fetch_releases,
                 download: Callable[[Release], Awaitable[Path]] | None = None,
                 launch: Callable[[list[str]], None] = _launch, clock: Callable[[], float] = time.time,
                 enabled: Callable[[], bool] | None = None) -> None:
        self.mode, self.notify, self.shutdown = mode, notify, shutdown
        self.root = root
        self.state_dir = state_dir or paths.STATE_DIR
        self.fetch, self.launch, self.clock = fetch, launch, clock
        self.download = download or self._download
        self.enabled = enabled or (lambda: installed_copy(root))
        self.current = current_version(root)
        self.next_check = 0.0  # il primo controllo al primo giro
        self.available: Release | None = None
        self.installer: Path | None = None  # scaricato e controllato, pronto da installare
        self.error = ""
        self.announced: Version | None = None
        self.applying = False

    @property
    def mark(self) -> Path:
        return self.state_dir / MARK

    # --- cercare e scaricare ---

    async def check(self) -> Release | None:
        """Chiede a GitHub, e se c'è una versione nuova scarica l'installatore (con l'impronta controllata)."""
        now = self.clock()
        try:
            found = pick(await self.fetch(), self.current)
            if found is not None and (self.installer is None or self.available != found):
                self.installer = None
                self.available = found
                self.installer = await self.download(found)
                log.info("aggiornamento: versione %s scaricata e controllata", found.name)
            elif found is None:
                self.available = self.installer = None
        except Exception as e:  # GitHub giù, rete assente, impronta sbagliata: si riprova più tardi
            self.error = f"{type(e).__name__}: {e}"[:300]
            self.next_check = now + RETRY_S
            log.warning("aggiornamento: controllo non riuscito (%s)", self.error)
            return self.available
        self.error = ""
        self.next_check = now + CHECK_EVERY_S
        return self.available

    async def _download(self, rel: Release) -> Path:
        from .setup.catalog import File
        from .setup.download import Downloader

        dest = f"engine/aggiornamenti/Jarvis-Setup-{rel.name}.exe"
        d = Downloader({"engine": paths.ENGINE_DIR, "models": paths.MODELS_DIR}, self.state_dir / "aggiornamenti.json")
        f = File(rel.url, rel.sha256, rel.size, dest)
        await d.run([f])
        return d.target(dest)

    # --- installare ---

    async def tick(self, quiet_for: float, busy: bool) -> None:
        """Ogni minuto, da app.py. busy: conversazione, compiti in corso o riposo."""
        mode = self.mode()
        if mode == "spenti" or self.applying or not self.enabled():
            return
        if self.clock() >= self.next_check:
            await self.check()
        if self.installer is None or self.available is None:
            return
        if mode == "automatici" and not busy and quiet_for >= QUIET_S:
            self.apply()
        elif mode == "chiedi" and self.announced != self.available.version:
            self.announced = self.available.version
            self.notify(f"Avviso di sistema: c'è una versione nuova di Jarvis, la {self.available.name} (ora è la "
                        f"{'.'.join(map(str, self.current))}). Dillo in una frase e chiedi se aggiornare: per farlo "
                        "usa lo strumento aggiornamenti con azione installa (Jarvis si riavvia da solo).")

    def apply(self) -> str:
        """Installa l'aggiornamento scaricato: Jarvis si chiude, il processo a parte installa e lo riaccende."""
        if not self.enabled():
            return "questa copia di Jarvis non si aggiorna da sola (è quella di sviluppo: si aggiorna con git)"
        if self.installer is None or self.available is None or not self.installer.is_file():
            return "non c'è un aggiornamento pronto da installare"
        pids = [os.getpid()]
        if os.environ.get("JARVIS_SUPERVISED") == "1":
            pids.append(os.getppid())  # anche il guardiano deve essere chiuso, o ritirerebbe su Jarvis
        log_file = paths.logs_dir() / "aggiornamento.log"
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.mark.write_text(json.dumps({"from": ".".join(map(str, self.current)), "to": self.available.name,
                                         "when": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
        self.launch(helper_command(pids, self.installer, log_file, pythonw, self.root))
        self.applying = True
        log.info("aggiornamento: installo la versione %s e mi riaccendo", self.available.name)
        self.shutdown()
        return f"installo la versione {self.available.name}: mi spengo e mi riaccendo da solo tra un paio di minuti"

    def after_restart(self) -> str | None:
        """Alla partenza: se si stava installando un aggiornamento, com'è andata (una volta sola)."""
        try:
            data = json.loads(self.mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        self.mark.unlink(missing_ok=True)
        target = parse_version(str(data.get("to", ""))) if isinstance(data, dict) else None
        if target is None:
            return None
        if self.current >= target:
            return (f"Avviso di sistema: Jarvis si è aggiornato alla versione {data['to']}. Dillo in una frase "
                    "quando capita.")
        return (f"Avviso di sistema: l'aggiornamento alla versione {data['to']} non è riuscito (registro in "
                "data/logs/aggiornamento.log): stai ancora usando la "
                f"{'.'.join(map(str, self.current))}. Dillo in una frase.")

    def describe(self) -> str:
        now = ".".join(map(str, self.current))
        if not self.enabled():
            return f"versione {now}; questa è la copia di sviluppo: si aggiorna con git, non da sola"
        mode = self.mode()
        parts = [f"versione {now}", f"aggiornamenti {mode}"]
        if self.available and self.installer:
            parts.append(f"pronta la {self.available.name} (azione installa)")
        elif self.available:
            parts.append(f"c'è la {self.available.name}, non ancora scaricata")
        elif self.next_check:
            parts.append("nessuna versione nuova")
        if self.error:
            parts.append(f"ultimo controllo non riuscito: {self.error}")
        return "; ".join(parts)

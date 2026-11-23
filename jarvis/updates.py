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

Dal 23/11 le copie installate sono anche repository git (jarvis/localrepo.py) e ogni release porta, oltre
all'installatore, `Jarvis-X.Y.Z.bundle` (la storia fino a quella versione, con la sua impronta). Allora non si usa
l'installatore: senza modifiche locali si va avanti (git merge --ff-only, poi uv sync, fatti dal processo a parte a
Jarvis chiuso); con modifiche fatte dall'auto-miglioramento l'unione la fa un compito (jarvis/tasks/self_improve.py,
UpdateMergeWorker) in una copia separata, con le prove: le modifiche restano e la versione nuova arriva. Quando
l'unione è entrata nel codice (la versione sul disco è più nuova di quella che gira) basta riavviarsi.

Le copie di chi sviluppa (prese con git, senza uv dell'installatore) non si aggiornano mai da sole. Finché il
repository è privato GitHub risponde 404: nessun aggiornamento e nessun errore.
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
class Asset:
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Release:
    version: Version
    url: str
    sha256: str
    size: int
    notes: str = ""
    bundle: Asset | None = None  # la storia git fino a questa versione: per le copie che sono repository

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
    """Installata con l'installatore (porta uv in tools/): chi sviluppa aggiorna con git, mai da solo."""
    from .localrepo import installed_copy as installed

    return installed(root)


def repo_copy(root: Path = paths.ROOT) -> bool:
    """La copia installata è già un repository (jarvis/localrepo.py) e Git c'è: si aggiorna unendo."""
    from .localrepo import git_exe

    return (root / ".git").exists() and git_exe() is not None


def _asset(r: dict, tag: str, name: str) -> Asset | None:
    """Un file della release, solo se ha l'impronta e sta dove deve stare."""
    a = next((a for a in r.get("assets") or [] if isinstance(a, dict) and a.get("name") == name), None)
    if a is None:
        return None
    digest, url, size = str(a.get("digest") or ""), str(a.get("browser_download_url") or ""), a.get("size")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return None  # senza impronta non si sa cosa si installerebbe
    if url != f"{DOWNLOADS}{tag}/{name}" or not isinstance(size, int) or size <= 0:
        return None
    return Asset(url, digest.removeprefix("sha256:"), size)


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
        name = ".".join(map(str, version))
        setup = _asset(r, m[0], f"Jarvis-Setup-{name}.exe")
        if setup is None:
            continue
        best = Release(version, setup.url, setup.sha256, setup.size, str(r.get("body") or "")[:1500],
                       _asset(r, m[0], f"Jarvis-{name}.bundle"))
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


def _ps(text: Any) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def helper_command(pids: list[int], installer: Path, log_file: Path, pythonw: Path, root: Path) -> list[str]:
    """Il processo a parte: aspetta che Jarvis (e il guardiano) siano chiusi, installa in silenzio, riaccende."""
    waits = "; ".join(f"Wait-Process -Id {int(p)} -Timeout 300 -ErrorAction SilentlyContinue" for p in pids)
    script = (f"{waits}; "
              f"Start-Process -FilePath {_ps(installer)} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"
              f"{_ps('/LOG=' + chr(34) + str(log_file) + chr(34))} -Wait; "
              f"Start-Process -FilePath {_ps(pythonw)} -ArgumentList {_ps(chr(34) + str(root / 'avvia_jarvis.pyw') + chr(34))}"
              f" -WorkingDirectory {_ps(root)}")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script]


def git_helper_command(pids: list[int], root: Path, commit: str | None, git: str, dev: bool, log_file: Path,
                       pythonw: Path) -> list[str]:
    """Come helper_command, per le copie che sono repository: a Jarvis chiuso porta avanti il codice (solo se si può
    senza unire: --ff-only), rimette le librerie di uv.lock (anche gli strumenti per le prove, se c'erano) e riaccende.
    Un passo che fallisce non tocca il resto: alla ripartenza Jarvis dice com'è andata (jarvis/updates.py)."""
    waits = "; ".join(f"Wait-Process -Id {int(p)} -Timeout 300 -ErrorAction SilentlyContinue" for p in pids)
    # nel registro tutto in UTF-8: con *>> PowerShell 5.1 scriverebbe in UTF-16 e il registro non si leggerebbe
    # (visto nella Sandbox il 23/11); 2>&1 perché anche gli errori di git e uv ci finiscano
    to_log = f"2>&1 | ForEach-Object {{ \"$_\" }} | Out-File -Append -Encoding utf8 {_ps(log_file)}"
    steps = [waits, f"Set-Location {_ps(root)}",
             f"('--- aggiornamento ' + (Get-Date -Format s)) | Out-File -Append -Encoding utf8 {_ps(log_file)}"]
    if commit:
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("commit non valido")
        steps.append(f"& {_ps(git)} merge --ff-only {commit} {to_log}")
    steps += [f"$env:UV_PYTHON_INSTALL_DIR = {_ps(root / 'tools' / 'python')}", "$env:UV_LINK_MODE = 'copy'",
              f"& {_ps(root / 'tools' / 'uv.exe')} sync --frozen{'' if dev else ' --no-dev'} "
              f"--python-preference only-managed {to_log}",
              f"Start-Process -FilePath {_ps(pythonw)} -ArgumentList {_ps(chr(34) + str(root / 'avvia_jarvis.pyw') + chr(34))}"
              f" -WorkingDirectory {_ps(root)}"]
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", "; ".join(steps)]


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
                 enabled: Callable[[], bool] | None = None, repo: Callable[[], bool] | None = None,
                 plan: Callable[[Path], Any] | None = None, merge: Callable[[str, str], Any] | None = None) -> None:
        self.mode, self.notify, self.shutdown = mode, notify, shutdown
        self.root = root
        self.state_dir = state_dir or paths.STATE_DIR
        self.fetch, self.launch, self.clock = fetch, launch, clock
        self.download = download or self._download
        self.enabled = enabled or (lambda: installed_copy(root))
        self.repo = repo or (lambda: repo_copy(root))  # copia installata che è un repository: si aggiorna unendo
        self.plan = plan or self._plan
        self.merge = merge  # avvia il compito che unisce la versione nuova alle modifiche locali (versione, commit)
        self.current = current_version(root)
        self.next_check = 0.0  # il primo controllo al primo giro
        self.available: Release | None = None
        self.installer: Path | None = None  # scaricato e controllato, pronto: l'installatore o il bundle
        self.bundled = False  # quello pronto è il bundle (aggiornamento con git), non l'installatore
        self.merging: Version | None = None  # unione in corso per questa versione (il compito la porta avanti)
        self.error = ""
        self.announced: Version | None = None
        self.applying = False

    @property
    def mark(self) -> Path:
        return self.state_dir / MARK

    def on_disk(self) -> Version:
        """La versione del codice sul disco: dopo un'unione è più nuova di quella che gira finché non ci si riavvia."""
        try:
            return current_version(self.root)
        except (OSError, ValueError, KeyError):
            return self.current

    # --- cercare e scaricare ---

    async def check(self) -> Release | None:
        """Chiede a GitHub, e se c'è una versione nuova scarica l'installatore o il bundle (con l'impronta)."""
        now = self.clock()
        try:
            found = pick(await self.fetch(), self.current)
            if found is not None and (self.installer is None or self.available != found):
                self.installer = None
                self.available = found
                self.bundled = found.bundle is not None and self.repo()
                self.installer = await self.download(found)
                log.info("aggiornamento: versione %s scaricata e controllata (%s)", found.name,
                         "storia git" if self.bundled else "installatore")
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

        use_bundle = rel.bundle is not None and self.repo()
        dest = f"engine/aggiornamenti/Jarvis-{rel.name}.bundle" if use_bundle else \
            f"engine/aggiornamenti/Jarvis-Setup-{rel.name}.exe"
        what = rel.bundle if use_bundle and rel.bundle else Asset(rel.url, rel.sha256, rel.size)
        d = Downloader({"engine": paths.ENGINE_DIR, "models": paths.MODELS_DIR}, self.state_dir / "aggiornamenti.json")
        f = File(what.url, what.sha256, what.size, dest)
        await d.run([f])
        return d.target(dest)

    def _plan(self, bundle: Path) -> Any:
        from .localrepo import plan_update

        return plan_update(self.root, bundle)

    # --- installare ---

    async def tick(self, quiet_for: float, busy: bool) -> None:
        """Ogni minuto, da app.py. busy: conversazione, compiti in corso o riposo."""
        mode = self.mode()
        if mode == "spenti" or self.applying or not self.enabled():
            return
        if self.on_disk() > self.current:  # un'unione è entrata nel codice: manca solo il riavvio
            if mode == "automatici" and not busy and quiet_for >= QUIET_S:
                self.restart_into_new_code()
            elif mode == "chiedi" and self.announced != self.on_disk():
                self.announced = self.on_disk()
                self.notify(f"Avviso di sistema: la versione {'.'.join(map(str, self.on_disk()))} è unita alle modifiche "
                            "locali e pronta: serve un riavvio. Chiedi se riavviarti (strumento aggiornamenti, azione "
                            "installa).")
            return
        if self.clock() >= self.next_check:
            await self.check()
        if self.installer is None or self.available is None or self.merging == self.available.version:
            return
        if mode == "automatici" and not busy and quiet_for >= QUIET_S:
            self.apply()
        elif mode == "chiedi" and self.announced != self.available.version:
            self.announced = self.available.version
            self.notify(f"Avviso di sistema: c'è una versione nuova di Jarvis, la {self.available.name} (ora è la "
                        f"{'.'.join(map(str, self.current))}). Dillo in una frase e chiedi se aggiornare: per farlo "
                        "usa lo strumento aggiornamenti con azione installa (Jarvis si riavvia da solo).")

    def _pids(self) -> list[int]:
        pids = [os.getpid()]
        if os.environ.get("JARVIS_SUPERVISED") == "1":
            pids.append(os.getppid())  # anche il guardiano deve essere chiuso, o ritirerebbe su Jarvis
        return pids

    def _leave(self, cmd: list[str], target: str) -> None:
        """Scrive cosa si sta facendo (per dirlo alla ripartenza), lancia il processo a parte e si spegne."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.mark.write_text(json.dumps({"from": ".".join(map(str, self.current)), "to": target,
                                         "when": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
        self.launch(cmd)
        self.applying = True
        log.info("aggiornamento: passo alla versione %s e mi riaccendo", target)
        self.shutdown()

    def _git_helper(self, commit: str | None) -> list[str]:
        from .localrepo import git_exe, has_test_tools

        return git_helper_command(self._pids(), self.root, commit, git_exe() or "git", has_test_tools(self.root),
                                  paths.logs_dir() / "aggiornamento.log", Path(sys.executable).with_name("pythonw.exe"))

    def restart_into_new_code(self) -> str:
        """Il codice sul disco è già quello nuovo (unione fatta): librerie e riavvio, a Jarvis chiuso."""
        target = ".".join(map(str, self.on_disk()))
        self._leave(self._git_helper(None), target)
        return f"passo alla versione {target}: mi spengo e mi riaccendo da solo tra poco"

    def apply(self) -> str:
        """Installa l'aggiornamento: Jarvis si chiude, il processo a parte aggiorna e lo riaccende. Con modifiche
        locali da unire, invece, parte il compito che le unisce (Jarvis resta acceso)."""
        if not self.enabled():
            return "questa copia di Jarvis non si aggiorna da sola (è quella di sviluppo: si aggiorna con git)"
        if self.on_disk() > self.current:
            return self.restart_into_new_code()
        if self.installer is None or self.available is None or not self.installer.is_file():
            return "non c'è un aggiornamento pronto da installare"
        rel = self.available
        if not self.bundled:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            self._leave(helper_command(self._pids(), self.installer, paths.logs_dir() / "aggiornamento.log", pythonw,
                                       self.root), rel.name)
            return f"installo la versione {rel.name}: mi spengo e mi riaccendo da solo tra un paio di minuti"
        plan = self.plan(self.installer)
        if plan.how == "dentro":  # la versione nuova è già nel codice (per esempio unita prima)
            self.available = self.installer = None
            return f"la versione {rel.name} è già nel mio codice"
        if plan.how == "avanti":  # nessuna modifica locale: si va avanti e basta
            self._leave(self._git_helper(plan.commit), rel.name)
            return f"passo alla versione {rel.name}: mi spengo e mi riaccendo da solo tra poco"
        if self.merge is None:
            return "ci sono modifiche locali da unire ma qui non posso avviare l'unione"
        self.merging = rel.version
        self.merge(rel.name, plan.commit)
        log.info("aggiornamento: versione %s da unire alle modifiche locali (compito avviato)", rel.name)
        return (f"la versione {rel.name} va unita alle modifiche fatte su tua richiesta: l'unione è partita come compito, "
                "con le prove; quando è pronta mi riavvio")

    def merge_failed(self, version: str) -> None:
        """Il compito di unione non ce l'ha fatta: si resta come si è, e si riprova solo con una versione ancora più
        nuova (o se lo si chiede)."""
        self.merging = None
        if self.available and self.available.name == version:
            self.announced = self.available.version  # niente avvisi a ripetizione per la stessa versione

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
        if self.on_disk() > self.current:
            parts.append(f"la {'.'.join(map(str, self.on_disk()))} è unita e pronta: manca il riavvio (azione installa)")
        elif self.merging:
            parts.append(f"unione con le modifiche locali in corso per la {'.'.join(map(str, self.merging))}")
        elif self.available and self.installer:
            parts.append(f"pronta la {self.available.name} (azione installa)")
        elif self.available:
            parts.append(f"c'è la {self.available.name}, non ancora scaricata")
        elif self.next_check:
            parts.append("nessuna versione nuova")
        if self.error:
            parts.append(f"ultimo controllo non riuscito: {self.error}")
        return "; ".join(parts)

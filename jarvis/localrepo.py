"""La copia installata come repository git: auto-miglioramento, versione sana e aggiornamenti che uniscono.

L'installatore mette i file del codice (lo stesso commit della release) e `jarvis.bundle`, la storia del progetto in un
file solo. Al primo avvio con Git disponibile (quello del PC o quello portatile scaricato dal primo avvio) la cartella
diventa un repository vero, con il commit della release: da lì funzionano per tutti, non solo per chi sviluppa,

- l'auto-miglioramento (jarvis/tasks/self_improve.py): rami, prove, unione;
- il ritorno all'ultima versione sana del guardiano (jarvis/supervisor.py);
- gli aggiornamenti come unione (jarvis/updates.py): le modifiche fatte da Jarvis restano, la versione nuova arriva.

Reinstallare sopra una copia che ha modifiche sue: i file diventano quelli della release, le modifiche non si perdono
(restano in un ramo "mie-modifiche/...") e Jarvis lo dice.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger("jarvis.localrepo")

BUNDLE = "jarvis.bundle"
RELEASE_REF = "refs/jarvis/rilascio"  # l'ultima versione arrivata (installatore o aggiornamento)
BACKUP_PREFIX = "mie-modifiche/"
NO_WINDOW = 0x08000000


class RepoError(RuntimeError):
    pass


def installed_copy(root: Path = paths.ROOT) -> bool:
    """Messa dall'installatore (che porta uv in tools/), non presa con git da chi sviluppa."""
    return (root / "tools" / "uv.exe").is_file() and (root / "avvia_jarvis.pyw").is_file()


def git_exe() -> str | None:
    return shutil.which("git")


def git(root: Path, *args: str, check: bool = True, timeout: float = 120) -> str:
    exe = git_exe()
    if exe is None:
        raise RepoError("Git non c'è (lo scarica il primo avvio)")
    r = subprocess.run([exe, *args], cwd=root, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW if os.name == "nt" else 0)
    if check and r.returncode != 0:
        raise RepoError(f"git {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def is_ancestor(root: Path, a: str, b: str) -> bool:
    exe = git_exe()
    if exe is None:
        raise RepoError("Git non c'è")
    return subprocess.run([exe, "merge-base", "--is-ancestor", a, b], cwd=root, capture_output=True,
                          creationflags=NO_WINDOW if os.name == "nt" else 0).returncode == 0


def fetch_bundle(root: Path, bundle: Path) -> str:
    """La storia del bundle nel repository: il commit della versione che porta (in RELEASE_REF)."""
    git(root, "bundle", "verify", str(bundle))
    git(root, "fetch", "--quiet", "--no-tags", str(bundle), f"+HEAD:{RELEASE_REF}")
    return git(root, "rev-parse", RELEASE_REF)


@dataclass
class Outcome:
    what: str  # "" (niente da fare), "creato", "allineato", "salvate"
    commit: str = ""
    backup: str = ""  # il ramo con le modifiche messe da parte

    def said(self) -> str | None:
        """Cosa dire all'utente (solo quando c'è qualcosa da sapere)."""
        if self.what == "salvate":
            return (f"Avviso di sistema: Jarvis è stato reinstallato sopra una copia con modifiche fatte da te: ora è la "
                    f"versione dell'installatore e le modifiche non sono perse, sono nel ramo {self.backup}. Dillo in una "
                    "frase: se le rivuole, le rifai con migliora_jarvis partendo da quel ramo (git diff main..."
                    f"{self.backup}).")
        return None


def ensure_repo(root: Path = paths.ROOT, clock=time.strftime) -> Outcome:
    """Alla partenza: se c'è un bundle dell'installatore, la cartella diventa (o torna allineata a) un repository."""
    bundle = root / BUNDLE
    if not bundle.is_file() or not installed_copy(root) or git_exe() is None:
        return Outcome("")
    fresh = not (root / ".git").exists()
    if fresh:
        git(root, "init", "--quiet")
        for key, value in (("core.autocrlf", "false"), ("user.name", "Jarvis"), ("user.email", "jarvis@localhost"),
                           ("gc.auto", "0")):
            git(root, "config", key, value)
    commit = fetch_bundle(root, bundle)
    backup = ""
    if fresh:
        what = "creato"
    else:  # reinstallato sopra: i file sono già quelli della release, il ramo dice com'era prima
        head = git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
        if head and not is_ancestor(root, head, commit):  # c'erano modifiche (o una storia diversa): da parte
            backup = BACKUP_PREFIX + clock("%Y%m%d-%H%M%S")
            git(root, "branch", backup, head)
            what = "salvate"
        else:
            what = "allineato"
    git(root, "update-ref", "refs/heads/main", commit)
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "reset", "--quiet", "--mixed", commit)  # l'indice come il commit; i file sono già lì
    dirty = git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty:  # non dovrebbe succedere: lo si scrive, non si cancella niente
        log.warning("copia installata: %d file diversi dal commit della release", len(dirty.splitlines()))
    bundle.unlink(missing_ok=True)
    log.info("copia installata: repository %s alla versione %s%s", what, commit[:10],
             f" (modifiche di prima nel ramo {backup})" if backup else "")
    return Outcome(what, commit, backup)


# ---------------------------------------------------------------------- aggiornare


@dataclass
class Plan:
    how: str  # "dentro" (già incluso), "avanti" (nessuna modifica locale: si va avanti), "unione" (ci sono modifiche)
    commit: str


def plan_update(root: Path, bundle: Path) -> Plan:
    """Cosa serve per arrivare alla versione del bundle, partendo da quella che gira (con le sue modifiche)."""
    commit = fetch_bundle(root, bundle)
    head = git(root, "rev-parse", "HEAD")
    if is_ancestor(root, commit, head):
        return Plan("dentro", commit)
    if is_ancestor(root, head, commit):
        return Plan("avanti", commit)
    return Plan("unione", commit)


# ---------------------------------------------------------------------- strumenti per le prove


def has_test_tools(root: Path = paths.ROOT) -> bool:
    scripts = root / ".venv" / "Scripts"
    return (scripts / "pytest.exe").is_file() and (scripts / "ruff.exe").is_file()


def uv_env(root: Path) -> dict[str, str]:
    """Come l'installatore: Python e librerie dentro la cartella di Jarvis."""
    return {**os.environ, "UV_PYTHON_INSTALL_DIR": str(root / "tools" / "python"), "UV_LINK_MODE": "copy"}


def ensure_test_tools(root: Path = paths.ROOT, run=subprocess.run) -> str:
    """Le prove (pytest, ruff) servono all'auto-miglioramento e agli aggiornamenti con modifiche: l'installatore non le
    mette (~60 MB), si scaricano la prima volta che servono. "" se ci sono già, altrimenti cosa è successo."""
    if has_test_tools(root) or not installed_copy(root):
        return ""
    r = run([str(root / "tools" / "uv.exe"), "sync", "--frozen", "--python-preference", "only-managed"], cwd=root,
            env=uv_env(root), capture_output=True, text=True, timeout=900, creationflags=NO_WINDOW if os.name == "nt" else 0)
    if r.returncode != 0 or not has_test_tools(root):
        raise RepoError(f"non sono riuscito a scaricare gli strumenti per le prove: {(r.stderr or r.stdout)[-300:]}")
    log.info("strumenti per le prove scaricati (pytest, ruff)")
    return "scaricati"

"""Gli strumenti esterni che Jarvis usa: OpenCode (il cervello gratuito), Node.js (il ponte di WhatsApp) e Git con
la sua bash (Claude Code su Windows, le prove dell'auto-miglioramento).

Il primo avvio li scarica nella cartella dei motori (`engine/tools`, pacchetti "tool-*" del catalogo) solo se il PC
non li ha già. Quelli installati sul PC vengono sempre prima: le cartelle scaricate si aggiungono in fondo al PATH
di Jarvis, così li trovano Jarvis stesso e i programmi che lancia. Claude Code ha bisogno della bash di Git: se
l'unico Git è quello portatile, gli si indica la sua (`CLAUDE_CODE_GIT_BASH_PATH`).
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable, MutableMapping
from pathlib import Path

from . import paths
from .setup import catalog

log = logging.getLogger("jarvis")

COMMAND = {"tool-opencode": "opencode", "tool-node": "node", "tool-git": "git"}  # come si chiamano sul PATH
GIT_BASH_VAR = "CLAUDE_CODE_GIT_BASH_PATH"


def check_file(key: str) -> Path:
    """L'eseguibile che c'è quando lo strumento scaricato è a posto (il `check` del catalogo)."""
    root, _, rest = catalog.PACKAGES[key].files[0].check.partition("/")
    if root != "engine":
        raise ValueError(f"{key}: gli strumenti stanno nella cartella dei motori")
    return paths.ENGINE_DIR / rest


def downloaded_dirs() -> list[Path]:
    """Le cartelle degli strumenti scaricati che ci sono davvero (quelle da aggiungere al PATH)."""
    return [check_file(k).parent for k in catalog.TOOLS if check_file(k).is_file()]


def portable_bash() -> Path:
    return check_file("tool-git").parent.parent / "bin" / "bash.exe"  # git/cmd/git.exe -> git/bin/bash.exe


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def use_downloaded_tools(env: MutableMapping[str, str] | None = None) -> list[Path]:
    """Rende visibili gli strumenti scaricati (in fondo al PATH). Si può chiamare più volte. Dice cosa ha aggiunto."""
    env = os.environ if env is None else env
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    added = [d for d in downloaded_dirs() if not any(_same(d, p) for p in parts)]
    if added:
        env["PATH"] = os.pathsep.join([*parts, *map(str, added)])
        log.info("strumenti scaricati nel PATH: %s", ", ".join(d.name for d in added))
    git = shutil.which("git", path=env.get("PATH", ""))
    ours = git is not None and _same(Path(git).parent, check_file("tool-git").parent)
    if ours and GIT_BASH_VAR not in env and portable_bash().is_file():
        env[GIT_BASH_VAR] = str(portable_bash())
    return added


def _opencode_found() -> bool:
    from .brain.opencode import find_opencode

    try:
        find_opencode()
    except FileNotFoundError:
        return False
    return True


def missing_tools(which: Callable[[str], str | None] = shutil.which,
                  opencode_found: Callable[[], bool] = _opencode_found) -> list[str]:
    """I pacchetti strumento da scaricare: quelli che il PC non ha, né installati né già scaricati."""
    out = []
    for key in catalog.TOOLS:
        if check_file(key).is_file():
            continue
        found = opencode_found() if key == "tool-opencode" else which(COMMAND[key]) is not None
        if not found:
            out.append(key)
    return out

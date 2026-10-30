"""Controlla in rete il catalogo del primo avvio (jarvis/setup/catalog.py), senza scaricare i file grandi.

Per ogni file: l'indirizzo risponde, la misura è quella scritta nel catalogo e, dove si può sapere senza
scaricare tutto, anche l'impronta. Le release di GitHub dichiarano misura e SHA-256 di ogni file nella loro API
(si legge anche una release di un repository privato, con il login di `gh`); Hugging Face dichiara l'impronta dei
file grandi; i file piccoli si scaricano e si controllano. Da lanciare prima di ogni versione e quando si cambia
il catalogo. Un file che si vede solo col login (repository privato) è segnato: per chi installa non c'è ancora.
Uso: python scripts/controlla_catalogo.py   (esce con 1 se qualcosa non torna)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.setup.catalog import PACKAGES, File

SMALL = 50 * 2**20  # sotto questa misura si scarica e si controlla l'impronta vera
RELEASE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)$")


def gh_token() -> str:
    """Il login di GitHub già fatto sul PC (variabile d'ambiente o `gh`): mai stampato né salvato."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


async def release(session: aiohttp.ClientSession, owner: str, repo: str, tag: str, token: str) -> dict | None:
    """I file di una release ({nome: dati}); None se la release non si vede con queste credenziali."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with session.get(f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}", headers=headers) as r:
        if r.status != 200:
            return None
        return {a["name"]: a for a in (await r.json()).get("assets", [])}


async def check_release(session: aiohttp.ClientSession, f: File, token: str, cache: dict) -> str:
    owner, repo, tag, name = RELEASE.match(f.url).groups()
    key = (owner, repo, tag)
    if key not in cache:
        cache[key] = (await release(session, owner, repo, tag, ""), await release(session, owner, repo, tag, token))
    public, seen = cache[key]
    asset = (seen or {}).get(name)
    if asset is None:
        return "non c'è nella release"
    if asset["size"] != f.size:
        return f"misura {asset['size']} invece di {f.size}"
    digest = asset.get("digest") or ""
    if digest and digest != f"sha256:{f.sha256}":
        return "impronta dichiarata diversa"
    if public is None or name not in public:
        return "si vede solo col login (repository privato): chi installa non può ancora scaricarlo"
    return ""


async def check(session: aiohttp.ClientSession, f: File, token: str, cache: dict) -> str:
    """"" se va bene, altrimenti il problema."""
    try:
        if RELEASE.match(f.url):
            return await check_release(session, f, token, cache)
        if f.size <= SMALL:
            async with session.get(f.url) as r:
                if r.status != 200:
                    return f"risponde {r.status}"
                data = await r.read()
            if len(data) != f.size:
                return f"misura {len(data)} invece di {f.size}"
            return "" if hashlib.sha256(data).hexdigest() == f.sha256 else "impronta diversa"
        async with session.head(f.url, allow_redirects=False) as r:
            linked = r.headers.get("X-Linked-Etag", "").strip('"')
            if linked and linked != f.sha256:
                return "impronta dichiarata diversa"
        async with session.head(f.url, allow_redirects=True) as r:
            if r.status != 200:
                return f"risponde {r.status}"
            size = int(r.headers.get("Content-Length", -1))
        return "" if size == f.size else f"misura {size} invece di {f.size}"
    except (aiohttp.ClientError, TimeoutError) as e:
        return f"non raggiungibile ({e})"


async def main() -> int:
    files = [(p.key, f) for p in PACKAGES.values() for f in p.files]
    token, cache = gh_token(), {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        # prima le release, una volta sola ciascuna; poi tutto il resto insieme
        for _, f in files:
            if RELEASE.match(f.url):
                await check(session, f, token, cache)
        results = await asyncio.gather(*(check(session, f, token, cache) for _, f in files))
    bad = 0
    for (key, f), problem in zip(files, results, strict=True):
        print(f"{'ok ' if not problem else 'NO '} {key:14} {f.dest}" + (f"  <- {problem}" if problem else ""))
        bad += bool(problem)
    print(f"\n{len(files) - bad}/{len(files)} file a posto")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

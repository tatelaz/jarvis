"""Controlla in rete il catalogo del primo avvio (jarvis/setup/catalog.py), senza scaricare i file grandi.

Per ogni file: l'indirizzo risponde, la misura è quella scritta nel catalogo e, dove si può sapere senza
scaricare tutto, anche l'impronta (Hugging Face la dichiara per i file grandi; i file piccoli si scaricano e
si controllano). Da lanciare prima di ogni versione e quando si cambia il catalogo.
Uso: python scripts/controlla_catalogo.py   (esce con 1 se qualcosa non torna)
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.setup.catalog import PACKAGES, File

SMALL = 50 * 2**20  # sotto questa misura si scarica e si controlla l'impronta vera


async def check(session: aiohttp.ClientSession, f: File) -> str:
    """"" se va bene, altrimenti il problema."""
    try:
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
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        results = await asyncio.gather(*(check(session, f) for _, f in files))
    bad = 0
    for (key, f), problem in zip(files, results, strict=True):
        print(f"{'ok ' if not problem else 'NO '} {key:14} {f.dest}" + (f"  <- {problem}" if problem else ""))
        bad += bool(problem)
    print(f"\n{len(files) - bad}/{len(files)} file a posto")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

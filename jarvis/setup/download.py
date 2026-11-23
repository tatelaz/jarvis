"""Download del primo avvio: riprende da dove si era fermato, controlla l'impronta, non lascia mai un file a metà.

Ogni file si scrive accanto alla destinazione con l'estensione `.part` e prende il suo nome solo quando misura
e impronta SHA-256 sono giuste: un download interrotto (rete giù, PC spento, setup chiuso) riparte dal byte a cui
era arrivato, con una richiesta `Range`. Se il server non sa riprendere ricomincia da capo; se il file che arriva
non è quello atteso (misura o impronta diverse) si ferma con un errore chiaro e non lo installa.

Gli archivi del motore si estraggono nella loro cartella (rifiutando percorsi che ne escono) e poi si cancellano.
Un piccolo registro (`scaricati.json`) ricorda cosa è già a posto, così il controllo di un file da 2,6 GB non si
rifà a ogni avvio: vale finché misura e data di modifica del file non cambiano.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path, PurePosixPath

import aiohttp

from .catalog import ROOTS, File

log = logging.getLogger("jarvis.setup")

CHUNK = 1 << 20
BACKOFF_S = (1, 2, 5, 10, 20, 30)  # attese tra un tentativo e l'altro
GONE = {401, 403, 404, 410}  # inutile riprovare: il file non c'è (o non è per noi)
USER_AGENT = "Jarvis-setup"
EXTRACTED_MARK = ".jarvis-estratti.json"  # nella cartella estratta: quali archivi ci sono già


class DownloadError(RuntimeError):
    """Un download che non si può completare. `permanent`: riprovare non serve (file sparito o diverso)."""

    def __init__(self, message: str, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


def sha256_file(path: Path, into: hashlib._Hash | None = None) -> str:
    h = into or hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def safe_member(base: Path, name: str) -> Path:
    """Dove va un file di un archivio, se resta dentro base. Un archivio che prova a uscirne è un archivio ostile."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or (p.parts and p.parts[0].endswith(":")):
        raise DownloadError(f"archivio con un percorso non ammesso: {name}", permanent=True)
    return base.joinpath(*p.parts)


class Progress:
    """Byte fatti su byte totali, per la barra: un file ripreso conta subito quello che c'era già, uno che
    ricomincia da capo torna indietro."""

    def __init__(self, total: int, report: Callable[[int, int, str], None] = lambda done, total, name: None):
        self.total, self.before, self.current, self.name = total, 0, 0, ""
        self._report = report

    @property
    def done(self) -> int:
        return self.before + self.current

    def at(self, n: int) -> None:
        """Del file in corso ci sono n byte."""
        self.current = n
        self._report(self.done, self.total, self.name)

    def finished(self, size: int) -> None:
        self.before += size
        self.current = 0


class Downloader:
    def __init__(self, roots: dict[str, Path], state: Path, retries: int = 6, chunk: int = CHUNK,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 free_bytes: Callable[[Path], int] | None = None,
                 run_sfx: Callable[[Path, Path], int] | None = None) -> None:
        if set(roots) != set(ROOTS):
            raise ValueError(f"servono le cartelle {ROOTS}")
        self.roots = roots
        self.state_path = state
        self.retries, self.chunk, self.sleep = retries, chunk, sleep
        self.free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self.run_sfx = run_sfx or _run_sfx

    # --- dove e cosa ---

    def target(self, dest: str) -> Path:
        root, _, rest = dest.partition("/")
        if root not in self.roots or not rest:
            raise ValueError(f"destinazione fuori dalle cartelle di Jarvis: {dest}")
        return safe_member(self.roots[root], rest)

    def _state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _remember(self, f: File, **info) -> None:
        data = self._state()
        data[f.sha256] = {"dest": f.dest, **info}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    async def is_done(self, f: File) -> bool:
        """Il file (o l'archivio, già estratto) è a posto?"""
        if f.unzip_to:
            known = self._state().get(f.sha256) or {}
            return (bool(known.get("extracted")) or self._marked(f)) and self.target(f.check).exists()
        return await self.on_disk(f)

    # Il registro dei download sta nei dati; questo segno sta accanto ai file estratti. Con dati nuovi e motore già
    # estratto (dati cancellati, una copia di prova) l'archivio non si riscarica e non si riestrae sopra: il 22/11 una
    # prova così ha riscritto il motore di tutti i giorni (per fortuna con la stessa versione).
    def _mark_path(self, f: File) -> Path:
        return self.target(f.unzip_to) / EXTRACTED_MARK

    def _marked(self, f: File) -> bool:
        try:
            return f.sha256 in json.loads(self._mark_path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False

    def _mark(self, f: File) -> None:
        path = self._mark_path(f)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        data[f.sha256] = f.dest
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    async def on_disk(self, f: File) -> bool:
        """Il file scaricato è al suo posto, con misura e impronta giuste?"""
        known = self._state().get(f.sha256) or {}
        path = self.target(f.dest)
        try:
            st = path.stat()
        except OSError:
            return False
        if st.st_size != f.size:
            return False
        if known.get("size") == st.st_size and known.get("mtime_ns") == st.st_mtime_ns:
            return True
        if await asyncio.to_thread(sha256_file, path) != f.sha256:
            log.warning("setup: %s c'è ma non è il file atteso: si riscarica", path)
            return False
        self._remember(f, size=st.st_size, mtime_ns=st.st_mtime_ns)
        return True

    async def missing(self, files: Iterable[File]) -> list[File]:
        return [f for f in files if not await self.is_done(f)]

    # --- scaricare ---

    async def run(self, files: Iterable[File],
                  report: Callable[[int, int, str], None] = lambda done, total, name: None) -> None:
        """Scarica (e controlla, ed estrae) tutto ciò che manca, un file alla volta."""
        todo = await self.missing(files)
        # gli archivi estratti occupano circa una volta e mezza; se motore e modelli stanno sullo stesso disco
        # il conto vale per entrambi (per eccesso se stanno su dischi diversi)
        need = sum(f.size for f in todo) + sum(f.size // 2 for f in todo if f.unzip_to)
        for root in {f.dest.partition("/")[0] for f in todo}:
            base = self.roots[root]
            probe = base
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            if self.free_bytes(probe) < need:
                raise DownloadError(f"spazio insufficiente in {base}: servono {need // 2**20} MB", permanent=True)
        progress = Progress(sum(f.size for f in todo), report)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
            for f in todo:
                progress.name = f.dest
                if not (f.unzip_to and await self.on_disk(f)):  # un archivio scaricato ma non estratto resta buono
                    await self.fetch(session, f, progress)
                if f.unzip_to:
                    await asyncio.to_thread(self.extract, f)
                progress.finished(f.size)

    async def fetch(self, session: aiohttp.ClientSession, f: File, progress: Progress) -> Path:
        path = self.target(f.dest)
        part = path.with_name(path.name + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                await self.sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])
            have = part.stat().st_size if part.exists() else 0
            if have > f.size:
                part.unlink()
                have = 0
            h = hashlib.sha256()
            if have:
                await asyncio.to_thread(sha256_file, part, h)
            progress.at(have)
            try:
                if have < f.size:
                    h = await self._stream(session, f, part, have, h, progress)
            except DownloadError:
                raise
            except (aiohttp.ClientError, TimeoutError, ConnectionError) as e:
                last = e
                log.info("setup: %s interrotto a %d byte (%s), riprovo", f.dest, part.stat().st_size
                         if part.exists() else 0, e)
                continue
            if h.hexdigest() != f.sha256:
                part.unlink()
                raise DownloadError(f"{f.dest}: il file arrivato non è quello atteso (impronta diversa)",
                                    permanent=True)
            os.replace(part, path)
            st = path.stat()
            self._remember(f, size=st.st_size, mtime_ns=st.st_mtime_ns)
            return path
        raise DownloadError(f"{f.dest}: download non riuscito dopo {self.retries + 1} tentativi ({last})")

    async def _stream(self, session, f: File, part: Path, have: int, h, progress: Progress):
        headers = {"Range": f"bytes={have}-"} if have else {}
        async with session.get(f.url, headers=headers) as r:
            if r.status in GONE:
                raise DownloadError(f"{f.dest}: il server risponde {r.status} ({f.url})", permanent=True)
            if r.status == 416 or (r.status == 206 and not r.headers.get("Content-Range", "")
                                   .startswith(f"bytes {have}-")):
                await asyncio.to_thread(part.unlink, missing_ok=True)  # non torna con il file del server: da capo
                progress.at(0)
                raise aiohttp.ClientError(f"ripresa non accettata dal server ({r.status})")
            if r.status not in (200, 206):
                raise aiohttp.ClientError(f"risposta {r.status}")
            if r.status == 200 and have:  # il server non sa riprendere: da capo
                have, h = 0, hashlib.sha256()
                progress.at(0)
            out = await asyncio.to_thread(open, part, "ab" if have else "wb")
            try:
                async for block in r.content.iter_chunked(self.chunk):
                    if have + len(block) > f.size:
                        out.close()
                        await asyncio.to_thread(part.unlink)
                        raise DownloadError(f"{f.dest}: il file è più grande del previsto", permanent=True)
                    out.write(block)
                    h.update(block)
                    have += len(block)
                    progress.at(have)
            finally:
                out.close()
        if have < f.size:
            raise aiohttp.ClientPayloadError(f"arrivati {have} byte su {f.size}")
        return h

    def extract(self, f: File) -> None:
        """Estrae un archivio scaricato nella sua cartella, controlla che ci sia il file atteso, cancella l'archivio."""
        archive = self.target(f.dest)
        base = self.target(f.unzip_to)
        if f.kind == "sfx":  # si arriva qui solo con l'impronta controllata (fetch o on_disk)
            base.mkdir(parents=True, exist_ok=True)
            code = self.run_sfx(archive, base)
            if code != 0:
                raise DownloadError(f"{f.dest}: l'estrazione è finita con il codice {code}")
        else:
            self._unzip(archive, base)
        if f.check and not self.target(f.check).exists():
            raise DownloadError(f"{f.dest}: dopo l'estrazione manca {f.check}", permanent=True)
        self._remember(f, extracted=True)
        self._mark(f)
        with contextlib.suppress(OSError):
            archive.unlink()

    def _unzip(self, archive: Path, base: Path) -> None:
        with zipfile.ZipFile(archive) as z:
            members = [(m, safe_member(base, m.filename)) for m in z.infolist()]  # prima tutti i controlli
            for m, out in members:
                if m.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)


def _run_sfx(archive: Path, base: Path) -> int:
    """Un archivio 7-Zip autoestraente (Git portatile) si apre eseguendolo, senza finestre: -o cartella, -y sì a tutto."""
    import subprocess

    r = subprocess.run([str(archive), f"-o{base}", "-y"], capture_output=True, timeout=900, creationflags=0x08000000)
    return r.returncode

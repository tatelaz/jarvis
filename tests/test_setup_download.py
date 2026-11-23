"""Download del primo avvio contro un server locale che si comporta male in tutti i modi che servono."""

import asyncio
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field

import pytest
from aiohttp import web

from jarvis.setup import download as dl
from jarvis.setup.catalog import File

DATA = bytes(range(256)) * 400  # 102400 byte
CHUNK = 4096


@dataclass
class Spec:
    data: bytes
    ranges: bool = True  # il server sa riprendere
    cuts: int = 0  # quante risposte si interrompono a metà
    cut_at: int = 30000
    status: int = 0  # risposta fissa (404, 500...)
    force416: int = 0  # quante riprese rifiuta con 416
    bad_range: int = 0  # quante riprese riprende dal punto sbagliato
    hold: asyncio.Event | None = None  # si ferma dopo il primo blocco finché non viene liberato


@dataclass
class Server:
    base: str = ""
    files: dict[str, Spec] = field(default_factory=dict)
    log: list[tuple[str, str | None]] = field(default_factory=list)

    def requests(self, name: str) -> list[str | None]:
        return [r for n, r in self.log if n == name]


@pytest.fixture
async def server():
    srv = Server()

    async def handle(req: web.Request):
        name = req.match_info["name"]
        spec = srv.files[name]
        rng = req.headers.get("Range")
        srv.log.append((name, rng))
        if spec.status:
            return web.Response(status=spec.status)
        data, start, headers, status = spec.data, 0, {}, 200
        if rng and spec.ranges:
            start = int(rng.removeprefix("bytes=").split("-")[0])
            if spec.force416:
                spec.force416 -= 1
                return web.Response(status=416)
            if start >= len(data):
                return web.Response(status=416)
            if spec.bad_range:  # un server guasto che riparte da un altro punto (e lo dichiara)
                spec.bad_range -= 1
                start += 1
            headers["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
            status = 206
        body = data[start:]
        resp = web.StreamResponse(status=status, headers=headers)
        resp.content_length = len(body)
        await resp.prepare(req)
        if spec.hold is not None:
            await resp.write(body[:CHUNK])
            await spec.hold.wait()
        if spec.cuts:
            spec.cuts -= 1
            await resp.write(body[:spec.cut_at])
            await asyncio.sleep(0.05)  # il client legge quello che è arrivato (come dal vero, dove arriva piano)
            req.transport.close()  # la connessione cade a metà
            return resp
        await resp.write(body)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/{name}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    srv.base = f"http://127.0.0.1:{port}/"
    yield srv
    await runner.cleanup()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make(srv: Server, name: str, data: bytes = DATA, dest: str = "", **kw) -> File:
    srv.files[name] = Spec(data, **kw)
    return File(srv.base + name, sha(data), len(data), dest or f"models/cartella/{name}")


@pytest.fixture
def downloader(tmp_path):
    waits: list[float] = []

    async def no_wait(s: float) -> None:
        waits.append(s)

    d = dl.Downloader({"engine": tmp_path / "engine", "models": tmp_path / "models"},
                      tmp_path / "data" / "scaricati.json", retries=3, chunk=CHUNK, sleep=no_wait,
                      free_bytes=lambda p: 10**12)
    d.waits = waits
    return d


async def test_downloads_checks_and_puts_the_file_in_place(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    seen = []
    await downloader.run([f], lambda done, total, name: seen.append((done, total, name)))
    out = tmp_path / "models" / "cartella" / "voce.gguf"
    assert out.read_bytes() == DATA
    assert not out.with_name("voce.gguf.part").exists()
    assert seen[-1] == (len(DATA), len(DATA), f.dest)
    assert [d for d, _, _ in seen] == sorted(d for d, _, _ in seen)  # la barra non torna mai indietro qui
    state = json.loads((tmp_path / "data" / "scaricati.json").read_text(encoding="utf-8"))
    assert state[f.sha256]["dest"] == f.dest
    assert await downloader.is_done(f)


async def test_resumes_from_the_saved_part(server, downloader, tmp_path):
    f = make(server, "orecchie.gguf")
    part = tmp_path / "models" / "cartella" / "orecchie.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA[:10000])
    seen = []
    await downloader.run([f], lambda done, total, name: seen.append(done))
    assert server.requests("orecchie.gguf") == ["bytes=10000-"]
    assert (tmp_path / "models" / "cartella" / "orecchie.gguf").read_bytes() == DATA
    assert seen[0] == 10000  # quello che c'era già conta subito


async def test_server_that_cannot_resume_starts_over(server, downloader, tmp_path):
    f = make(server, "memoria.onnx", ranges=False)
    part = tmp_path / "models" / "cartella" / "memoria.onnx.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA[:10000])
    await downloader.run([f])
    assert (tmp_path / "models" / "cartella" / "memoria.onnx").read_bytes() == DATA


async def test_dropped_connection_is_retried_from_where_it_stopped(server, downloader, tmp_path):
    f = make(server, "voce.gguf", cuts=2, cut_at=30000)
    await downloader.run([f])
    assert (tmp_path / "models" / "cartella" / "voce.gguf").read_bytes() == DATA
    ranges = server.requests("voce.gguf")
    assert ranges[0] is None and all(r and r.startswith("bytes=") for r in ranges[1:])
    assert len(ranges) == 3
    assert downloader.waits == list(dl.BACKOFF_S[:2])


async def test_gives_up_after_the_last_attempt(server, downloader):
    f = make(server, "voce.gguf", cuts=99, cut_at=10)
    with pytest.raises(dl.DownloadError, match="dopo 4 tentativi") as e:
        await downloader.run([f])
    assert not e.value.permanent  # si può riprovare più tardi (e si riparte dalla parte salvata)
    assert len(server.requests("voce.gguf")) == 4


@pytest.mark.parametrize("status", [404, 403, 410])
async def test_missing_file_fails_at_once(server, downloader, status):
    f = make(server, "sparito.gguf", status=status)
    with pytest.raises(dl.DownloadError, match=str(status)) as e:
        await downloader.run([f])
    assert e.value.permanent
    assert downloader.waits == []
    assert len(server.requests("sparito.gguf")) == 1


async def test_server_error_is_retried(server, downloader):
    f = make(server, "voce.gguf", status=500)
    with pytest.raises(dl.DownloadError, match="tentativi"):
        await downloader.run([f])
    assert len(server.requests("voce.gguf")) == 4


async def test_wrong_file_is_never_installed(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    server.files["voce.gguf"].data = bytes(len(DATA))  # stessa misura, contenuto diverso
    with pytest.raises(dl.DownloadError, match="impronta") as e:
        await downloader.run([f])
    assert e.value.permanent
    folder = tmp_path / "models" / "cartella"
    assert not (folder / "voce.gguf").exists() and not (folder / "voce.gguf.part").exists()


async def test_file_bigger_than_expected_is_refused(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    server.files["voce.gguf"].data = DATA + b"altro"
    with pytest.raises(dl.DownloadError, match="più grande"):
        await downloader.run([f])
    assert not (tmp_path / "models" / "cartella" / "voce.gguf.part").exists()


async def test_part_longer_than_the_file_is_thrown_away(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    part = tmp_path / "models" / "cartella" / "voce.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA + b"rimasugli")
    await downloader.run([f])
    assert server.requests("voce.gguf") == [None]
    assert (tmp_path / "models" / "cartella" / "voce.gguf").read_bytes() == DATA


async def test_complete_part_is_checked_without_asking_the_server(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    part = tmp_path / "models" / "cartella" / "voce.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA)
    await downloader.run([f])
    assert server.requests("voce.gguf") == []
    assert (tmp_path / "models" / "cartella" / "voce.gguf").read_bytes() == DATA


@pytest.mark.parametrize("kind", ["force416", "bad_range"])
async def test_refused_resume_starts_over(server, downloader, tmp_path, kind):
    f = make(server, "voce.gguf", **{kind: 1})
    part = tmp_path / "models" / "cartella" / "voce.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(DATA[:5000])
    await downloader.run([f])
    assert server.requests("voce.gguf") == ["bytes=5000-", None]
    assert (tmp_path / "models" / "cartella" / "voce.gguf").read_bytes() == DATA


async def test_file_already_in_place_is_not_downloaded_again(server, downloader, tmp_path, monkeypatch):
    f = make(server, "voce.gguf")
    out = tmp_path / "models" / "cartella" / "voce.gguf"
    out.parent.mkdir(parents=True)
    out.write_bytes(DATA)
    await downloader.run([f])
    assert server.requests("voce.gguf") == []

    def no_hash(*a, **k):
        raise AssertionError("ricontrollato un file già controllato")

    monkeypatch.setattr(dl, "sha256_file", no_hash)
    assert await downloader.is_done(f)  # il registro basta finché misura e data non cambiano


async def test_changed_file_on_disk_is_checked_again(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    await downloader.run([f])
    out = tmp_path / "models" / "cartella" / "voce.gguf"
    out.write_bytes(bytes(len(DATA)))  # stessa misura, rovinato
    assert not await downloader.is_done(f)
    await downloader.run([f])
    assert out.read_bytes() == DATA


async def test_wrong_size_on_disk_means_missing(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    out = tmp_path / "models" / "cartella" / "voce.gguf"
    out.parent.mkdir(parents=True)
    out.write_bytes(DATA[:100])
    assert await downloader.missing([f]) == [f]


async def test_not_enough_space_stops_before_downloading(server, downloader):
    f = make(server, "voce.gguf")
    downloader.free_bytes = lambda p: len(DATA) - 1
    with pytest.raises(dl.DownloadError, match="spazio") as e:
        await downloader.run([f])
    assert e.value.permanent and server.log == []


async def test_cancelled_download_keeps_its_part(server, downloader, tmp_path):
    hold = asyncio.Event()
    f = make(server, "voce.gguf", hold=hold)
    task = asyncio.ensure_future(downloader.run([f]))
    part = tmp_path / "models" / "cartella" / "voce.gguf.part"
    for _ in range(200):
        await asyncio.sleep(0.01)
        if part.exists() and part.stat().st_size >= CHUNK:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    hold.set()
    assert part.stat().st_size == CHUNK  # al prossimo avvio si riparte da qui


def zipped(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def archive(srv: Server, name: str, entries: dict[str, bytes], check: str = "engine/audiocpp/server.exe") -> File:
    data = zipped(entries)
    srv.files[name] = Spec(data)
    return File(srv.base + name, sha(data), len(data), f"engine/{name}", "engine/audiocpp", check)


async def test_archive_is_extracted_checked_and_removed(server, downloader, tmp_path):
    f = archive(server, "motore.zip", {"server.exe": b"exe", "tools/leggimi.txt": b"ciao"})
    await downloader.run([f])
    base = tmp_path / "engine" / "audiocpp"
    assert (base / "server.exe").read_bytes() == b"exe"
    assert (base / "tools" / "leggimi.txt").read_bytes() == b"ciao"
    assert not (tmp_path / "engine" / "motore.zip").exists()
    assert await downloader.is_done(f)
    (base / "server.exe").unlink()
    assert not await downloader.is_done(f)  # qualcuno ha tolto il motore: va rimesso


async def test_an_extracted_engine_is_recognised_even_with_new_data(server, downloader, tmp_path):
    """Dati nuovi (cancellati, o una copia di prova) e motore già estratto: niente download né estrazione sopra.
    Il 22/11 una prova così ha riscritto il motore di tutti i giorni."""
    f = archive(server, "motore.zip", {"server.exe": b"exe"})
    await downloader.run([f])
    fresh = dl.Downloader(downloader.roots, tmp_path / "dati_nuovi" / "scaricati.json")
    assert await fresh.is_done(f)
    await fresh.run([f])
    assert len(server.requests("motore.zip")) == 1  # solo il primo download
    other = archive(server, "motore2.zip", {"server.exe": b"nuovo"})  # un'altra versione nello stesso posto
    assert not await fresh.is_done(other)  # quella si scarica e si estrae, come prima


async def test_archive_without_the_expected_file_fails(server, downloader):
    f = archive(server, "motore.zip", {"altro.exe": b"exe"})
    with pytest.raises(dl.DownloadError, match="manca"):
        await downloader.run([f])
    assert not await downloader.is_done(f)


@pytest.mark.parametrize("evil", ["../fuori.txt", "tools/../../fuori.txt", "/assoluto.txt", "C:/fuori.txt",
                                  "..\\fuori.txt"])
async def test_hostile_archive_writes_nothing(server, downloader, tmp_path, evil):
    f = archive(server, "motore.zip", {"server.exe": b"exe", evil: b"ostile"})
    with pytest.raises(dl.DownloadError, match="non ammesso") as e:
        await downloader.run([f])
    assert e.value.permanent
    assert not (tmp_path / "engine" / "audiocpp" / "server.exe").exists()  # niente estratto a metà
    assert not (tmp_path / "engine" / "fuori.txt").exists() and not (tmp_path / "fuori.txt").exists()


async def test_archive_downloaded_but_not_extracted_is_not_downloaded_again(server, downloader, tmp_path):
    f = archive(server, "motore.zip", {"server.exe": b"exe"})
    saved = tmp_path / "engine" / "motore.zip"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(server.files["motore.zip"].data)  # il setup si era chiuso prima di estrarre
    await downloader.run([f])
    assert server.requests("motore.zip") == []
    assert (tmp_path / "engine" / "audiocpp" / "server.exe").exists()


@pytest.mark.parametrize("dest", ["models/../fuori.bin", "altro/x.bin", "models", "models/a/../../x"])
def test_destination_must_stay_inside_the_jarvis_folders(downloader, dest):
    with pytest.raises((ValueError, dl.DownloadError)):
        downloader.target(dest)


def test_needs_both_folders(tmp_path):
    with pytest.raises(ValueError, match="cartelle"):
        dl.Downloader({"models": tmp_path}, tmp_path / "s.json")


async def test_damaged_register_is_just_forgotten(server, downloader, tmp_path):
    f = make(server, "voce.gguf")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "scaricati.json").write_text("{rotto", encoding="utf-8")
    await downloader.run([f])
    assert await downloader.is_done(f)


async def test_several_files_one_after_the_other_with_one_bar(server, downloader):
    a, b = make(server, "a.bin"), make(server, "b.bin", data=DATA[:5000])
    seen = []
    await downloader.run([a, b], lambda done, total, name: seen.append((done, total, name)))
    assert seen[-1][:2] == (len(DATA) + 5000, len(DATA) + 5000)
    assert {n for _, _, n in seen} == {a.dest, b.dest}


def sfx(srv: Server, name: str, data: bytes = DATA) -> File:
    srv.files[name] = Spec(data)
    return File(srv.base + name, sha(data), len(data), f"engine/tools/{name}", "engine/tools/git",
                "engine/tools/git/cmd/git.exe", kind="sfx")


async def test_self_extracting_archive_is_run_only_after_its_fingerprint(server, downloader, tmp_path):
    runs = []

    def run(archive, base):
        runs.append((archive.name, base))
        (base / "cmd").mkdir(parents=True)
        (base / "cmd" / "git.exe").write_bytes(b"git")
        return 0

    downloader.run_sfx = run
    f = sfx(server, "PortableGit.7z.exe")
    await downloader.run([f])
    assert runs == [("PortableGit.7z.exe", tmp_path / "engine" / "tools" / "git")]
    assert not (tmp_path / "engine" / "tools" / "PortableGit.7z.exe").exists()  # l'archivio si cancella
    assert await downloader.is_done(f)


async def test_a_tampered_self_extracting_archive_never_runs(server, downloader):
    runs = []
    downloader.run_sfx = lambda archive, base: runs.append(archive) or 0
    f = sfx(server, "PortableGit.7z.exe")
    server.files["PortableGit.7z.exe"].data = bytes(len(DATA))  # stessa misura, contenuto diverso
    with pytest.raises(dl.DownloadError, match="impronta"):
        await downloader.run([f])
    assert runs == []


async def test_self_extracting_archive_that_fails_or_misses_its_file(server, downloader):
    downloader.run_sfx = lambda archive, base: 2
    f = sfx(server, "PortableGit.7z.exe")
    with pytest.raises(dl.DownloadError, match="codice 2") as e:
        await downloader.run([f])
    assert not e.value.permanent  # si può riprovare
    downloader.run_sfx = lambda archive, base: 0  # finisce "bene" ma senza git.exe
    with pytest.raises(dl.DownloadError, match="manca") as e:
        await downloader.run([f])
    assert e.value.permanent

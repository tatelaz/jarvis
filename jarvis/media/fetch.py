"""Da dove arriva il video: un file del PC (percorso, nome detto a voce o "l'ultimo") o un indirizzo web.

I siti (YouTube e gli altri che yt-dlp conosce) si scaricano in una cartella temporanea, a una risoluzione che basta
per i fotogrammi: video e audio in due file separati, così non serve FFmpeg per unirli (PyAV li legge entrambi).
YouTube vuole un interprete JavaScript per le sue firme: deno se c'è, altrimenti Node (quello del ponte WhatsApp).
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..apps.base import AppError, norm, pick_by_name
from .watch import MediaError, clock

log = logging.getLogger("jarvis.media")

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv", ".3gp", ".ts", ".mpg", ".mpeg"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".oga", ".wma", ".amr"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3  # un video da 2 GB a 480p dura ore: oltre è un errore, non un video da guardare
_URL = re.compile(r"^https?://\S+$", re.I)
_LATEST = re.compile(r"^(l )?ultim[oa]( (video|audio|file|vocale|registrazione))?( (scaricat|salvat)[oa])?$")


@dataclass
class Media:
    name: str  # per il cervello: titolo o nome del file, e da dove viene
    video: Path | None
    audio: Path | None
    origin: str  # "file", "web", "whatsapp"


def default_folders(home: Path | None = None) -> list[Path]:
    """Dove cercare un file detto per nome: Desktop, Download (anche quelli di WhatsApp), Video, Musica, Documenti."""
    h = home or Path.home()
    return [h / "Desktop", h / "Downloads", h / "Downloads" / "WhatsApp", h / "Videos", h / "Music", h / "Documents"]


def is_url(text: str) -> bool:
    return bool(_URL.match(str(text).strip()))


def _media_files(folders: list[Path]) -> list[Path]:
    out, seen = [], set()
    for d in folders:
        try:
            entries = list(os.scandir(d))
        except OSError:  # cartella che non c'è o non si legge
            continue
        for e in entries:
            p = Path(e.path)
            if e.is_file() and p.suffix.lower() in MEDIA_EXT and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)
    return out


def find_local(text: str, folders: list[Path]) -> Path:
    """Un percorso completo, oppure un file cercato per nome nelle cartelle solite ("vacanze", "l'ultimo video")."""
    raw = os.path.expandvars(str(text).strip().strip('"').strip("'"))
    if not raw:
        raise MediaError("quale video o audio? Serve un percorso, un nome di file o un indirizzo")
    p = Path(raw).expanduser()
    if p.is_absolute() or len(p.parts) > 1:
        if p.is_file():
            return p
        raise MediaError(f"il file {raw} non esiste")
    files = _media_files(folders)
    if not files:
        raise MediaError("nessun video o audio nel Desktop, nei Download, in Video o in Musica: serve il percorso")
    key = norm(raw.replace("'", " "))
    if _LATEST.match(key):
        if "video" in key.split():
            files = [f for f in files if f.suffix.lower() in VIDEO_EXT] or files
        elif "audio" in key.split() or "vocale" in key.split():
            files = [f for f in files if f.suffix.lower() in AUDIO_EXT] or files
        return max(files, key=lambda f: f.stat().st_mtime)
    stem = raw if Path(raw).suffix.lower() not in MEDIA_EXT else Path(raw).stem
    try:
        return pick_by_name(stem, files, lambda f: f.stem, plural="file", empty="quale file?",
                            missing=f"non trovo un video o un audio chiamato '{raw}' nel Desktop, nei Download, "
                                    "in Video o in Musica: serve il percorso completo")
    except AppError as e:
        raise MediaError(str(e)) from None


class _Quiet:
    """I messaggi di yt-dlp vanno nel registro di Jarvis, non sulla console."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)


def height_for(frame_px: int) -> int:
    """La risoluzione da scaricare: quella che i fotogrammi useranno davvero (480p per 768 px)."""
    return 480 if frame_px <= 854 else 720 if frame_px <= 1280 else 1080


def ytdlp_options(dest: Path, audio_only: bool, height: int) -> dict[str, Any]:
    # H.264 prima di tutto: solo lì si possono decodificare i soli fotogrammi chiave (VP9 li decodifica tutti, 3-4
    # volte più lento: misurato su un video di 10 minuti); AV1 per ultimo (decodifica lenta sul processore)
    video = (f"bv*[height<={height}][vcodec^=avc1]/bv*[height<={height}][vcodec!^=av01]/bv*[height<={height}]"
             "/wv*")
    return {
        "quiet": True, "no_warnings": True, "noprogress": True, "logger": _Quiet(),
        "noplaylist": True, "max_filesize": MAX_DOWNLOAD_BYTES,
        "js_runtimes": {"deno": {}, "node": {}},  # firme di YouTube: deno se c'è, altrimenti Node
        # video e audio separati (con la virgola): unirli richiederebbe FFmpeg, PyAV li legge così come sono
        "format": "ba/b" if audio_only else f"{video},ba/b",
        "outtmpl": str(dest / "%(id)s.%(format_id)s.%(ext)s"),  # percorso completo: anche i pezzi temporanei lì
    }


def download(url: str, dest: Path, audio_only: bool = False, frame_px: int = 768, ydl_factory=None) -> Media:
    """Scarica il video (o solo l'audio) in dest. Gira in un thread: può durare."""
    if not is_url(url):
        raise MediaError(f"indirizzo non valido: {url}")
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            raise MediaError("manca il pacchetto yt-dlp per scaricare dai siti: va installato nel venv di Jarvis "
                             "(uv add yt-dlp[default])") from None
        ydl_factory = yt_dlp.YoutubeDL
        errors: tuple[type[BaseException], ...] = (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError)
    else:
        errors = (RuntimeError,)
    t = time.perf_counter()
    try:
        with ydl_factory(ytdlp_options(dest, audio_only, height_for(frame_px))) as ydl:
            info = ydl.extract_info(url, download=True)
    except errors as e:
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).removeprefix("ERROR: ").strip()
        raise MediaError(f"non riesco a scaricarlo: {msg[:300]}") from None
    if info is None:
        raise MediaError("il sito non ha dato niente da scaricare")
    if info.get("_type") == "playlist" or info.get("entries"):
        raise MediaError("è una playlist o un canale: serve l'indirizzo di un video solo")
    files = [Path(r["filepath"]) for r in info.get("requested_downloads") or [] if r.get("filepath")]
    files = [f for f in files if f.is_file()]
    if not files:
        raise MediaError("scaricamento finito senza file (formato non disponibile, o più di 2 GB?)")
    video = next((Path(r["filepath"]) for r in info["requested_downloads"]
                  if r.get("filepath") and r.get("vcodec") not in (None, "none")), None)
    audio = next((Path(r["filepath"]) for r in info["requested_downloads"]
                  if r.get("filepath") and r.get("acodec") not in (None, "none")), None)
    if audio_only:
        video = None
    if video is None and audio is None:
        video = audio = files[0]  # formato senza informazioni: lo legge PyAV
    site = str(info.get("extractor_key") or info.get("extractor") or "web").replace("Youtube", "YouTube")
    who = f" di {info['uploader']}" if info.get("uploader") else ""
    length = f", {clock(info['duration'])}" if info.get("duration") else ""
    log.info("scaricato da %s in %.1f s: %s", site, time.perf_counter() - t, [f.name for f in files])
    return Media(f"«{info.get('title') or url}»{who} ({site}{length})", video, audio, "web")

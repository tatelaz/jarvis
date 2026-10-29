"""Lo strumento guarda_video del cervello frontale: trova il video, lo guarda, lo ascolta e risponde con immagini e
testo. Le impostazioni (sezione media) si rileggono a ogni video: cambiarle a voce vale subito."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..apps.base import AppError
from ..brain.tools import ToolOutput
from . import fetch
from .omni import Omni
from .watch import Limits, MediaError, parse_time, watch

log = logging.getLogger("jarvis.media")

WhatsAppFetch = Callable[[str, Path, str], Awaitable[tuple[Path, str]]]


def _yes(v: Any) -> bool:
    return v is True or str(v or "").strip().lower() in ("true", "1", "sì", "si", "vero")


def limits_of(s: Any) -> Limits:
    """Dalle impostazioni, dentro misure sensate anche se il file di configurazione è stato scritto a mano."""
    return Limits(frames=max(1, min(50, int(s.frames))), frame_px=max(256, min(1568, int(s.frame_px))),
                  max_minutes=max(1.0, min(240.0, float(s.max_minutes))),
                  transcript_chars=max(2000, min(200_000, int(s.transcript_chars))))


class MediaWatcher:
    def __init__(self, settings: Callable[[], Any], stt: Callable[[Any], Awaitable[str]] | None, *,
                 folders: list[Path] | None = None, whatsapp: WhatsAppFetch | None = None,
                 may_whatsapp: Callable[[], str | None] = lambda: None, omni: Omni | None = None,
                 download: Callable[..., fetch.Media] = fetch.download, tmp_root: Path | None = None) -> None:
        self.settings = settings  # la sezione media della configurazione, riletta ogni volta
        self.stt = stt
        self.folders = folders if folders is not None else fetch.default_folders()
        self.whatsapp = whatsapp
        self.may_whatsapp = may_whatsapp  # i messaggi sono solo dell'utente: controllato qui, non dal cervello
        self.omni = omni
        self.download = download
        self.tmp_root = tmp_root

    async def look(self, a: dict[str, Any]) -> ToolOutput:
        s = self.settings()
        limits = limits_of(s)
        source, chat = str(a.get("sorgente") or "").strip(), str(a.get("chat") or "").strip()
        if chat and (why := self.may_whatsapp()):
            return ToolOutput([why])
        audio_only, sounds = _yes(a.get("solo_audio")), _yes(a.get("suoni"))
        try:
            start, end = parse_time(a.get("inizio")), parse_time(a.get("fine"))
            with tempfile.TemporaryDirectory(prefix="jarvis_video_", dir=self.tmp_root,
                                             ignore_cleanup_errors=True) as tmp:
                media = await self._resolve(source, chat, str(a.get("quale") or ""), Path(tmp), audio_only, s)
                watched, pcm = await watch(None if audio_only else media.video, media.audio, self.stt, limits,
                                           media.name, start, end, keep_audio=sounds)
                if sounds:
                    if self.omni is None:
                        watched.notes.append("il modello per musica e suoni non è disponibile")
                    elif pcm is None or not len(pcm):
                        watched.notes.append("niente audio da far ascoltare al modello dei suoni")
                    else:
                        try:
                            watched.sounds = await self.omni.describe(pcm, watched.start, watched.frames,
                                                                      str(a.get("domanda") or ""))
                        except AppError as e:
                            watched.notes.append(str(e))
                out = watched.output(limits.transcript_chars)
                log.info("guarda_video: ~%d token per il cervello (%d immagini)",
                         watched.tokens(limits.transcript_chars), len(out.images))
                return out
        except AppError as e:  # anche MediaError e gli errori di WhatsApp: si spiegano da soli
            return ToolOutput([f"Errore: {e}"])

    async def _resolve(self, source: str, chat: str, which: str, tmp: Path, audio_only: bool, s: Any) -> fetch.Media:
        if chat:
            if self.whatsapp is None:
                raise MediaError("WhatsApp non è disponibile qui")
            path, what = await self.whatsapp(chat, tmp, which)
            return fetch.Media(f"WhatsApp, {what}", path, path, "whatsapp")
        if not source:
            raise MediaError("serve sorgente (percorso, nome del file o indirizzo) oppure chat (WhatsApp)")
        if fetch.is_url(source):
            if not s.web:
                raise MediaError("scaricare video dai siti è spento (impostazione media.web)")
            return await asyncio.to_thread(self.download, source, tmp, audio_only, limits_of(s).frame_px)
        path = await asyncio.to_thread(fetch.find_local, source, self.folders)
        return fetch.Media(f"{path.name} (in {path.parent})", path, path, "file")

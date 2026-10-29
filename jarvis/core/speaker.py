"""Dalla risposta in streaming alla voce: frasi sintetizzate in ordine e accodate al riproduttore.

Mentre una frase suona, la successiva si sintetizza già. La prima esce appena il cervello ne ha
scritto un pezzo pronunciabile (anche solo "Subito, signore."), così la latenza percepita è quella
della prima frase breve, non dell'intera risposta.

Senza buchi: il segmentatore consegna pezzi piccoli (fino alle virgole) e qui si decide quanti
unirne. Finché c'è audio in coda si aggiunge testo (un pezzo lungo ha un'intonazione più naturale),
ma solo quanto si riesce a sintetizzare prima che la coda si svuoti. Tra un pezzo e l'altro si
rimette la pausa che il taglio dei silenzi aveva tolto: più lunga dopo un punto, breve dopo una virgola.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

import numpy as np

from ..audio.player import Segment
from ..speech.text import SentenceChunker
from ..speech.voice import VoiceError, VoiceOutput

log = logging.getLogger("jarvis.speaker")

PAUSE_AFTER = {".": 0.30, "!": 0.30, "?": 0.32, "…": 0.38, ":": 0.20, ";": 0.18, ",": 0.12}
SAFETY_S = 0.25  # margine tra la fine stimata della sintesi e il momento in cui la coda si svuota
SENTENCE_END = (".", "!", "?", "…")
# un pezzo unito non supera questa lunghezza: oltre, la sintesi dura tanto che un errore di stima diventa un buco
MAX_PIECE_CHARS = 240


class ResponseSpeaker:
    def __init__(self, voice: VoiceOutput, play: Callable[..., Segment], on_event: Callable[..., None] | None = None):
        self.voice = voice
        self._play = play
        self._on_event = on_event or (lambda *a, **k: None)
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._chunker = SentenceChunker(instant=voice.is_cached)
        self.segments: list[Segment] = []
        self.full_text = ""
        self.response_id = 0
        self.t_start = 0.0
        self.first_audio_at: float | None = None
        self._finishing = False
        self.failed_chunks: list[str] = []
        self._played = asyncio.Event()
        self._in_flight = False  # un pezzo preso dalla coda e non ancora consegnato al riproduttore
        self._progress = asyncio.Event()  # qualcosa è stato detto o consegnato (per wait_said)

    @property
    def busy(self) -> bool:
        """Vero finché c'è testo da sintetizzare o audio non ancora finito."""
        return (self._worker is not None and not self._worker.done()) or any(not s.finished for s in self.segments)

    def start(self) -> None:
        self.cancel_nowait()
        self.response_id += 1
        self._chunker = SentenceChunker(instant=self.voice.is_cached)
        self._queue = asyncio.Queue()
        self.segments = []
        self.full_text = ""
        self.failed_chunks = []
        self.t_start = time.perf_counter()
        self.first_audio_at = None
        self._finishing = False
        self._worker = asyncio.create_task(self._run(self.response_id), name="tts-worker")

    def feed(self, delta: str) -> None:
        self.full_text += delta
        for chunk in self._chunker.feed(delta):
            self._queue.put_nowait(chunk)

    def boundary(self) -> None:
        """Il cervello si ferma a usare uno strumento: ciò che ha scritto finora è completo e si dice subito. Un punto
        in fondo al testo altrimenti aspetta il carattere dopo ("3." di "3.5"): il 27/10 "Controllo subito." è
        rimasto muto per tutti i 20 secondi degli strumenti."""
        for chunk in self._chunker.flush():
            self._queue.put_nowait(chunk)

    def say(self, text: str) -> None:
        """Frase intera, fuori dallo streaming (es. avvisi del sistema)."""
        self.feed(text if text.endswith((" ", "\n")) else text + " ")

    def finish(self) -> None:
        for chunk in self._chunker.flush():
            self._queue.put_nowait(chunk)
        self._queue.put_nowait(None)
        self._finishing = True  # dopo l'ultimo pezzo in coda c'è solo la fine

    def segment_finished(self, _seg: Segment | None = None) -> None:
        """Da chiamare quando il riproduttore finisce (o scarta) un segmento."""
        if all(s.finished for s in self.segments):
            self._played.set()
        self._progress.set()

    async def wait_done(self, timeout: float | None = None) -> None:
        """Aspetta che tutto sia stato sintetizzato e riprodotto."""
        async def _wait() -> None:
            if self._worker:
                await asyncio.shield(self._worker)
            while any(not s.finished for s in self.segments):
                self._played.clear()
                if all(s.finished for s in self.segments):
                    break
                await self._played.wait()

        await asyncio.wait_for(_wait(), timeout)

    def said_everything(self) -> bool:
        """Detto tutto ciò che è già scritto (anche a metà risposta, mentre il cervello usa uno strumento)."""
        working = self._worker is not None and not self._worker.done()
        return (not working or (self._queue.empty() and not self._in_flight)) and all(s.finished for s in self.segments)

    async def wait_said(self, timeout: float = 20.0) -> None:
        """Aspetta che sia stato detto ciò che è già scritto: prima di spegnere la voce per prestare la scheda video
        (jarvis/media/omni.py), "lo ascolto un attimo" deve arrivare fino in fondo. Mai più di timeout secondi."""
        async def _wait() -> None:
            while not self.said_everything():
                self._progress.clear()
                if self.said_everything():
                    break
                with contextlib.suppress(TimeoutError):  # e ogni tanto si ricontrolla (annullamenti senza segnale)
                    await asyncio.wait_for(self._progress.wait(), 0.25)

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_wait(), timeout)

    def queued_seconds(self) -> float:
        """Secondi di voce già pronti e non ancora ascoltati: il tempo che c'è per preparare il resto."""
        return sum(s.meta.get("seconds", 0.0) * (1.0 - s.fraction_played) for s in self.segments if not s.finished)

    async def _gather(self, text: str) -> tuple[str, str | None, bool]:
        """Unisce al pezzo i successivi finché la sintesi fa in tempo. -> (testo, avanzo, fine)"""
        while self.segments:  # il primo pezzo parte subito, da solo
            slack = self.queued_seconds() - self.voice.estimate_seconds(text) - SAFETY_S
            if slack <= 0 or (self._queue.empty() and text.rstrip().endswith(SENTENCE_END)):
                break  # niente margine, oppure una frase completa e nient'altro pronto: si va
            try:
                nxt = await asyncio.wait_for(self._queue.get(), timeout=slack)
            except TimeoutError:
                break
            if nxt is None:
                return text, None, True
            joined = f"{text} {nxt}"
            if len(joined) > MAX_PIECE_CHARS or self.voice.estimate_seconds(joined) + SAFETY_S > self.queued_seconds():
                return text, nxt, False  # non ci starebbe: il pezzo nuovo aspetta il giro dopo
            text = joined
        return text, None, False

    async def _run(self, rid: int) -> None:
        try:
            await self._pieces(rid)
        finally:
            if rid == self.response_id:  # un lavoratore vecchio, annullato, non tocca quello nuovo
                self._in_flight = False
            self._progress.set()

    async def _pieces(self, rid: int) -> None:
        carry: str | None = None
        while True:
            self._in_flight = carry is not None
            self._progress.set()  # chi aspetta (wait_said) ricontrolla: forse non c'è altro da dire
            first = carry if carry is not None else await self._queue.get()
            self._in_flight = True
            carry = None
            if first is None or rid != self.response_id:
                return
            chunk, carry, ended = await self._gather(first)
            if rid != self.response_id:
                return
            t = time.perf_counter()
            try:
                audio, sr, spoken = await self.voice.say(chunk)
            except VoiceError as e:
                log.error("frase non pronunciabile: %s", e)
                self.failed_chunks.append(chunk)
                self._on_event("tts_failed", text=chunk, error=str(e))
                if ended:
                    return
                continue
            log.debug("frase pronta in %.0f ms (%d caratteri, %.1f s in coda): %s",
                      1000 * (time.perf_counter() - t), len(chunk), self.queued_seconds(), chunk[:60])
            if rid != self.response_id:
                return
            if audio.size == 0:
                if ended:
                    return
                continue
            last = ended or (carry is None and self._finishing and self._queue.qsize() == 1)
            if not last:  # la pausa che il taglio dei silenzi aveva tolto (all'ultima frase non serve)
                pause = PAUSE_AFTER.get(chunk.rstrip()[-1:], 0.0)
                audio = np.concatenate([audio, np.zeros(int(sr * pause), audio.dtype)])
            seg = self._play(audio, sr, text=chunk,
                             meta={"response": rid, "spoken": spoken, "seconds": audio.size / sr})
            self.segments.append(seg)
            if self.first_audio_at is None:
                self.first_audio_at = time.perf_counter()
                self._on_event("first_audio", latency_ms=1000 * (self.first_audio_at - self.t_start))
            if ended:
                return

    def said_chars(self) -> int:
        """Quanti caratteri della risposta sono già stati pronunciati: i sottotitoli seguono la voce, non il testo
        che arriva dal cervello (che corre avanti di qualche secondo)."""
        n = 0
        for seg in self.segments:
            if seg.finished and not seg.interrupted:
                n += len(seg.text) + 1
            else:
                return n + int(len(seg.text) * min(1.0, seg.fraction_played))
        return n

    def spoken_text(self) -> str:
        """Ciò che l'utente ha davvero sentito di questa risposta (anche se interrotta)."""
        return " ".join(t for t in (s.spoken_text() for s in self.segments if s.pos > 0) if t).strip()

    def cancel_nowait(self) -> None:
        self.response_id += 1
        if self._worker and not self._worker.done():
            self._worker.cancel()
        self._worker = None

    async def cancel(self) -> None:
        worker = self._worker
        self.cancel_nowait()
        if worker:
            with contextlib.suppress(asyncio.CancelledError):
                await worker

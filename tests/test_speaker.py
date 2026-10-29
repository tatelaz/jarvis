"""Dalla risposta in streaming alla voce, con tempi realistici in scala: niente buchi a metà frase.

Il caso segnalato da Andrea: in una frase lunga diceva un pezzo, si fermava, poi continuava. Succedeva
perché il primo pezzo (breve) finiva prima che fosse pronta la sintesi del resto (lungo).
"""

import asyncio
import time

import numpy as np

from jarvis.audio.player import Segment
from jarvis.core.speaker import PAUSE_AFTER, ResponseSpeaker

SCALE = 0.25  # tutti i tempi ridotti a un quarto: il test dura poco, i rapporti restano quelli veri
SR = 24000
RATE = 19.0  # caratteri al secondo dell'audio prodotto
SYNTH_A, SYNTH_B = 0.53, 0.0139  # costo misurato della sintesi su questa macchina


class TimedVoice:
    """Sintesi lenta quanto quella vera (in scala); 'Subito.' è in cache."""

    def __init__(self):
        self.cached = {"Subito."}
        self.requests: list[str] = []

    def is_cached(self, text):
        return text in self.cached

    def estimate_seconds(self, text):
        return 0.0 if text in self.cached else (SYNTH_A + SYNTH_B * len(text)) * SCALE

    async def say(self, text):
        self.requests.append(text)
        await asyncio.sleep(self.estimate_seconds(text))  # decine di ms: la risoluzione di Windows basta
        seconds = max(0.3, len(text) / RATE) * SCALE
        return np.full(int(SR * seconds), 0.2, np.float32), SR, text


class RealtimePlayer:
    """Suona i segmenti in ordine al ritmo reale e annota i buchi (coda vuota a metà risposta)."""

    def __init__(self):
        self.queue: list[Segment] = []
        self.gaps: list[float] = []
        self.on_finished = lambda seg: None  # come il riproduttore vero: avvisa lo speaker
        self._idle_since: float | None = None
        self._started = False
        self._last = time.perf_counter()

    def play(self, audio, sr, text="", meta=None):
        seg = Segment(audio, text, meta or {})
        self.queue.append(seg)
        return seg

    def tick(self):
        now = time.perf_counter()
        budget = int((now - self._last) * SR)
        self._last = now
        while budget > 0:
            head = next((s for s in self.queue if not s.finished), None)
            if head is None:
                if self._started and self._idle_since is None:
                    self._idle_since = now
                return
            if self._idle_since is not None:
                self.gaps.append((now - self._idle_since) / SCALE)  # in secondi "veri"
                self._idle_since = None
            self._started = True
            step = min(budget, head.audio.size - head.pos)
            head.pos += step
            budget -= step
            if head.pos >= head.audio.size:
                head.finished = True
                self.on_finished(head)


async def speak(text_pieces, delay_between=0.002):
    voice, player = TimedVoice(), RealtimePlayer()
    sp = ResponseSpeaker(voice, player.play)
    player.on_finished = sp.segment_finished
    stop = False

    async def clock():
        while not stop:
            player.tick()
            await asyncio.sleep(0.001)

    clk = asyncio.ensure_future(clock())
    sp.start()
    for p in text_pieces:  # il cervello scrive veloce (come Claude o gpt-oss)
        sp.feed(p)
        await asyncio.sleep(delay_between)
    sp.finish()
    await sp.wait_done(timeout=15)
    stop = True
    await clk
    return voice, player, sp


LONG = ("Subito. Ho controllato il meteo per tutta la settimana, domani piove su Milano per tutto il giorno, "
        "con temporali nel pomeriggio e venti forti da sud, mentre sabato il cielo resta coperto ma asciutto, "
        "e domenica torna il sole con temperature fino a ventidue gradi.")


async def test_long_answer_has_no_gaps_after_the_opening():
    voice, player, _ = await speak([LONG[i:i + 6] for i in range(0, len(LONG), 6)])
    assert voice.requests[0] == "Subito."  # parte subito, dalla cache
    # dopo "Subito." un attimo di attesa è inevitabile (lo copre la sua pausa); poi mai più silenzi
    assert len(player.gaps) <= 1, player.gaps
    assert all(g < 1.2 for g in player.gaps), player.gaps
    assert " ".join(voice.requests) == LONG


async def test_pieces_grow_when_there_is_time():
    voice, _, _ = await speak([LONG[i:i + 6] for i in range(0, len(LONG), 6)])
    # con l'audio in coda il resto si unisce in pezzi lunghi (intonazione naturale), non virgola per virgola
    assert len(voice.requests) <= 4, voice.requests


async def test_natural_pause_between_pieces_but_not_after_the_last():
    _, player, _ = await speak(["Subito. ", "Ho fatto quello che mi aveva chiesto, signore."])
    first, last = player.queue[0], player.queue[-1]
    expected_first = max(0.3, len("Subito.") / RATE) * SCALE + PAUSE_AFTER["."]
    assert first.meta["seconds"] == np.float64(first.audio.size / SR)
    assert abs(first.audio.size / SR - expected_first) < 0.01
    assert abs(last.audio.size / SR - max(0.3, len(last.text) / RATE) * SCALE) < 0.01  # nessuna coda muta


async def test_merged_pieces_never_exceed_the_cap():
    from jarvis.core.speaker import MAX_PIECE_CHARS

    long_text = "Subito. " + " ".join(f"Questa è la frase numero {i} di una risposta lunghissima, signore." for i in range(12))
    voice, _, _ = await speak([long_text[i:i + 8] for i in range(0, len(long_text), 8)])
    assert all(len(r) <= MAX_PIECE_CHARS for r in voice.requests), [len(r) for r in voice.requests]
    assert " ".join(voice.requests) == long_text


async def test_before_lending_the_gpu_the_sentence_already_written_is_said():
    """A metà risposta il cervello usa guarda_video coi suoni: la voce si spegnerà, ma "Lo ascolto un attimo." deve
    arrivare fino in fondo. Il turno non è finito (wait_done aspetterebbe per sempre): conta solo ciò che è scritto."""
    voice, player = TimedVoice(), RealtimePlayer()
    sp = ResponseSpeaker(voice, player.play)
    player.on_finished = sp.segment_finished
    stop = False

    async def clock():
        while not stop:
            player.tick()
            await asyncio.sleep(0.001)

    clk = asyncio.ensure_future(clock())
    assert sp.said_everything()  # niente da dire
    sp.start()
    sp.feed("Lo ascolto un attimo, signore.")
    sp.boundary()  # parte lo strumento
    assert not sp.said_everything()
    await sp.wait_said(timeout=5)
    assert sp.said_everything() and player.queue and all(s.finished for s in player.queue)
    assert " ".join(voice.requests) == "Lo ascolto un attimo, signore." and sp.busy  # la risposta continua dopo
    sp.feed("Ecco cosa ho sentito.")
    sp.finish()
    await sp.wait_done(timeout=5)
    stop = True
    await clk


async def test_waiting_for_the_sentence_never_blocks_forever():
    class StuckVoice(TimedVoice):
        async def say(self, text):
            await asyncio.sleep(3600)

    sp = ResponseSpeaker(StuckVoice(), RealtimePlayer().play)
    sp.start()
    sp.say("Una frase che non arriva mai.")
    t = time.perf_counter()
    await sp.wait_said(timeout=0.2)
    assert time.perf_counter() - t < 1.0 and not sp.said_everything()
    await sp.cancel()

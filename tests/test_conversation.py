"""Gestore della conversazione con componenti finti: regole di turno, interruzione, eventi, ripieghi."""

import asyncio
import time

import numpy as np
import pytest

from jarvis.audio.listener import ListenEvent
from jarvis.audio.player import Player, Segment
from jarvis.brain.base import BrainUnavailable, Done, TextDelta, ToolCall, ToolStart
from jarvis.brain.router import BrainRouter
from jarvis.config import Config
from jarvis.core.bus import Bus
from jarvis.core.conversation import Conversation, is_stop_command, mentions_wake_word, similarity
from jarvis.core.speaker import ResponseSpeaker

SR_OUT = 48000


class FakeBrain:
    def __init__(self, replies=None, name="finto", fail=False, delay=0.01):
        self.replies = list(replies or [])
        self.messages = []
        self.interrupted = 0
        self.name = name
        self.fail = fail
        self.delay = delay
        self._stop = False

    async def respond(self, message):
        self.messages.append(message)
        if self.fail:
            raise BrainUnavailable("limite d'uso raggiunto")
        self._stop = False
        reply = self.replies.pop(0) if self.replies else "Va bene."
        text = ""
        for word in reply.split(" "):
            if self._stop:
                yield Done(text, interrupted=True)
                return
            await asyncio.sleep(self.delay)
            piece = word + " "
            text += piece
            yield TextDelta(piece)
        yield Done(text)

    async def interrupt(self):
        self.interrupted += 1
        self._stop = True


class FakeVoice:
    """Audio finto: 12 caratteri al secondo, valore costante (si sente "qualcosa")."""

    def __init__(self):
        self.said = []
        self.cached: set[str] = set()  # frasi con l'audio già pronto

    def is_cached(self, text):
        return text in self.cached

    def estimate_seconds(self, text):
        return 0.0 if text in self.cached else 0.01  # come la attesa finta di say()

    async def say(self, text):
        await asyncio.sleep(0.005)
        self.said.append(text)
        return np.full(int(24000 * max(0.2, len(text) / 12)), 0.3, np.float32), 24000, text


class Harness:
    def __init__(self, brain=None, stt_map=None, cfg=None, identity=None):
        self.cfg = cfg or Config()
        self.cfg.audio.barge_in_confirm_ms = 150
        self.bus = Bus()
        self.listener = "idle"
        self.player = Player(SR_OUT, on_event=lambda k, s: self.conv.on_player_event(k, s))

        def play(audio, sr, text="", meta=None):
            import soxr
            a = soxr.resample(audio, sr, SR_OUT)
            return self.player.enqueue(Segment(a, text, meta or {}))

        self.voice = FakeVoice()
        self.speaker = ResponseSpeaker(self.voice, play, on_event=lambda *a, **k: None)
        self.stt_map = stt_map or {}
        self.brain = brain or FakeBrain()

        async def stt(audio):
            return self.stt_map.get(round(float(audio[0])), "")

        self.barge_key = None  # cosa "dice" l'audio della frase in corso (chiave di stt_map)
        self.echo_reports = 0

        def listener_audio():
            return np.full(16000, float(self.barge_key if self.barge_key is not None else -1), np.float32)

        def on_echo():
            self.echo_reports += 1

        self.conv = Conversation(self.cfg, self.bus, self.player, self.speaker, stt, self.brain,
                                 listener_state=lambda: self.listener,
                                 listener_audio=listener_audio, on_echo=on_echo, identity=identity)
        self._clock = None

    async def start_clock(self, speed=20):
        """Il "dispositivo": consuma l'audio a velocità accelerata. Il numero di blocchi si ricava dal
        tempo trascorso, perché su Windows asyncio.sleep ha una risoluzione di ~15 ms."""
        import time

        async def run():
            t0, done = time.perf_counter(), 0
            while True:
                due = int((time.perf_counter() - t0) * speed * SR_OUT / 480)
                for _ in range(min(due - done, 400)):
                    self.player.render(480)
                done = max(done, due - 400) if due - done > 400 else due
                await asyncio.sleep(0.02)
        self._clock = asyncio.ensure_future(run())

    async def stop(self):
        if self._clock:
            self._clock.cancel()

    def turn(self, key, voiced_ms=800):
        audio = np.full(16000, float(key), np.float32)
        self.conv.on_listen_event(ListenEvent("turn_end", 0.0, audio, {"voiced_ms": voiced_ms, "duration_ms": 1000}))

    def events(self, kind):
        return [e for e in self.bus.history if e["type"] == kind]

    async def until(self, cond, timeout=5.0):
        # scadenza sull'orologio reale: su Windows asyncio.sleep sotto i ~15,6 ms ritorna subito
        import time

        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if cond():
                return
            await asyncio.sleep(0.02)
        hist = [(e["type"], e.get("state") or e.get("message") or e.get("text", "")) for e in self.bus.history]
        raise AssertionError(f"condizione non raggiunta; eventi: {hist}; segmenti: "
                             f"{[(s.text[:15], s.finished, s.pos) for s in self.speaker.segments]}; orologio: "
                             f"{self._clock and self._clock.done() and repr(self._clock.exception())}")


# ------------------------------------------------------------------ funzioni pure

@pytest.mark.parametrize("text,expected", [
    ("Jarvis, che ore sono?", True), ("Ehi Jervis accendi la luce", True), ("giarvis", True),
    ("che ore sono", False), ("Davide è arrivato", False), ("jar", False),
])
def test_wake_word(text, expected):
    assert mentions_wake_word(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("Stop.", True), ("Basta!", True), ("Jarvis, zitto", True), ("ok basta", True),
    ("basta con questa storia della pizza ordinala", False), ("fermati un attimo che ti spiego tutto", False),
    ("che tempo fa", False),
])
def test_stop_command(text, expected):
    assert is_stop_command(text) is expected


def test_echo_similarity():
    assert similarity("ho trovato tre voli per Londra", "Ho trovato tre voli per Londra domani mattina.") == 1.0
    assert similarity("aspetta cambia destinazione", "Ho trovato tre voli per Londra.") < 0.3


# ------------------------------------------------------------------ conversazione

async def test_requires_wake_word_then_stays_awake():
    h = Harness(FakeBrain(["Sono le dieci.", "Domani piove."]),
                stt_map={1: "che ore sono", 2: "Jarvis, che ore sono?", 3: "e domani che tempo fa?"})
    await h.start_clock()
    h.turn(1)
    await asyncio.sleep(0.2)
    assert h.brain.messages == [] and h.events("ignored")[-1]["reason"] == "non rivolto a Jarvis"
    h.turn(2)
    await h.until(lambda: h.events("jarvis_done"))
    h.turn(3)  # seguito senza nome: la conversazione è aperta
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    assert len(h.brain.messages) == 2 and h.brain.messages[1].endswith("e domani che tempo fa?")
    assert h.voice.said == ["Sono le dieci.", "Domani piove."]
    await h.stop()


async def test_barge_in_stops_voice_and_generation_and_remembers_what_was_said():
    long = "Ho trovato tre voli per Londra. " + "Il primo parte alle sette e costa poco. " * 6
    h = Harness(FakeBrain([long, "Certo, cambio."], delay=0.0),
                stt_map={1: "Jarvis, cerca voli per Londra", 2: "aspetta, cambia destinazione: Parigi"})
    await h.start_clock(speed=5)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    await asyncio.sleep(0.3)
    h.barge_key = 2  # dalla voce escono parole vere, diverse da quelle di Jarvis
    h.listener = "speech"  # l'utente inizia a parlare sopra Jarvis e continua
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await asyncio.sleep(0.05)
    assert h.player._target == pytest.approx(h.cfg.audio.duck_gain)  # volume abbassato subito
    await h.until(lambda: h.events("interrupted"))
    await h.until(lambda: not h.player.speaking)  # dissolvenza di 15 ms e poi silenzio
    assert h.brain.interrupted >= 1
    spoken = h.events("interrupted")[0]["spoken"]
    assert spoken and len(spoken) < len(long)
    h.listener = "idle"
    h.turn(2)
    await h.until(lambda: len(h.brain.messages) == 2)
    msg = h.brain.messages[1]
    assert "Mi hai interrotto" in msg and spoken[:20] in msg and msg.endswith("Parigi")
    await h.stop()


async def test_cough_is_a_false_alarm_and_speech_resumes_full_volume():
    h = Harness(FakeBrain(["Questa è una risposta abbastanza lunga da essere interrotta. " * 3]),
                stt_map={1: "Jarvis, raccontami qualcosa"})
    await h.start_clock(speed=5)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    h.listener = "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    h.listener = "idle"  # finisce subito: era un colpo di tosse
    await asyncio.sleep(0.3)
    assert not h.events("interrupted")
    assert h.player._target == 1.0 and h.player.speaking
    await h.stop()


async def test_stop_word_silences_without_asking_the_brain():
    h = Harness(FakeBrain(["Una risposta lunga lunga lunga lunga lunga lunga lunga lunga. " * 4]),
                stt_map={1: "Jarvis, parlami", 2: "basta"})
    await h.start_clock(speed=5)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    h.turn(2)
    await h.until(lambda: h.events("interrupted"))
    await asyncio.sleep(0.1)
    assert len(h.brain.messages) == 1 and not h.player.speaking
    await h.stop()


async def test_own_echo_is_not_taken_as_user_speech():
    reply = "Ho trovato tre voli per Londra domani mattina, il più economico parte alle sette."
    h = Harness(FakeBrain([reply]), stt_map={1: "Jarvis, voli per Londra", 2: "trovato tre voli per Londra domani"})
    await h.start_clock(speed=3)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    h.turn(2)
    await h.until(lambda: h.events("ignored"))
    assert h.events("ignored")[-1]["reason"] == "eco della voce di Jarvis"
    assert not h.events("interrupted")
    await h.stop()


async def test_events_wait_for_quiet_then_are_reported():
    h = Harness(FakeBrain(["Una risposta piuttosto lunga che occupa la voce per un po' di tempo.",
                           "Il compito è terminato."]), stt_map={1: "Jarvis, dimmi qualcosa"})
    await h.start_clock(speed=4)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    h.conv.notify("compito #1 (build) terminato con successo")
    await asyncio.sleep(0.1)
    assert len(h.brain.messages) == 1  # non parla sopra se stesso
    await h.until(lambda: len(h.brain.messages) == 2, timeout=10)
    assert "[EVENTO] compito #1" in h.brain.messages[1]
    await h.stop()


async def test_brain_failure_switches_to_fallback_and_says_so():
    main = FakeBrain(fail=True, name="claude")
    backup = FakeBrain(["Risposta dal cervello di riserva."], name="locale")
    router = BrainRouter([main, backup])
    h = Harness(router, stt_map={1: "Jarvis, ci sei?", 2: "E adesso?"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert any("riserva" in s for s in h.voice.said)
    assert "Risposta dal cervello di riserva." in " ".join(h.voice.said)
    said = len(h.voice.said)
    h.turn(2)
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    assert not any("riserva" in s for s in h.voice.said[said:])  # l'avviso si dà una volta, non a ogni turno
    # il secondo turno lo gestisce ancora la riserva, che ha già il contesto: niente riassunto
    assert "<riassunto>" not in backup.messages[1]
    # il riassunto per chi subentra viene dalla cronologia vera
    assert "Jarvis, ci sei?" in h.conv.recap_text() and "Risposta dal cervello di riserva." in h.conv.recap_text()
    await h.stop()


async def test_subscription_exhausted_says_until_when_never_silence():
    class Exhausted(FakeBrain):
        async def respond(self, message):
            self.messages.append(message)
            raise BrainUnavailable("You've hit your session limit", retry_at=time.time() + 3600, scope="provider")
            yield  # pragma: no cover

    h = Harness(BrainRouter([Exhausted(name="opus"), Exhausted(name="haiku")]), stt_map={1: "Jarvis, ci sei?"})
    for b in h.brain.brains:
        b.provider = "claude"
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    await h.until(lambda: h.voice.said)
    assert "esaurito l'uso di Claude fino alle" in h.voice.said[0]
    assert len(h.brain.brains[1].messages) == 0  # stesso abbonamento: inutile provarlo
    await h.stop()


async def test_model_error_is_spoken_not_silent():
    class Broken(FakeBrain):
        async def respond(self, message):
            self.messages.append(message)
            yield Done("", error="errore strano del modello")

    h = Harness(Broken(), stt_map={1: "Jarvis, ci sei?"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.voice.said)
    assert "problema" in h.voice.said[0]
    await h.stop()


async def test_short_noise_turns_and_fillers_are_ignored():
    h = Harness(stt_map={1: "mh", 2: "Jarvis"})
    h.turn(2, voiced_ms=120)  # troppo breve per essere una frase
    h.turn(1)
    await asyncio.sleep(0.2)
    assert h.brain.messages == []


async def test_crash_inside_a_turn_is_reported_not_swallowed():
    class Exploding(FakeBrain):
        async def respond(self, message):
            raise ZeroDivisionError("bug inatteso")
            yield  # pragma: no cover

    h = Harness(Exploding(), stt_map={1: "Jarvis, ci sei?"})
    h.turn(1)
    await h.until(lambda: any(e["where"] == "conversation" for e in h.events("error")))
    assert "ZeroDivisionError" in h.events("error")[-1]["message"]
    assert not h.conv.thinking  # non resta bloccato in "sto pensando"


def test_bus_accepts_kind_as_data_field():
    b = Bus()
    ev = b.emit("notice", kind="brain_fallback")
    assert ev["type"] == "notice" and ev["kind"] == "brain_fallback"


async def test_event_waits_for_a_real_pause_and_yields_to_the_user():
    import time

    h = Harness(FakeBrain(["Il compito è terminato."]))
    await h.start_clock()
    h.listener = "speech"  # l'utente sta parlando (con qualcuno, o con Jarvis)
    t0 = time.perf_counter()
    h.conv.notify("compito #2 terminato")
    await asyncio.sleep(0.6)
    h.listener = "idle"
    await asyncio.sleep(0.6)
    h.listener = "speech"  # riprende a parlare prima che sia passato abbastanza silenzio
    await asyncio.sleep(0.3)
    assert h.brain.messages == []
    h.listener = "idle"
    await h.until(lambda: h.brain.messages)
    waited_quiet = time.perf_counter() - t0 - 1.5
    assert waited_quiet >= 1.1  # ha aspettato ~1,2 s di silenzio vero dopo l'ultima frase
    await h.stop()


async def test_own_voice_leaking_into_the_mic_does_not_interrupt_jarvis():
    """Caso reale della prima prova: cuffie a volume alto, Jarvis si sentiva e si zittiva da solo."""
    reply = "Signore, credo che il microfono stia captando la mia voce: le consiglio di abbassare il volume. " * 2
    h = Harness(FakeBrain([reply]), stt_map={1: "Jarvis, ci sei?", 5: "Signore, credo che il microfono"})
    await h.start_clock(speed=3)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    h.barge_key = 5  # il microfono "sente" la voce di Jarvis
    h.listener = "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await h.until(lambda: any(e.get("reason") == "eco della voce di Jarvis" for e in h.events("ignored")))
    await asyncio.sleep(0.1)
    assert not h.events("interrupted")
    assert h.player.speaking and h.player._target == 1.0  # continua a voce piena
    assert h.echo_reports == 1  # e chiede di riaccendere la cancellazione d'eco
    # anche quando la frase "sentita" finisce, non arriva al cervello
    h.listener = "idle"
    h.turn(5)
    await asyncio.sleep(0.2)
    assert len(h.brain.messages) == 1
    await h.stop()


async def test_barge_in_waits_for_words_then_interrupts():
    h = Harness(FakeBrain(["Una risposta lunga che continua per parecchio tempo ancora e ancora. " * 4]),
                stt_map={1: "Jarvis, raccontami", 7: "no aspetta, cambiamo argomento"})
    await h.start_clock(speed=3)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    h.barge_key = None  # all'inizio solo "voce" senza parole riconoscibili
    h.listener = "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await asyncio.sleep(0.5)
    assert not h.events("interrupted")
    h.barge_key = 7  # ora le parole ci sono
    await h.until(lambda: h.events("interrupted"))
    await h.stop()


async def test_not_addressed_to_jarvis_closes_the_conversation():
    class Dismissing(FakeBrain):
        async def respond(self, message):
            self.messages.append(message)
            if "passami il sale" in message:
                yield ToolCall("nessuna_risposta", {})
                yield Done("")
                return
            yield TextDelta("Certo. ")
            yield Done("Certo.")

    h = Harness(Dismissing(), stt_map={1: "Jarvis, ci sei?", 2: "passami il sale per favore", 3: "che ore sono"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.conv.awake
    h.turn(2)  # parla con qualcun altro: il cervello capisce che non è per lui
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    assert not h.conv.awake and h.events("awake")[-1]["seconds"] == 0
    h.turn(3)  # ora serve di nuovo il nome
    await asyncio.sleep(0.2)
    assert len(h.brain.messages) == 2
    assert h.events("ignored")[-1]["reason"] == "non rivolto a Jarvis"
    await h.stop()


async def test_cached_short_opening_is_spoken_on_its_own():
    """"Subito." è già in cache: esce da solo e subito, il resto si sintetizza mentre suona."""
    h = Harness(FakeBrain(["Subito. Diciassette per ventitré fa trecentonovantuno."]), stt_map={1: "Jarvis, conti?"})
    h.voice.cached.add("Subito.")
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: len(h.voice.said) >= 2)
    assert h.voice.said[:2] == ["Subito.", "Diciassette per ventitré fa trecentonovantuno."]
    await h.stop()


async def test_interrupting_an_announcement_keeps_the_conversation_open():
    """Dal vivo: Jarvis dava un avviso, l'utente gli ha parlato sopra e la sua domanda (col nome storpiato
    dall'eco, "Iar che ore sono?") è stata scartata come "non rivolta a Jarvis"."""
    h = Harness(FakeBrain(["Mi sono ripreso da un arresto imprevisto, signore, e sono di nuovo operativo. " * 3,
                           "Sono le cinque."]),
                stt_map={7: "Iar che ore sono?"})
    await h.start_clock(speed=3)
    h.conv.notify("Avviso di sistema: ripresa dopo una caduta.")
    await h.until(lambda: h.player.speaking, timeout=10)
    assert h.conv.awake  # Jarvis ha preso la parola: gli si risponde senza nome
    h.barge_key = 7
    h.listener = "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await h.until(lambda: h.events("interrupted"))
    h.listener = "idle"
    h.turn(7)
    await h.until(lambda: len(h.brain.messages) == 2)
    assert "Iar che ore sono?" in h.brain.messages[1]
    assert not [e for e in h.events("ignored") if e.get("reason") == "non rivolto a Jarvis"]
    await h.stop()


async def test_interruption_reopens_the_conversation_even_after_the_window():
    h = Harness(FakeBrain(["Una risposta lunga che continua per parecchio tempo ancora e ancora. " * 4, "Bene."]),
                stt_map={1: "Jarvis, raccontami", 7: "no aspetta, cambiamo argomento"})
    h.conv.awake_window_s = 0.3  # la finestra aperta dalla voce di Jarvis scade mentre parla
    await h.start_clock(speed=3)
    h.turn(1)
    await h.until(lambda: h.player.speaking)
    await asyncio.sleep(0.5)
    assert not h.conv.awake
    h.barge_key = 7
    h.listener = "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await h.until(lambda: h.events("interrupted"))
    assert h.conv.awake  # chi interrompe sta parlando con Jarvis
    await h.stop()


async def test_resting_wakes_only_on_the_name_and_keeps_the_sentence():
    h = Harness(stt_map={1: "Jarvis, che tempo fa domani?", 2: "passami il telecomando"})
    woken = []
    h.conv.resting = True
    h.conv.on_wake = woken.append
    await h.start_clock()
    h.turn(2)
    await h.until(lambda: h.events("ignored"))
    assert h.events("ignored")[-1]["reason"] == "a riposo" and woken == []
    h.turn(1)
    await h.until(lambda: woken)
    assert woken == ["Jarvis, che tempo fa domani?"] and h.brain.messages == []  # il cervello aspetta il risveglio
    h.conv.resting = False
    h.conv.resume_turn(woken[0])  # risvegliato: la frase diventa un turno normale
    await h.until(lambda: h.brain.messages)
    assert "che tempo fa domani" in h.brain.messages[0]
    await h.stop()


async def test_resting_ignores_the_owner_without_the_name_even_right_after_speaking():
    """26/10: dopo "sono a riposo" la finestra d'ascolto era ancora aperta e le frasi di Andrea a un amico lo
    risvegliavano (tre volte di fila, con il caos del "spengiti")."""
    h = Harness(stt_map={2: "adesso riposo vuol dire che il microfono resta acceso", 1: "Jarvis, ci sei?"})
    woken = []
    h.conv.on_wake = woken.append
    await h.start_clock()
    h.conv.wake()  # Jarvis ha appena parlato: la conversazione è aperta
    h.conv.resting = True
    h.turn(2)
    await h.until(lambda: h.events("ignored"))
    assert h.events("ignored")[-1]["reason"] == "a riposo" and woken == []
    h.turn(1)
    await h.until(lambda: woken)
    assert woken == ["Jarvis, ci sei?"]
    await h.stop()


class ScriptedBrain(FakeBrain):
    """Cervello che emette per ogni messaggio una sequenza precisa: stringhe = testo, il resto = eventi (strumenti)."""

    def __init__(self, scripts):
        super().__init__()
        self.scripts = list(scripts)

    async def respond(self, message):
        self.messages.append(message)
        text = ""
        for ev in self.scripts.pop(0):
            await asyncio.sleep(0.02)
            if isinstance(ev, float):  # una pausa (es. il cervello che scrive il lungo obiettivo di un compito)
                await asyncio.sleep(ev)
            elif isinstance(ev, str):
                text += ev
                yield TextDelta(ev)
            else:
                yield ev
        yield Done(text)


async def test_silence_tool_means_nothing_is_heard_and_the_conversation_closes():
    """26/10: chiamava nessuna_risposta e poi diceva ad alta voce "Resto in silenzio: parlava con Luca"."""
    h = Harness(ScriptedBrain([
        [ToolCall("nessuna_risposta", {}), "Resto in silenzio: in quel momento parlava con Luca, non con me."],
        ["Resto in silenzio: ", ToolCall("nessuna_risposta", {}), "anche questa era per Luca."],
    ]), stt_map={1: "Jarvis, come vedi Luca sono tante cose separate", 2: "Jarvis, e questo è il compito"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.voice.said == [] and h.events("jarvis_done")[-1]["text"] == ""
    assert not h.conv.awake  # conversazione chiusa: le frasi dette all'amico non arrivano più a Jarvis
    h.turn(2)  # il testo arriva prima dello strumento: si ferma e non resta niente da sentire
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    await asyncio.sleep(0.3)
    assert h.speaker.spoken_text() == "" and not h.player.speaking
    assert h.events("jarvis_done")[-1]["text"] == "" and not h.conv.awake
    await h.stop()


async def test_a_web_search_never_starts_in_silence():
    """26/10: "dimmi una cosa su Elon Musk" → 21 s senza una parola mentre cercava sul web."""
    h = Harness(ScriptedBrain([
        [ToolCall("WebSearch", {"query": "Elon Musk notizie"}), "Ecco la notizia più fresca."],
        ["Un attimo. ", ToolCall("WebSearch", {"query": "meteo"}), "Domani sole."],
    ]), stt_map={1: "Jarvis, dimmi una cosa su Elon Musk", 2: "Jarvis, che tempo fa domani?"})
    h.voice.cached.add("Controllo subito.")
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.voice.said[0] == "Controllo subito."  # prima ancora del risultato della ricerca
    h.turn(2)
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    assert h.voice.said.count("Controllo subito.") == 1  # aveva già parlato lui: niente frase in più
    await h.stop()


async def test_a_task_is_announced_while_the_brain_is_still_writing_it():
    """27/10: 7-11 s di silenzio mentre Opus scriveva l'obiettivo di un compito: la frase pronta deve suonare
    appena inizia la chiamata, non quando è completa."""
    h = Harness(ScriptedBrain([
        [ToolStart("avvia_compito"), 1.0, ToolCall("avvia_compito", {"obiettivo": "lungo", "titolo": "t"}), "Fatto."],
    ]), stt_map={1: "Jarvis, prova qualcosa sul browser"})
    h.voice.cached.add("Me ne occupo io.")
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.voice.said)
    assert h.voice.said == ["Me ne occupo io."] and not h.events("tool")  # prima che lo strumento sia completo
    await h.until(lambda: h.events("jarvis_done"))
    assert h.voice.said.count("Me ne occupo io.") == 1  # la chiamata completa non la ripete
    await h.stop()


async def test_task_news_waits_until_jarvis_is_awake():
    h = Harness()
    h.conv.resting = True
    await h.start_clock()
    h.conv.notify("Il compito #1 è completato.")
    await asyncio.sleep(1.6)  # oltre la pausa di quiete: a riposo comunque non parla
    assert h.brain.messages == []
    h.conv.resting = False
    await h.until(lambda: h.brain.messages, timeout=5)
    assert "compito #1" in h.brain.messages[0]
    await h.stop()


async def test_deaf_rest_does_not_even_transcribe():
    calls = []
    h = Harness(stt_map={1: "Jarvis, ci sei?"})
    original = h.conv.stt

    async def counting(audio):
        calls.append(1)
        return await original(audio)

    h.conv.stt = counting
    h.conv.resting = h.conv.deaf = True
    h.conv.on_wake = lambda text: calls.append("sveglia")
    h.turn(1)
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await asyncio.sleep(0.3)
    assert calls == []  # niente trascrizioni né risvegli: a riposo senza orecchie si usa la finestra o il tasto


async def test_typing_to_a_resting_jarvis_wakes_him():
    h = Harness()
    woken = []
    h.conv.resting = True
    h.conv.on_wake = woken.append
    await h.conv.submit_text("ci sei?")
    assert woken == ["ci sei?"] and h.brain.messages == []


async def test_user_turns_are_counted_for_app_confirmations_events_are_not():
    """Le azioni delicate delle app (jarvis/apps) partono solo con un sì detto DOPO la richiesta: si contano i turni
    dell'utente e si tiene la sua ultima frase. Un evento o una frase non rivolta a Jarvis non sono una risposta."""
    h = Harness(FakeBrain(["Fatto.", "Bene.", "Inviato."]),
                stt_map={1: "che ore sono", 2: "Jarvis, scrivi a Luca", 3: "sì, mandalo"})
    await h.start_clock(speed=4)
    h.turn(1)
    await asyncio.sleep(0.2)
    assert h.conv.user_turns == 0
    h.turn(2)
    await h.until(lambda: h.events("jarvis_done"))
    assert (h.conv.user_turns, h.conv.last_user_text) == (1, "Jarvis, scrivi a Luca")
    h.conv.notify("compito #1 terminato")
    await h.until(lambda: len(h.events("jarvis_done")) == 2, timeout=10)
    assert h.conv.user_turns == 1
    h.turn(3)
    await h.until(lambda: len(h.events("jarvis_done")) == 3, timeout=10)
    assert (h.conv.user_turns, h.conv.last_user_text) == (2, "sì, mandalo")
    await h.stop()


async def test_english_narration_before_a_tool_is_not_spoken():
    """27/10: Jarvis ha detto ad alta voce "Let me check the status." prima di usare uno strumento (e poi
    "status.Direi di sì" attaccato). La frase di servizio prima di uno strumento si scarta; l'inglese chiesto apposta
    (nessuno strumento dopo) si dice; l'italiano passa subito."""
    h = Harness(ScriptedBrain([
        ["Let me check ", "the status.", ToolStart("usa_app"), ToolCall("usa_app", {"app": "whatsapp", "azione": "stato"}),
         "Direi di sì, signore."],
        ["Let me know if you need anything."],
        ["Il collegamento è attivo."],
    ]), stt_map={1: "Jarvis, whatsapp funziona?", 2: "Jarvis, come si dice fammi sapere?", 3: "Jarvis, e adesso?"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.events("jarvis_done")[-1]["text"] == "Direi di sì, signore."
    assert not any("Let me" in s for s in h.voice.said)
    h.turn(2)
    await h.until(lambda: len(h.events("jarvis_done")) == 2)
    assert h.events("jarvis_done")[-1]["text"] == "Let me know if you need anything."
    h.turn(3)
    await h.until(lambda: len(h.events("jarvis_done")) == 3)
    assert h.events("jarvis_done")[-1]["text"] == "Il collegamento è attivo."
    await h.stop()


async def test_text_after_a_tool_is_not_glued_to_the_sentence_before():
    h = Harness(ScriptedBrain([["Controllo subito.", ToolCall("usa_app", {}), "Ecco fatto."]]),
                stt_map={1: "Jarvis, controlla"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    assert h.events("jarvis_done")[-1]["text"] == "Controllo subito. Ecco fatto."
    await h.stop()


async def test_what_is_written_before_a_tool_is_spoken_at_once():
    """27/10: "Controllo subito." scritto e poi 20 s di strumenti: la voce aspettava il carattere dopo il punto
    ("3." potrebbe essere "3.5") e la frase partiva solo alla fine, insieme al resto."""
    h = Harness(ScriptedBrain([
        ["Controllo subito.", ToolStart("usa_app"), 1.5, ToolCall("usa_app", {"app": "whatsapp"}), "Funziona."],
    ]), stt_map={1: "Jarvis, prova WhatsApp"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.voice.said, timeout=1.4)  # prima che lo strumento finisca
    assert h.voice.said == ["Controllo subito."] and not h.events("tool")
    await h.until(lambda: h.events("jarvis_done"))
    assert h.events("jarvis_done")[-1]["text"] == "Controllo subito. Funziona."
    await h.stop()


async def test_text_before_choosing_silence_stops_at_the_start_of_the_call():
    """Il cervello scrive e poi sceglie il silenzio. Il primo pezzo può essere già partito (esce alla virgola, prima
    della scelta), ma all'inizio di nessuna_risposta tutto si ferma: il resto non si sente e la risposta è vuota."""
    h = Harness(ScriptedBrain([
        ["Resto in silenzio, parlava con Luca.", ToolStart("nessuna_risposta"), 0.3, ToolCall("nessuna_risposta", {})],
    ]), stt_map={1: "Jarvis, no niente, parlavo con Luca"})
    await h.start_clock()
    h.turn(1)
    await h.until(lambda: h.events("jarvis_done"))
    await asyncio.sleep(0.3)
    assert not any("Luca" in x for x in h.voice.said) and h.events("jarvis_done")[-1]["text"] == ""
    assert not h.player.speaking
    await h.stop()


async def test_someone_talking_while_jarvis_thinks_does_not_cancel_the_answer():
    """27/10: la sorella di Andrea chiede un video; mentre Jarvis pensa qualcuno parla e un pezzo di voce di un
    secondo, giudicato male, cancellava la richiesta (la frase intera poi risultava "non rivolta a Jarvis").
    Da zitto Jarvis non si interrompe a metà frase: decide a frase finita."""
    h = Harness(ScriptedBrain([[1.5, "Ecco il video di Gabby."]]), stt_map={1: "Jarvis, metti Gabby", 7: "perché sta su cerca?"})
    await h.start_clock()
    h.turn(1)
    await asyncio.sleep(0.3)  # Jarvis sta pensando, non parla ancora
    h.barge_key, h.listener = 7, "speech"
    h.conv.on_listen_event(ListenEvent("speech_start", 0.0))
    await asyncio.sleep(1.0)  # oltre la conferma dell'interruzione
    assert not h.events("interrupted")
    h.listener = "idle"
    await h.until(lambda: h.events("jarvis_done"))
    assert h.events("jarvis_done")[-1]["text"] == "Ecco il video di Gabby."
    await h.stop()

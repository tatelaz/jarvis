"""Il direttore d'orchestra: turni di parola, interruzioni, cervello e voce.

Regole che rendono naturale la conversazione:
- Jarvis non ti interrompe mai; tu puoi interrompere lui sempre. Appena inizi a parlare la sua
  voce si abbassa; se continui (o se dalla trascrizione escono parole vere) si ferma, insieme alla
  generazione in corso. Nella cronologia resta solo ciò che ha detto davvero.
- "Stop", "basta", "zitto"... lo zittiscono subito, senza passare dal cervello.
- Fuori da una conversazione aperta risponde solo se lo chiami ("Jarvis, ..."); dopo una risposta
  resta in ascolto per un po' e puoi proseguire senza ripetere il nome.
- La propria voce rientrata nel microfono (eco residua) non viene scambiata per la tua.
- Gli eventi dei sotto-agenti aspettano un momento di quiete per non parlarti sopra.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import logging
import re
import time
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import numpy as np

from ..audio.listener import ListenEvent
from ..audio.player import Player
from ..brain.base import BrainUnavailable, Done, Notice, TextDelta, ToolCall, ToolResult, ToolStart
from ..config import Config
from ..identity import passphrase
from ..identity.enroll import MIN_CONSISTENCY, SENTENCES, Enrollment
from ..identity.guard import EVENT, LEGACY, TYPED, Voice
from ..identity.people import OWNER
from .bus import Bus
from .narration import NarrationFilter
from .speaker import ResponseSpeaker

log = logging.getLogger("jarvis.conversation")

STOP_WORDS = {"stop", "basta", "zitto", "zitta", "fermo", "fermati", "silenzio", "taci", "aspetta", "smettila",
              "ok basta", "ok stop", "jarvis stop", "jarvis basta", "basta così", "stop jarvis", "ferma"}
FILLERS = {"", "mh", "mm", "mmh", "eh", "ehm", "ah", "oh", "uh", "boh", "hm", "ehi"}
WAKE_NAMES = ("jarvis",)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9' ]+", " ", t).strip()


def mentions_wake_word(text: str) -> bool:
    """Riconosce "Jarvis" anche quando la trascrizione lo storpia (Jervis, Giarvis, Jarvi...)."""
    for tok in normalize(text).split():
        for name in WAKE_NAMES:
            if tok == name or difflib.SequenceMatcher(None, tok, name).ratio() >= 0.72:
                return True
    return False


def is_stop_command(text: str) -> bool:
    t = normalize(text)
    t = re.sub(r"\b(jarvis|per favore|grazie)\b", "", t).strip()
    return t in STOP_WORDS or (len(t.split()) <= 3 and any(w in STOP_WORDS for w in t.split()) and len(t) <= 18)


def is_meaningful(text: str) -> bool:
    t = normalize(text)
    return bool(t) and t not in FILLERS and any(c.isalpha() for c in t)


def similarity(a: str, b: str) -> float:
    """Quanto le parole di a compaiono in b (per riconoscere la propria eco)."""
    wa, wb = normalize(a).split(), set(normalize(b).split())
    if not wa:
        return 0.0
    return sum(1 for w in wa if w in wb) / len(wa)


# strumenti che fanno aspettare prima di una risposta: appena il cervello inizia a chiamarli, se non ha ancora
# detto niente, una frase pronta (tra le COMMON_PHRASES della voce: suona senza attesa). Il 26/10: 21 s muti durante
# una ricerca web; il 27/10: 7-11 s muti mentre il cervello scriveva l'obiettivo di un compito
SLOW_TOOLS = {"WebSearch": "Controllo subito.", "WebFetch": "Controllo subito.",
              "avvia_compito": "Me ne occupo io.", "migliora_jarvis": "Me ne occupo io."}
ENROLL_TAIL_S = 20.0  # dopo la registrazione della voce, chi legge ancora le frasi non sta chiedendo niente


def reads_enrollment_sentence(text: str) -> bool:
    """Una delle frasi da leggere per la registrazione della voce (anche solo un pezzo)."""
    return len(normalize(text).split()) >= 2 and any(similarity(text, s) >= 0.7 for s in SENTENCES)


def _clip(text: str, most: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= most else text[: most - 1].rstrip() + "…"


@dataclass
class Interruption:
    spoken: str  # ciò che l'utente ha sentito
    planned: str  # ciò che Jarvis stava per dire
    at: float = field(default_factory=time.time)


def _when(until: float | None) -> str:
    """'fino alle 20:20', 'fino a domani alle 8:05', oppure '' se non si sa."""
    if not until:
        return ""
    t, today = time.localtime(until), time.localtime()
    hhmm = f"{t.tm_hour}:{t.tm_min:02d}"
    days = (time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    if round(days) <= 0:
        return f"fino alle {hhmm}"
    if round(days) == 1:
        return f"fino a domani alle {hhmm}"
    return f"fino al {t.tm_mday} alle {hhmm}"


def _why(scope: str, until: float | None, provider: str = "claude") -> str:
    if scope == "provider":
        when = _when(until)
        what = "l'uso gratuito di OpenCode" if provider == "opencode" else "l'uso di Claude"
        return f"Ho esaurito {what}" + (f" {when}" if when else " per ora")
    return "Il mio cervello principale non risponde"


def switch_sentence(info: dict) -> str:
    """Cosa dire quando subentra un altro cervello."""
    if info.get("from_provider") == "opencode":  # L'utente ha messo OpenCode in testa
        return f"{_why(info.get('scope', 'brain'), info.get('until'), 'opencode')}: passo al sistema di riserva."
    then = {"local": "continuo con il cervello locale",
            "opencode": "continuo con un modello gratuito di OpenCode"}.get(info.get("to_provider", ""),
                                                                          "passo al sistema di riserva")
    return f"{_why(info.get('scope', 'brain'), info.get('until'))}: {then}."


def unavailable_sentence(e: BrainUnavailable, address: str, already_switched: bool = False) -> str:
    """Cosa dire quando nessun cervello può rispondere."""
    if already_switched:
        return f"Temo che anche il sistema di riserva si sia fermato, {address}. Riprovi tra poco."
    if e.exhausted:
        return f"{_why(e.scope, e.retry_at)}, e non ho un cervello di riserva disponibile."
    return f"Temo di non riuscire a ragionare in questo momento, {address}. Riprovo tra poco."


class Conversation:
    def __init__(self, cfg: Config, bus: Bus, player: Player, speaker: ResponseSpeaker,
                 stt: Callable[[np.ndarray], Awaitable[str]], brain, listener_state: Callable[[], str],
                 context: Callable[[str], str] | None = None,
                 awake_window_s: float = 25.0, listener_audio: Callable[[], np.ndarray] | None = None,
                 on_echo: Callable[[], None] | None = None, identity=None) -> None:
        self.cfg = cfg
        self.bus = bus
        self.player = player
        self.speaker = speaker
        self.stt = stt
        self.brain = brain  # di solito un BrainRouter: ripieghi, pause e ritorni li gestisce lui
        if getattr(brain, "recap", False) is None:
            brain.recap = self.recap_text
        self.listener_state = listener_state
        self.context = context or (lambda _text: "")
        self.awake_window_s = awake_window_s
        self.listener_audio = listener_audio
        self.on_echo = on_echo
        self.identity = identity  # chi sta parlando (jarvis/identity); None = riconoscimento assente
        self.voice: Voice = LEGACY  # chi ha detto la frase del turno in corso
        # turni dell'utente finora e l'ultima frase: le azioni delicate delle app partono solo con un sì detto DOPO
        # la richiesta (jarvis/apps/base.py)
        self.user_turns = 0
        self.last_user_text = ""
        self._interlocutor: Voice | None = None  # con chi è aperta la conversazione
        self.enrollment: Enrollment | None = None  # si sta imparando una voce: le frasi lette non vanno al cervello
        self._enroll_tail_until = 0.0  # fino a quando le frasi da leggere si ignorano anche a registrazione finita
        self._recent_jarvis: tuple[str, float] = ("", 0.0)  # ultima cosa detta, per riconoscerne l'eco
        self.awake_until = 0.0
        self.push_to_talk_armed = False
        self.thinking = False
        self.state = "in attesa"
        # riposo (scheda video libera): si risponde solo al nome, che riaccende Jarvis
        self.resting = False
        self.deaf = False  # a riposo senza orecchie: niente trascrizioni
        self.on_wake: Callable[[str | None], None] | None = None
        self._turn_task: asyncio.Task | None = None
        self._barge_task: asyncio.Task | None = None
        self._barge_pending = False
        self._interruption: Interruption | None = None
        self._events: deque[str] = deque()
        self._event_task: asyncio.Task | None = None
        self._partial_busy = False
        self.history: deque[dict] = deque(maxlen=200)
        self._recap_since = 0.0  # fin dove gli scambi con gli ospiti sono già stati riferiti all'utente
        self.metrics: deque[dict] = deque(maxlen=100)
        self._turn_started_at = 0.0
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> asyncio.Task:
        """Avvia un'attività tenendone un riferimento (altrimenti il GC potrebbe interromperla)."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        """Nessun errore resta sepolto in un'attività: si registra e si segnala."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("attività fallita: %r", exc, exc_info=exc)
            self.bus.emit("error", where="conversation", message=f"{type(exc).__name__}: {exc}")
            self.thinking = False

    # ------------------------------------------------------------ stato

    @property
    def jarvis_active(self) -> bool:
        return self.thinking or self.player.speaking or self.speaker.busy

    def _set_state(self, state: str, **info) -> None:
        self.state = state  # letto dal battito: il guardiano vede anche cosa stava facendo
        self.bus.emit("state", sticky=True, state=state, **info)

    def resume_turn(self, text: str) -> None:
        """Dopo il risveglio: la frase che l'ha riacceso diventa un turno normale."""
        self.wake()
        self._start_turn(text)

    def wake(self, seconds: float | None = None) -> None:
        self.awake_until = time.monotonic() + (seconds or self.awake_window_s)
        self.bus.emit("awake", sticky=True, seconds=seconds or self.awake_window_s)

    def sleep(self) -> None:
        """Chiude la conversazione aperta: per la prossima frase servirà di nuovo il nome."""
        self.awake_until = 0.0
        self._interlocutor = None
        self.bus.emit("awake", sticky=True, seconds=0)

    @property
    def awake(self) -> bool:
        return time.monotonic() < self.awake_until or not self.cfg.ui.wake_word

    def arm_push_to_talk(self) -> None:
        self.push_to_talk_armed = True
        self.wake(10)
        self.bus.emit("ptt", armed=True)

    def on_player_event(self, kind: str, seg) -> None:
        if kind in ("segment_end", "segment_dropped"):
            self.speaker.segment_finished(seg)
            if not self.jarvis_active:
                self._set_state("in attesa")

    # ------------------------------------------------------------ eventi dall'audio

    def on_listen_event(self, ev: ListenEvent) -> None:
        if self.deaf:
            return
        if self.resting and ev.kind != "turn_end":
            return  # a riposo niente interruzioni né trascrizioni parziali: si aspetta la frase intera
        if ev.kind == "speech_start":
            self._on_speech_start()
        elif ev.kind == "partial":
            if not self._partial_busy:
                self._spawn(self._on_partial(ev.audio))
        elif ev.kind == "turn_end":
            self._spawn(self._on_turn_end(ev.audio, ev.info))

    def _on_speech_start(self) -> None:
        self._set_state("ascolto")
        # Interrompersi a metà frase serve solo mentre Jarvis si sente: da zitto (sta pensando) si aspetta la frase
        # intera e la si giudica a fine turno (_on_turn_end interrompe se è rivolta a lui). Il 27/10 un pezzo di
        # voce di un secondo, giudicato male, cancellava la richiesta di un ospite che poi risultava "non rivolta".
        if self.jarvis_active and self.cfg.audio.barge_in and self.player.speaking:
            self._barge_pending = True
            if self.player.speaking:
                self.player.duck(self.cfg.audio.duck_gain)
            if self._barge_task is None or self._barge_task.done():
                self._barge_task = self._spawn(self._confirm_barge_in())

    async def _confirm_barge_in(self) -> None:
        """Conferma l'interruzione solo se dalla voce escono parole vere e diverse da quelle di Jarvis.

        Il solo volume non basta: la voce di Jarvis può rientrare nel microfono (cuffie ad alto volume,
        casse) e senza questo controllo si zittirebbe da sola (successo nella prima prova dal vivo).
        """
        await asyncio.sleep(self.cfg.audio.barge_in_confirm_ms / 1000)
        deadline = time.monotonic() + 2.5
        while self._barge_pending:
            if self.listener_state() != "speech":  # rumore breve, colpo di tosse: si riprende
                self._cancel_barge()
                return
            verdict = await self._judge_barge()
            if not self._barge_pending:
                return
            if verdict == "interrupt":
                await self.interrupt("parla sopra")
                return
            if verdict == "echo" or time.monotonic() > deadline:
                # eco, oppure voce senza parole riconoscibili (musica, TV): Jarvis continua
                self._cancel_barge()
                return
            await asyncio.sleep(0.25)

    def _cancel_barge(self) -> None:
        self._barge_pending = False
        self.player.unduck()

    def _is_own_echo(self, text: str) -> bool:
        """Vero se la frase "sentita" ripete ciò che Jarvis sta dicendo o ha appena detto."""
        ref = self.speaker.full_text if self.jarvis_active else ""
        recent, at = self._recent_jarvis
        if time.monotonic() - at < 8.0:
            ref = f"{ref} {recent}"
        return bool(ref.strip()) and similarity(text, ref) >= 0.6

    async def _judge_barge(self) -> str:
        """"interrupt", "echo" oppure "unsure" (ancora nessuna parola riconoscibile)."""
        if self.listener_audio is None:
            return "interrupt"
        audio = self.listener_audio()
        if audio.size < 16000 * 0.3:
            return "unsure"
        try:
            text = (await self.stt(audio)).strip()
        except Exception as e:
            log.warning("trascrizione per l'interruzione fallita: %s", e)
            return "interrupt"  # nel dubbio vince l'utente
        if is_stop_command(text):
            return "interrupt"
        if not is_meaningful(text):
            return "unsure"
        if self._is_own_echo(text):
            self.bus.emit("ignored", text=text, reason="eco della voce di Jarvis")
            log.warning("eco della propria voce nel microfono: %r", text)
            if self.on_echo:
                self.on_echo()
            return "echo"
        if not mentions_wake_word(text) and not self._continues(await self._identify(audio)):
            return "unsure"  # una voce estranea (TV, un'altra persona) non zittisce Jarvis: serve il nome
        return "interrupt"

    async def _on_partial(self, audio: np.ndarray) -> None:
        if audio is None or audio.size < 16000 * 0.5:
            return
        self._partial_busy = True
        try:
            text = await self.stt(audio)
            if text:
                self.bus.emit("partial", text=passphrase.redact(text))
                if self.jarvis_active and is_stop_command(text):
                    await self.interrupt("comando di stop")
        except Exception as e:
            log.debug("trascrizione parziale fallita: %s", e)
        finally:
            self._partial_busy = False

    async def _on_turn_end(self, audio: np.ndarray, info: dict) -> None:
        t0 = time.perf_counter()
        if info.get("voiced_ms", 1000) < 200:
            self._end_false_alarm()
            return
        who = asyncio.ensure_future(self._identify(audio))  # chi parla, mentre si trascrive
        try:
            text = (await self.stt(audio)).strip()
        except Exception as e:
            who.cancel()
            log.error("trascrizione fallita: %s", e)
            self.bus.emit("error", where="stt", message=str(e))
            self._end_false_alarm()
            return
        stt_ms = 1000 * (time.perf_counter() - t0)
        voice = await who
        pw = passphrase.extract(text)
        if pw is not None:
            result = self.identity.try_passphrase(pw, voice) if self.identity and self.identity.active else "spento"
            log.info("parola d'ordine %s", "accettata" if result == "ok" else f"non accettata ({result})")  # mai il testo
            if result == "ok":
                self._identified(Voice(self.identity.people.owner(), voice.score, voice.embedding, via="parola"),
                                 stt_ms, info)
                return
        text = passphrase.redact(text)  # nominata dentro una frase qualsiasi: il resto non esce mai
        if not is_meaningful(text):
            self.bus.emit("ignored", text=text, reason="nessuna parola")
            self._end_false_alarm()
            return
        if self._is_own_echo(text):
            self.bus.emit("ignored", text=text, reason="eco della voce di Jarvis")
            self._end_false_alarm()
            return
        if self.enrollment is not None:  # dopo il filtro dell'eco: la voce di Jarvis non entra nell'impronta
            await self._enroll_sample(text, audio, voice, info)
            return
        if time.monotonic() < self._enroll_tail_until and reads_enrollment_sentence(text):
            # 26/10: a registrazione finita l'utente leggeva ancora ("Sabato sera andiamo"): interrompeva l'annuncio
            # e la frase arrivava al cervello come una richiesta
            self.bus.emit("ignored", text=text, reason="frase della registrazione, già finita")
            self._end_false_alarm()
            return
        stop = is_stop_command(text)
        if not self.awake:
            self._interlocutor = None
        named = self.push_to_talk_armed or mentions_wake_word(text)
        continues = self._continues(voice)
        # senza nome la conversazione continua solo con chi la sta facendo o con una voce conosciuta (non la TV)
        addressed = named or (self.awake and continues)
        if self.jarvis_active and (stop or addressed):
            await self.interrupt("interrotto dall'utente")
        elif self._barge_pending:
            self._cancel_barge()
        if stop:
            self.bus.emit("user", text=text, stop=True, speaker=voice.label)
            self._set_state("in attesa")
            return
        if self.resting:
            # solo il nome (o il pulsante/Ctrl+Alt+J) lo riaccende: il 26/10 la finestra d'ascolto rimasta aperta
            # dopo "sono a riposo" lo svegliava con le frasi che l'utente diceva a un amico
            if named and self.on_wake is not None:  # "Jarvis, ..." lo riaccende e la frase non va persa
                self.push_to_talk_armed = False
                self.voice = self._interlocutor = voice
                self.bus.emit("user", text=text, stt_ms=round(stt_ms), duration_ms=info.get("duration_ms"),
                              speaker=voice.label, owner=voice.owner)
                self.on_wake(text)
            else:
                self.bus.emit("ignored", text=text, reason="a riposo")
            return
        if not addressed:
            why = "voce estranea alla conversazione" if self.awake and not continues else "non rivolto a Jarvis"
            self.bus.emit("ignored", text=text, reason=why, speaker=voice.label)
            self._set_state("in attesa")
            return
        self.push_to_talk_armed = False
        if named or self._interlocutor is None:
            self._interlocutor = voice
        self.wake()
        self.voice = voice
        self.bus.emit("user", text=text, stt_ms=round(stt_ms), duration_ms=info.get("duration_ms"),
                      speaker=voice.label, owner=voice.owner)
        self._start_turn(text)

    # ------------------------------------------------------------ chi parla

    async def _identify(self, audio: np.ndarray) -> Voice:
        if self.identity is None:
            return LEGACY
        try:
            return await self.identity.who(audio)
        except Exception as e:  # il riconoscimento non deve mai fermare la conversazione: nel dubbio ospite
            log.warning("riconoscimento della voce fallito: %s", e)
            return LEGACY if not self.identity.active else Voice(None)

    def _continues(self, voice: Voice) -> bool:
        """Una frase senza "Jarvis" continua la conversazione? Sì per le voci conosciute; per una voce sconosciuta
        solo se è l'ospite con cui si sta parlando (così la TV o chi passa non si inseriscono)."""
        if self.identity is None or voice.via == "legacy" or voice.known:
            return True
        it = self._interlocutor
        return it is not None and not it.known and self.identity.same_person(it, voice)

    def _identified(self, voice: Voice, stt_ms: float, info: dict) -> None:
        """Parola d'ordine giusta: quella voce vale come l'utente. Si saluta senza mai ripeterla o nominarla."""
        self.push_to_talk_armed = False
        self._interlocutor = voice
        self.voice = voice
        self.wake()
        self.bus.emit("user", text="la parola d'ordine " + passphrase.HIDDEN, stt_ms=round(stt_ms),
                      duration_ms=info.get("duration_ms"), speaker=voice.label, owner=True)
        self._start_turn(f"{self.cfg.persona.user_name} si è appena fatto riconoscere (identità confermata: non dire "
                         "come e non nominare parole d'ordine). Salutalo in una frase.", kind="event")

    def start_enrollment(self, e: Enrollment) -> str:
        """Da adesso le frasi lette servono a imparare la voce (non vanno al cervello)."""
        if self.identity is None or not self.identity.model.available:
            return "Errore: manca il modello per riconoscere le voci."
        self.enrollment = e
        self.bus.emit("show", title=f"Imparo la voce di {e.name}", markdown=e.card())
        self.bus.emit("enroll", name=e.name, status="avviata", progress=0)
        who = "l'utente" if e.role == OWNER else e.name
        return (f"Registrazione avviata: chiedi a {who} di leggere ad alta voce, una alla volta, le frasi comparse "
                f"nella finestra (servono circa {round(e.need_s)} secondi di voce). Le frasi lette non ti arriveranno: "
                "ti avviso io quando ho finito. Per annullare basta dire «annulla».")

    async def _enroll_sample(self, text: str, audio: np.ndarray, voice: Voice, info: dict) -> None:
        e = self.enrollment
        self.bus.emit("ignored", text=passphrase.redact(text), reason="registrazione della voce")
        if is_stop_command(text) or re.search(r"\bannulla", text.lower()):
            self.enrollment = None
            self.bus.emit("enroll", name=e.name, status="annullata", progress=e.progress)
            self._start_turn(f"La registrazione della voce di {e.name} è stata annullata. Dillo in una frase.",
                             kind="event")
            return
        if voice.embedding is None:
            voice = Voice(voice.person, voice.score, await asyncio.to_thread(self.identity.embed, audio), voice.via)
        status = e.add(voice, info.get("voiced_ms", info.get("duration_ms", 0)) / 1000)
        self.bus.emit("enroll", name=e.name, status=status, progress=e.progress)
        if not e.done:
            return
        self.enrollment = None
        self._enroll_tail_until = time.monotonic() + ENROLL_TAIL_S
        quality = e.consistency()
        if quality < MIN_CONSISTENCY:
            log.warning("registrazione della voce di %s scartata: campioni troppo diversi (%.2f)", e.name, quality)
            self.bus.emit("enroll", name=e.name, status="non riuscita", progress=100)
            self._start_turn(f"Non sono riuscito a imparare bene la voce di {e.name}: troppo rumore o più voci "
                             "insieme. Proponi di riprovare in un momento di silenzio.", kind="event")
            return
        self.identity.people.enroll(e.name, e.role, e.samples, e.note)
        log.info("voce di %s imparata: %d frasi, %.0f s, coerenza %.2f", e.name, len(e.samples), e.seconds, quality)
        self.bus.emit("enroll", name=e.name, status="fatta", progress=100)
        first = e.role == OWNER
        self._start_turn(f"Ho imparato la voce di {e.name} ({len(e.samples)} frasi). "
                         + (f"Da adesso riconosci {e.name} dalla voce e le voci sconosciute sono ospiti. "
                            if first else f"Da adesso riconosci {e.name}: per ora può fare solo le cose di base. ")
                         + "Dillo in una frase.", kind="event")

    def _end_false_alarm(self) -> None:
        if self._barge_pending:
            self._barge_pending = False
            self.player.unduck()
        if not self.jarvis_active:
            self._set_state("in attesa")

    async def submit_text(self, text: str) -> None:
        """Messaggio scritto dall'interfaccia: è sempre rivolto a Jarvis e interrompe ciò che sta dicendo."""
        text = text.strip()
        if not text:
            return
        if self.jarvis_active:
            await self.interrupt("messaggio scritto")
        if is_stop_command(text):
            return
        self.voice = self._interlocutor = TYPED  # chi scrive è davanti al PC
        text = passphrase.redact(text)
        if self.resting and self.on_wake is not None:  # scrivere a Jarvis a riposo lo riaccende
            self.bus.emit("user", text=text, typed=True)
            self.on_wake(text)
            return
        self.wake()
        self.bus.emit("user", text=text, typed=True)
        self._start_turn(text)

    # ------------------------------------------------------------ interruzione

    async def interrupt(self, reason: str = "") -> None:
        """Ferma subito voce e generazione, ricordando ciò che è stato davvero detto."""
        self._barge_pending = False
        was_active = self.jarvis_active
        spoken, planned = self.speaker.spoken_text(), self.speaker.full_text
        # prima si blocca la sintesi (nessuna frase nuova può entrare), poi si zittisce la voce
        cancel = asyncio.ensure_future(self.speaker.cancel())
        self.player.stop()
        await cancel
        with contextlib.suppress(Exception):
            await self.brain.interrupt()
        task = self._turn_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.thinking = False
        self.player.unduck()
        if was_active:
            # chi interrompe Jarvis sta parlando con lui: la conversazione resta aperta, niente nome
            self.wake()
            self._recent_jarvis = (spoken or planned, time.monotonic())
            self._interruption = Interruption(spoken, planned)
            self.bus.emit("interrupted", reason=reason, spoken=spoken)
            log.info("Jarvis interrotto (%s) dopo aver detto: %r", reason, spoken[:80])

    async def wait_idle(self, timeout: float = 20.0) -> None:
        """Aspetta che il turno in corso e la sua voce siano finiti (per riavviarsi senza troncare la frase)."""
        turn = self._turn_task
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            if turn and not turn.done():
                await asyncio.wait_for(asyncio.shield(turn), timeout)
            await self.speaker.wait_done(timeout=timeout)

    async def stop_all(self) -> None:
        await self.interrupt("tasto di stop")
        self._events.clear()
        self._set_state("in attesa")

    # ------------------------------------------------------------ turno di risposta

    def _compose(self, text: str, kind: str = "user") -> str:
        ctx = self.context(text)
        parts = []
        if self._interruption and time.time() - self._interruption.at < 120:
            i = self._interruption
            parts.append(f"Mi hai interrotto. Avevo detto ad alta voce solo: \"{i.spoken or '(niente)'}\". "
                         "Il resto della risposta precedente NON è stato pronunciato.")
        self._interruption = None
        who = self._speaker_line()
        if who:
            parts.append(who)
        if self.voice.owner:
            recap = self._guest_recap()
            if recap:
                parts.append(recap)
        if ctx:
            parts.append(ctx)
        head = "<contesto>\n" + "\n".join(parts) + "\n</contesto>\n" if parts else ""
        return head + (text if kind == "user" else f"[EVENTO] {text}")

    def _guest_recap(self, most: int = 6) -> str:
        """Cosa si sono detti Jarvis e le voci non dell'utente da quando gliel'ha riferito l'ultima volta. Il 26/10 la
        sessione dell'utente non ne sapeva niente e Jarvis ha inventato ("anche sotto la vecchietta ho sentito
        l'utente", mentre quella voce era stata trattata da ospite)."""
        since, self._recap_since = self._recap_since, time.time()
        lines: list[str] = []
        open_guest = False
        for h in self.history:
            if h["ts"] <= since:
                continue
            if h["role"] == "ospite":
                lines.append(f"- {h.get('who') or 'un ospite'}: «{_clip(h['text'])}»")
                open_guest = True
            elif h["role"] == "jarvis" and open_guest:
                lines[-1] += f" → hai risposto: «{_clip(h['text'])}»"
                open_guest = False
            else:
                open_guest = False
        if not lines:
            return ""
        return (f"Intanto, mentre non parlava {self.cfg.persona.user_name}, voci che non sono la sua ti hanno parlato "
                "(trattate da ospiti, nella loro sessione):\n" + "\n".join(lines[-most:]))

    def _speaker_line(self) -> str:
        v, owner = self.voice, self.cfg.persona.user_name
        if self.identity is None or v.via in ("legacy", "evento") or not self.identity.active:
            return ""
        if v.via == "tastiera":
            return f"{owner} ti scrive dalla finestra."
        if v.owner:
            return f"A parlare è {owner}."
        if v.person is not None:
            return (f"A parlare è {v.person.name}, una persona che conosci, non {owner}"
                    + (f" ({v.person.note})." if v.person.note else "."))
        return f"A parlare è una voce che non conosci: un ospite, non {owner}."

    def _start_turn(self, text: str, kind: str = "user") -> None:
        if kind == "event" and not self.voice.owner:
            self.voice = EVENT  # Jarvis riferisce all'utente: sessione e permessi suoi
        if kind == "user":
            self.user_turns += 1
            self.last_user_text = text
        self._turn_started_at = time.perf_counter()
        self._turn_task = self._spawn(self._run_turn(text, kind))

    async def _run_turn(self, text: str, kind: str) -> None:
        message = self._compose(text, kind)
        self.thinking = True
        self._set_state("elaborazione")
        self.speaker.start()
        reply = ""
        dismissed = False  # il cervello ha giudicato la frase non rivolta a lui
        filled = False  # già detta la frase pronta per uno strumento lento (SLOW_TOOLS)
        t0 = time.perf_counter()

        def fill(tool: str) -> None:
            nonlocal filled
            if tool in SLOW_TOOLS and not reply.strip() and not dismissed and not filled:
                filled = True
                self.speaker.say(SLOW_TOOLS[tool])
        first_text_ms = None
        narration = NarrationFilter()  # 27/10: "Let me check the status." detto ad alta voce prima di uno strumento

        def speak(text: str) -> None:
            nonlocal reply, first_text_ms
            if not text:
                return
            if first_text_ms is None:
                first_text_ms = 1000 * (time.perf_counter() - t0)
                self._set_state("parla")
                # quando Jarvis prende la parola (anche per un avviso) gli si risponde senza nome
                self.wake()
            if reply and reply[-1] in ".!?:" and not text[0].isspace():  # dopo uno strumento: "status.Direi"
                text = " " + text
            reply += text
            self.speaker.feed(text)
            self.bus.emit("jarvis_delta", text=text)
        try:
            switched = False
            try:
                async for ev in self.brain.respond(message):
                    if isinstance(ev, TextDelta):
                        if dismissed:  # 26/10: dopo nessuna_risposta diceva "Resto in silenzio: ..." ad alta voce
                            continue
                        speak(narration.feed(ev.text))
                    elif isinstance(ev, ToolCall):
                        narration.tool()
                        if ev.name == "nessuna_risposta" and not dismissed:
                            dismissed = True
                            if reply.strip():  # aveva già cominciato a spiegarlo: non si sente niente
                                self.speaker.start()
                                self.player.stop()
                                reply = ""
                        else:  # i cervelli che non segnalano l'inizio della chiamata
                            if not dismissed:
                                self.speaker.boundary()
                            fill(ev.name)
                        self.bus.emit("tool", name=ev.name, args=ev.args)
                    elif isinstance(ev, ToolStart):
                        narration.tool()
                        if ev.name == "nessuna_risposta" and not dismissed:
                            dismissed = True  # zitto già all'inizio della chiamata, non a chiamata completa
                            if reply.strip():
                                self.speaker.start()
                                self.player.stop()
                                reply = ""
                        elif not dismissed:
                            self.speaker.boundary()  # ciò che ha scritto prima dello strumento si dice adesso
                        fill(ev.name)
                    elif isinstance(ev, ToolResult):
                        self.bus.emit("tool_result", name=ev.name, ok=ev.ok, preview=ev.preview)
                    elif isinstance(ev, Notice):
                        self.bus.emit("notice", kind=ev.kind, info=ev.info)
                        if ev.kind == "brain_switch" and not ev.info.get("back") and not switched:
                            switched = True
                            self.speaker.say(switch_sentence(ev.info))
                    elif isinstance(ev, Done):
                        if ev.error:
                            self.bus.emit("error", where="brain", message=ev.error)
                            if not reply.strip() and not dismissed:  # mai il silenzio: si dice che qualcosa è andato storto
                                self.speaker.say(f"Ho avuto un problema nel formulare la risposta, "
                                                 f"{self.cfg.persona.address_as}. Può ripetere?")
            except BrainUnavailable as e:
                log.error("cervello non disponibile: %s", e)
                self.bus.emit("error", where="brain", message=str(e))
                self.speaker.say(unavailable_sentence(e, self.cfg.persona.address_as, already_switched=switched))
            if not dismissed:  # ciò che restava trattenuto senza uno strumento dopo si dice (es. inglese chiesto)
                speak(narration.flush())
            self.thinking = False
            self.speaker.finish()
            role = ("user" if self.voice.owner else "ospite") if kind == "user" else "event"
            self.history.append({"role": role, "text": text, "ts": time.time(),
                                 "who": self.voice.person.name if self.voice.person and role == "ospite" else None})
            await self.speaker.wait_done(timeout=600)
            self.history.append({"role": "jarvis", "text": self.speaker.spoken_text() or reply, "ts": time.time()})
            self._recent_jarvis = (reply, time.monotonic())
            self.bus.emit("jarvis_done", text=reply)
            self.metrics.append({"first_text_ms": first_text_ms,
                                 "first_audio_ms": (1000 * (self.speaker.first_audio_at - self._turn_started_at))
                                 if self.speaker.first_audio_at else None})
            if dismissed and not reply.strip():
                self.sleep()
            else:
                self.wake()
        except asyncio.CancelledError:
            raise
        finally:
            self.thinking = False
            if not self.player.speaking:
                self._set_state("in attesa")
            self._schedule_events()

    def recap_text(self, n: int = 8) -> str:
        """Gli ultimi scambi in breve, per un cervello che subentra senza averli sentiti."""
        who = {"user": self.cfg.persona.user_name, "jarvis": "Jarvis", "event": "Evento"}
        return "\n".join(f"{who.get(h['role'], h['role'])}: {h['text'][:300]}" for h in list(self.history)[-n:])

    # ------------------------------------------------------------ eventi dai sotto-agenti

    def notify(self, text: str) -> None:
        """Un fatto da riferire (compito finito, domanda, errore): parla appena c'è quiete."""
        self._events.append(text)
        self.bus.emit("event_queued", text=text)
        self._schedule_events()

    def _schedule_events(self) -> None:
        if self._events and (self._event_task is None or self._event_task.done()):
            self._event_task = self._spawn(self._deliver_events())

    async def _deliver_events(self) -> None:
        # aspetta un attimo di quiete: niente voce dell'utente, niente risposta in corso
        quiet_since = None
        while self._events:
            busy = self.jarvis_active or self.listener_state() == "speech" or self.resting  # a riposo si aspetta
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= 1.2:
                batch = " ".join(self._events)
                self._events.clear()
                self._start_turn(batch, kind="event")
                return
            await asyncio.sleep(0.1)

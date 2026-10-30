"""Jarvis completo: motori vocali, audio, conversazione, cervello, compiti, memoria."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

from . import autostart, health, paths, winjob
from .apps import games, gmail, whatsapp
from .apps.base import AppError, Apps
from .audio.devices import ResilientBackend
from .audio.listener import Listener
from .audio.loopback import SystemOutputTap
from .audio.pipeline import AudioPipeline
from .audio.turn import SmartTurn
from .audio.vad import SileroVad
from .brain.base import BrainUnavailable, Done
from .brain.claude import ClaudeBrain
from .brain.claude_cli import find_cli
from .brain.local import LocalBrain
from .brain.persona import guest_prompt, system_prompt
from .brain.router import BrainRouter
from .brain.selector import BrainSelector
from .brain.tools import ToolRegistry
from .config import ConfigStore
from .core.bus import Bus
from .core.context import ContextBuilder
from .core.conversation import Conversation
from .core.speaker import ResponseSpeaker
from .identity.admin import VoiceAdmin
from .identity.guard import LEGACY, Identity
from .identity.model import SpeakerModel
from .identity.people import People
from .improve.diary import Diary, Recorder, crash_entries, summary_event
from .media.omni import GpuLease, Omni
from .media.service import MediaWatcher
from .memory.embed import Embedder
from .memory.keeper import MemoryKeeper
from .memory.store import Memory
from .opencode_kit import OpenCodeKit
from .scheduler import Plan, Scheduler, dispatch
from .speech import engine
from .speech.stt import transcribe_file
from .speech.tts import TtsClient
from .speech.voice import VoiceOutput
from .tasks.claude_worker import ClaudeWorker
from .tasks.hands import DesktopLease, build_hands
from .tasks.manager import ACTIVE, TaskManager
from .tasks.opencode_worker import ClaudeQuota, FallbackWorker
from .tasks.safety import SafetyPolicy
from .tasks.self_improve import SelfImproveWorker
from .tools import system as system_tools
from .tools.front import Services, build_front_tools
from .ui.hotkeys import Hotkeys
from .ui.server import UiServer

log = logging.getLogger("jarvis.app")
AUDIO_OUTAGE_TO_MENTION_S = 10.0  # un audio sparito per meno (cuffie ricollegate al volo) non merita parole

EARS_PORT, VOICE_PORT = 8790, 8792
EARS_ALT_PORT = 8788  # l'udito che prende il posto dell'altro (scheda video ⇄ processore) parte qui: nessun buco


class Jarvis:
    def __init__(self, backend=None, memory: Memory | None = None) -> None:
        self.store = ConfigStore()
        self.cfg = self.store.config
        self.bus = Bus(journal=paths.logs_dir() / "eventi.jsonl")
        # le prove (dispositivo simulato) non scrivono mai nella memoria vera: il 5/10 ci erano finite ~150
        # frasi di prova ("quanto fa 17 per 23?") mescolate alle conversazioni dell'utente
        emb = Embedder()
        embedder = emb if backend is None and self.cfg.memory.semantic and emb.available else None
        self.memory = memory or Memory(None if backend is None else paths.data_path("prove", "memoria_prove.db"),
                                       embedder=embedder)
        # idem la scelta della cancellazione d'eco per dispositivo (in aec.json c'era una "stanza simulata")
        self.aec_memory = paths.DATA_DIR / "cache" / "aec.json" if backend is None else None
        # e il diario degli errori (auto-miglioramento spontaneo)
        self.diary = Diary(paths.data_path("diario.db") if backend is None else paths.data_path("prove", "diario_prove.db"))
        self.recorder = Recorder(self.diary, self.cfg.persona.user_name)
        # i programmi (promemoria, richieste e compiti a un'ora o a ripetizione); le prove hanno i loro
        sched = self.cfg.schedule
        self.scheduler = Scheduler(paths.data_path("programmi.db") if backend is None
                                   else paths.data_path("prove", "programmi_prove.db"), self._fire_plan,
                                   enabled=lambda: sched.enabled, catch_up_s=lambda: sched.catch_up_minutes * 60)
        self.backend = backend
        self.exit_code = 0
        self.exit_requested = asyncio.Event()  # __main__ lo aspetta: chiusura voluta o riavvio
        self.resting = False
        self._rest_lock = asyncio.Lock()
        self._voice: VoiceOutput | None = None
        self.whatsapp: whatsapp.WhatsApp | None = None  # WhatsApp nascosto (jarvis/apps/whatsapp.py)
        self.gmail: gmail.Gmail | None = None  # Gmail con l'API di Google (jarvis/apps/gmail.py)
        self.opencode: OpenCodeKit | None = None  # modelli gratis quando Claude è esaurito (jarvis/opencode_kit.py)
        self.claude_quota = ClaudeQuota()  # fino a quando Claude è esaurito (decide dove vanno i compiti)
        self.ears: engine.EngineProcess | None = None
        self.ears_device = "cpu"  # dove gira l'udito adesso ("cuda" o "cpu")
        self.voice_engine: engine.EngineProcess | None = None
        self.pipeline: AudioPipeline | None = None
        # le voci conosciute (dati biometrici: file a parte, solo sul PC); le prove non toccano quello vero
        self.people = People(paths.data_path("voci.db") if backend is None else paths.data_path("prove", "voci_prove.db"))
        self.identity: Identity | None = None
        self.conv: Conversation | None = None
        self.ui: UiServer | None = None
        self.hotkeys: Hotkeys | None = None
        self.started_at = 0.0
        self._bg: set[asyncio.Task] = set()

    # ------------------------------------------------------------ servizi

    async def _engine_event(self, kind: str, info: dict) -> None:
        self.bus.emit("engine", kind=kind, **info)

    async def stt(self, audio) -> str:
        assert self.ears is not None
        r = await transcribe_file(self.ears.host, self.ears.port, "stt", audio, timeout=30)
        return (r.get("text") or "").strip()

    def _ears_process(self, device: str, port: int) -> engine.EngineProcess:
        s = self.cfg.speech
        return engine.EngineProcess(engine.ears_spec(port, device, s.ears_threads, s.stt_model),
                                    on_event=self._engine_event)

    async def _move_ears(self, device: str) -> None:
        """Sposta l'udito tra scheda video e processore senza buchi: il nuovo parte su un'altra porta, poi si
        scambiano e il vecchio si spegne (le trascrizioni leggono la porta a ogni frase)."""
        if self.ears is None or device == self.ears_device:
            return
        new = self._ears_process(device, EARS_ALT_PORT if self.ears.port == EARS_PORT else EARS_PORT)
        await new.start(timeout=60)
        # riscaldato prima dello scambio (intanto sente ancora il vecchio): a freddo la prima frase costava 3,8 s
        import numpy as np

        noise = (0.01 * np.random.default_rng(0).standard_normal(16000)).astype(np.float32)
        with contextlib.suppress(Exception):
            await transcribe_file(new.host, new.port, "stt", noise, timeout=30)
        new.supervise()
        old, self.ears, self.ears_device = self.ears, new, device
        await old.stop()
        log.info("udito spostato sul%s", "la scheda video" if device == "cuda" else " processore")

    def _background(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def _preload_models(self) -> None:
        """Modelli caricati subito dopo "pronto", prima della prima frase e non durante: il 26/10 il modello del
        significato si caricava al primo turno, la CPU piena faceva restare indietro l'audio e la frase arrivava a
        pezzi."""
        if self.identity is not None:
            self._background(asyncio.to_thread(self.identity.model._load))
        self._background(asyncio.to_thread(self.memory.warm))

    # ------------------------------------------------------------ avvio

    async def start(self) -> None:
        t0 = time.perf_counter()
        cfg = self.cfg
        loop = asyncio.get_running_loop()
        self.bus.emit("state", sticky=True, state="avvio")
        # autodiagnosi prima di caricare i motori (dopo, la memoria video libera non dice più niente)
        mic_check = None if self.backend is not None else _has_microphone
        problems = await asyncio.to_thread(health.preflight, cfg, engine.model_path, find_cli,
                                           has_microphone=mic_check)
        for p in problems:
            log.warning("autodiagnosi: %s", p)
            self.bus.emit("notice", kind="preflight", info={"problem": p})
        if self.backend is None:  # solo il Jarvis vero tocca il registro di Windows (non le prove)
            with contextlib.suppress(OSError):
                on, fixed = await asyncio.to_thread(autostart.reconcile, cfg.system.autostart)
                if on != cfg.system.autostart:  # cambiato dall'utente nelle App di avvio di Windows
                    self.store.set("system.autostart", on)
                if fixed:
                    log.info("avvio automatico: %s", fixed)

        # motori vocali (in parallelo) e voce di Jarvis
        voice_meta = engine.load_voice(cfg.speech.tts_voice)
        free = await asyncio.to_thread(health.free_vram_mb)
        self.ears_device = health.ears_device_for(cfg.speech.ears_device, free)
        if self.ears_device != cfg.speech.ears_device:
            log.warning("memoria video quasi piena (%s MB liberi): per questo avvio l'udito resta sul processore", free)
        self.ears = self._ears_process(self.ears_device, EARS_PORT)
        self.voice_engine = engine.EngineProcess(engine.voice_spec(VOICE_PORT, voice_meta, cfg.speech.tts_model),
                                                 on_event=self._engine_event)
        await asyncio.gather(self.ears.start(), self.voice_engine.start())
        self.ears.supervise()
        self.voice_engine.supervise()
        voice = VoiceOutput(TtsClient(self.voice_engine.host, self.voice_engine.port), voice_meta,
                            language="English" if cfg.persona.language == "en" else "Italian",
                            speed=lambda: cfg.speech.tts_speed)  # "velocità voce" a voce, vale subito
        self._voice = voice
        # caricamento e "riscaldamento" prima di dichiararsi pronto: la prima richiesta a freddo costa
        # 4-8 s (caricamento del modello, grafi CUDA) e finirebbe proprio sulla prima risposta
        await self._warm_engines(voice)

        # compiti, strumenti, cervello
        workspace = paths.DATA_DIR / "workspace"
        safety = SafetyPolicy([workspace, *map(Path, cfg.safety.workspace_dirs)], cfg.safety.confirm_destructive)

        desktop = DesktopLease()
        # OpenCode (modelli gratis) quando Claude è esaurito; le prove usano una cartella a parte e non lo legano
        kit = self.opencode = OpenCodeKit(self.store, None if self.backend is None else paths.DATA_DIR / "prove" / "opencode",
                                          adopt=winjob.adopt if self.backend is None else None)
        self.claude_quota = ClaudeQuota(lambda: self.conv.brain.provider_paused_until("claude") if self.conv else None)

        def worker_factory(task):
            if task.kind == "miglioria":  # modifica al codice di Jarvis: copia separata, controlli, conferma
                def inner(t, worktree, env):
                    t.cwd = worktree
                    return ClaudeWorker(t, cfg.persona.user_name, cfg.brain.worker_claude_model,
                                        cfg.brain.worker_claude_effort, SafetyPolicy([worktree], True), env_extra=env)
                return SelfImproveWorker(task, inner, cfg.persona.user_name)
            # di norma Claude Code; OpenCode solo se Claude è esaurito (prima o durante il compito)
            return FallbackWorker(task, lambda: ClaudeWorker(task, cfg.persona.user_name, cfg.brain.worker_claude_model,
                                                             cfg.brain.worker_claude_effort, safety,
                                                             hands=build_hands(cfg.hands, cfg.persona.user_name),
                                                             desktop=desktop),
                                  lambda note: kit.worker(task, safety, desktop, note), self.claude_quota,
                                  enabled=lambda: cfg.brain.opencode and cfg.brain.opencode_workers)

        self.tasks = TaskManager(self.bus, lambda text: self.conv and self.conv.notify(text), worker_factory, workspace)
        # chi parla: acceso solo con il dispositivo vero, il modello presente e (da solo) quando l'utente registra la voce
        model = SpeakerModel()
        if self.backend is None and cfg.identity.enabled and model.available:
            self.identity = Identity(self.people, model, threshold=lambda: cfg.identity.threshold,
                                     unlock_s=lambda: cfg.identity.unlock_minutes * 60)
        admin = (VoiceAdmin(self.people, cfg.persona.user_name, lambda e: self.conv.start_enrollment(e))
                 if self.identity is not None else None)
        apps = self._build_apps() if self.backend is None else None  # le prove non toccano le app
        services = Services(self.bus, self.store, self.memory, self.tasks, voice.fx, restart=self.request_restart,
                            shutdown=self.request_shutdown, rest=self.request_rest,
                            appliers={"system.autostart": autostart.apply}, voices=admin, diary=self.diary,
                            apps=apps, media=self._build_media() if self.backend is None else None,
                            free_models=kit, schedule=self.scheduler,
                            owner_speaking=lambda: self.identity is None or self.conv is None or self.conv.voice.owner)
        tools = build_front_tools(services)
        kit.mount_front(tools)  # gli stessi strumenti (con gli stessi permessi) anche per il cervello su OpenCode
        if self.identity is not None:  # i permessi li controlla il codice, a ogni strumento
            tools.set_gate(lambda tool: Identity.refusal(tool.group, self.conv.voice if self.conv else LEGACY,
                                                         cfg.persona.user_name))
        front_cwd = workspace / "_jarvis"

        def chain_for(persona: str, tag: str = "") -> BrainRouter:
            chain = [ClaudeBrain(persona, tools, cfg.brain.claude_model, cfg.brain.claude_effort, cwd=front_cwd,
                                 name=f"claude{tag}"),
                     ClaudeBrain(persona, tools, cfg.brain.fast_claude_model, cfg.brain.fast_claude_effort, cwd=front_cwd,
                                 name=f"claude-veloce{tag}"),
                     kit.brain(persona, tag)]  # spento a voce (brain.opencode) dice "non disponibile" e si passa oltre
            if cfg.brain.cloud_fallback:
                chain.append(LocalBrain(persona, tools, cfg.brain.ollama_base_url, cfg.brain.cloud_model,
                                        kind="ollama", name=f"{cfg.brain.cloud_model}{tag}", provider="ollama-cloud"))
            if cfg.brain.fallback_to_local:  # Bonsai 2: se non è acceso fallisce subito e si passa oltre
                chain.append(LocalBrain(persona, tools, cfg.brain.local_base_url, cfg.brain.local_model,
                                        cfg.brain.local_api_key, name=f"local{tag}"))
            return BrainRouter(chain, preferred=lambda: cfg.brain.provider)

        # gli ospiti parlano con un'altra sessione: niente profilo, niente conversazioni dell'utente da far trapelare
        brain = BrainSelector(chain_for(system_prompt(cfg, self.memory.profile())),
                              lambda: chain_for(guest_prompt(cfg), " (ospiti)"),
                              who=lambda: "owner" if self.conv is None or self.conv.voice.owner else "guest")
        ctx = ContextBuilder(self.tasks.summary, system_tools.active_window,
                             lambda q: [f.text for f in self.memory.search(q, k=4, touch=True)])

        def context(text: str) -> str:  # a un ospite niente memoria, finestra in primo piano o compiti dell'utente
            return ctx.build(text, owner=self.conv is None or self.conv.voice.owner)

        # audio
        listener = Listener(cfg.audio, SileroVad().feed, SmartTurn().probability)
        backend = self.backend or ResilientBackend(
            cfg.audio.input_device, cfg.audio.output_device,
            on_change=lambda st: loop.call_soon_threadsafe(self._audio_changed, st))
        self.pipeline = AudioPipeline(
            cfg.audio, backend, listener, loop,
            on_listen=lambda ev: self.conv.on_listen_event(ev),
            on_player=lambda kind, seg: self.conv.on_player_event(kind, seg),
            on_level=self._emit_level,
            aec_memory=self.aec_memory,
            reference=SystemOutputTap() if self.backend is None else None,  # le prove non catturano il PC
        )
        speaker = ResponseSpeaker(voice, self.pipeline.play,
                                  on_event=lambda kind, **info: self.bus.emit(kind, **info))
        self.conv = Conversation(cfg, self.bus, self.pipeline.player, speaker, self.stt, brain,
                                 listener_state=lambda: listener.state, context=context,
                                 listener_audio=listener.snapshot, on_echo=self.pipeline.force_aec, identity=self.identity,
                                 awake_window_s=cfg.ui.conversation_window_s)
        self.conv.on_wake = lambda text: self._background(self.wake_up(text))
        self._background(self._journal_turns())
        self._background(self._keep_memory(front_cwd))
        self._background(self._keep_diary())
        self._background(self._keep_apps())
        if self.backend is None:  # le prove non accendono OpenCode né fanno esami
            self._background(self._keep_opencode())

        self.ui = UiServer(self, port=cfg.ui.port)
        await self.ui.start()
        await brain.start()
        self.pipeline.start()
        if self.backend is None:  # tasti globali solo con l'audio vero (non nei test)
            self.hotkeys = Hotkeys(loop)
            self.hotkeys.add(cfg.ui.hotkey_talk, self.talk_key)
            self.hotkeys.add(cfg.ui.hotkey_stop, lambda: self._background(self.conv.stop_all()))
            self.hotkeys.start()
        self.started_at = time.monotonic()
        self.bus.emit("state", sticky=True, state="in attesa")
        self.bus.emit("ready", seconds=round(time.perf_counter() - t0, 1), devices=backend.describe())
        log.info("Jarvis pronto in %.1f s", time.perf_counter() - t0)
        self._background(voice.warm_up())
        self._preload_models()
        # battito per il guardiano; la versione diventa "sana" solo se gira sotto il guardiano ed è salvata
        supervised = os.environ.get("JARVIS_SUPERVISED") == "1"
        commit = await asyncio.to_thread(health.clean_commit) if supervised else None
        self._background(health.Heartbeat(commit=commit).run(lambda: getattr(self.conv, "state", "attivo")))
        note = health.take_recovery_note() if supervised else None
        if note:
            self.conv.notify(f"Avviso di sistema: {health.recovery_sentence(note, cfg.persona.address_as)} "
                             f"Dillo all'utente in una frase.")
        if problems:
            self.conv.notify("Avviso di sistema, problemi trovati all'avvio: " + " ".join(problems)
                             + " Riferiscili all'utente in breve.")
        self._background(self.scheduler.run())  # dopo "pronto": i programmi "a ogni accensione" e i recuperi

    async def _fire_plan(self, plan: Plan, due_at: float) -> None:
        """È l'ora di un programma: a riposo, se si deve, Jarvis si riaccende per dirlo; poi un compito parte da solo,
        avvisi e richieste arrivano al cervello come eventi (con i permessi dell'utente, tranne gli ospiti)."""
        if self.conv is None:
            return
        if self.resting and plan.action != "compito" and self.cfg.schedule.wake_from_rest:
            await self.wake_up(None, announce=False)
        await dispatch(plan, due_at, time.time(), self.conv.notify, self.tasks.spawn if self.tasks else None,
                       self.cfg.persona.user_name)

    def _build_apps(self) -> Apps:
        """Le abilità (jarvis/apps): permessi di chi parla, conferme con un sì detto dopo la richiesta."""
        cfg = self.store.config

        def may(group: str) -> str | None:
            if self.identity is None:
                return None
            return Identity.refusal(group, self.conv.voice if self.conv else LEGACY, cfg.persona.user_name)

        data = paths.data_path("whatsapp")
        self.whatsapp = whatsapp.WhatsApp(
            whatsapp.Bridge(whatsapp.bridge_command(data), adopt=winjob.adopt), data,
            mode=lambda: cfg.apps.whatsapp, alerts=lambda: cfg.apps.whatsapp_avvisi,
            notify=lambda text: self.conv and self.conv.notify(text))
        wa = whatsapp.build(self.whatsapp, transcribe=lambda path: whatsapp.transcribe_with(self.stt, path))
        self.gmail = gmail.Gmail(paths.data_path("gmail"), Path.home() / "Downloads",
                                 notify=lambda text: self.conv and self.conv.notify(text))
        return Apps([wa, gmail.build(self.gmail), games.build(games.Library(games.steam_path()))], may=may,
                    turns=lambda: self.conv.user_turns if self.conv else 0,
                    last_text=lambda: self.conv.last_user_text if self.conv else "",
                    confirm=lambda: cfg.safety.confirm_destructive)

    def _build_media(self) -> MediaWatcher:
        """Guardare video e ascoltare audio (jarvis/media): le orecchie di Jarvis per il parlato, WhatsApp per i suoi
        video e vocali, e il modello dei suoni che prende in prestito la scheda video al posto della voce."""
        cfg = self.cfg

        async def voice_on() -> None:
            await self.voice_engine.start()
            self.voice_engine.supervise()
            if self._voice:
                await self._voice.prime()

        async def wanted_ears() -> str:  # come al risveglio: sulla scheda video solo se ci sta
            return health.ears_device_for(cfg.speech.ears_device, await asyncio.to_thread(health.free_vram_mb))

        async def quiet() -> None:  # la frase "lo ascolto" deve finire prima che la voce si spenga
            if self.conv is not None:
                await self.conv.speaker.wait_said(timeout=20)

        def may_messages() -> str | None:
            if self.identity is None:
                return None
            return Identity.refusal("messaggi", self.conv.voice if self.conv else LEGACY, cfg.persona.user_name)

        async def wa_media(chat: str, dest: Path, which: str) -> tuple[Path, str]:
            if self.whatsapp is None:
                raise AppError("WhatsApp non è disponibile")
            return await whatsapp.fetch_media(self.whatsapp, chat, dest, which)

        lease = GpuLease(lock=self._rest_lock, resting=lambda: self.resting, quiet=quiet,
                         stop_voice=lambda: self.voice_engine.stop(), start_voice=voice_on,
                         ears_device=lambda: self.ears_device, move_ears=self._move_ears, wanted_ears=wanted_ears,
                         notify=lambda text: self.conv and self.conv.notify(text))
        return MediaWatcher(lambda: cfg.media, self.stt, whatsapp=wa_media, may_whatsapp=may_messages,
                            omni=Omni(lambda: cfg.media.omni, lease))

    async def _keep_apps(self) -> None:
        """Ogni mezzo minuto: WhatsApp acceso sempre, spento, o spento dopo 15 minuti senza usarlo."""
        while True:
            await asyncio.sleep(30)
            if self.whatsapp is not None:
                try:
                    await self.whatsapp.tick()
                except Exception:  # un'app che non risponde non deve mai fermare Jarvis
                    log.exception("controllo di WhatsApp fallito")

    async def _keep_opencode(self) -> None:
        """Ogni minuto: OpenCode spento se non serve più; esame dei modelli gratis quando tocca, solo in quiete."""
        while True:
            await asyncio.sleep(60)
            if self.opencode is None or self.conv is None:
                continue
            busy = self.resting or self.conv.jarvis_active or self.conv.thinking
            quiet = 0.0 if busy else time.time() - self.recorder.last_activity_at
            try:
                await self.opencode.tick(quiet, self.claude_quota.exhausted_until() is not None)
            except Exception:  # OpenCode non deve mai disturbare Jarvis
                log.exception("manutenzione di OpenCode fallita")

    def request_restart(self) -> str:
        """Riavvio chiesto a voce: il guardiano lo fa ripartire subito (non è una caduta)."""
        if os.environ.get("JARVIS_SUPERVISED") != "1":
            return ("Non posso riavviarmi da solo: non sono partito dal guardiano. Va riavviato a mano con "
                    "Avvia Jarvis.")
        self._background(self._restart_when_quiet())
        return "riavvio in arrivo: mi riavvio appena finisco di parlare"

    # ------------------------------------------------------------ riposo: scheda video libera

    def _audio_changed(self, st: dict) -> None:
        """Il dispositivo audio è cambiato, sparito o tornato: eco rimisurata, finestra avvisata; dopo un'assenza
        lunga Jarvis lo dice (prima non poteva: senza audio si vede solo nella finestra)."""
        if self.pipeline is not None:
            self.pipeline.devices_changed()
        self.bus.emit("audio", sticky=True, **st)
        if st["ok"] and st.get("outage_s", 0) >= AUDIO_OUTAGE_TO_MENTION_S and self.conv is not None:
            self.conv.notify(f"Avviso di sistema: l'audio era sparito per {round(st['outage_s'])} secondi ed è "
                             f"tornato (ora uso {st['output']}). Dillo all'utente in una frase.")

    def identity_state(self) -> dict:
        """Per la finestra: riconoscimento acceso, voci conosciute, parola d'ordine impostata (mai il suo testo)."""
        return {"available": self.identity is not None, "active": bool(self.identity and self.identity.active),
                "people": [{"name": p.name, "owner": p.owner} for p in self.people.all()],
                "passphrase": self.people.has_passphrase()}

    def set_passphrase(self, text: str) -> str:
        """Dalla finestra (chi la usa è davanti al PC). Il testo non va mai in registri o eventi."""
        try:
            self.people.set_passphrase(text)
        except ValueError as e:
            return str(e)
        log.info("parola d'ordine impostata")
        self.bus.emit("identity", **self.identity_state())
        return "ok"

    def _emit_level(self, mic: float, out: float) -> None:
        """Livelli audio per la finestra, con quanta parte della risposta è già stata detta (sottotitoli)."""
        said = self.conv.speaker.said_chars() if self.conv and self.conv.speaker.segments else -1
        self.bus.emit("level", mic=round(mic, 4), out=round(out, 4), said=said)

    def talk_key(self) -> None:
        """Ctrl+Alt+J: a riposo riaccende Jarvis, altrimenti "parla senza dire il nome"."""
        if self.resting:
            self._background(self.wake_up(None))
        elif self.conv:
            self.conv.arm_push_to_talk()

    def request_rest(self) -> str:
        if self.resting:
            return "sono già a riposo"
        self._background(self.rest())
        return "mi metto a riposo appena finisco di parlare: libero la scheda video"

    async def rest(self) -> None:
        """Scarica i modelli e libera tutta la scheda video (voce ~3,9 GB, e l'udito passa sul processore): il resto
        di Jarvis resta acceso e leggero."""
        if self.conv:
            await self.conv.wait_idle(timeout=20)  # prima finisce la frase
        async with self._rest_lock:
            if self.resting:
                return
            self.resting = True
            if self.conv:
                self.conv.resting = True
                self.conv.sleep()  # niente finestra d'ascolto aperta: a riposo si risponde solo al nome
            keep_ears = self.cfg.system.rest_keeps_ears
            await self.voice_engine.stop()
            if not keep_ears:
                await self.ears.stop()
                if self.conv:
                    self.conv.deaf = True
            elif self.ears_device == "cuda":  # sente ancora il nome, ma dal processore: la scheda video si libera
                await self._move_ears("cpu")
            if self.conv:
                self.conv._set_state("riposo")
            self.bus.emit("rest", sticky=True, active=True, listening=keep_ears)
            log.info("a riposo: modelli scaricati (orecchie %s)", "accese" if keep_ears else "spente")

    async def wake_up(self, then: str | None = None, announce: bool = True) -> None:
        """Ricarica i modelli; then = la frase con cui l'utente l'ha svegliato (diventa un turno normale);
        announce=False: niente "sono di nuovo operativo" (lo sveglia un programma, che ha già la sua frase)."""
        async with self._rest_lock:
            if self.resting:
                if self.conv:
                    self.conv._set_state("avvio", detail="mi riaccendo")
                t = time.perf_counter()
                # l'udito torna sulla scheda video se ci sta (misurato adesso: magari c'è ancora un gioco aperto)
                wanted = health.ears_device_for(self.cfg.speech.ears_device,
                                                await asyncio.to_thread(health.free_vram_mb))
                starts = [self.voice_engine.start()]
                if self.ears.proc is None or self.ears.proc.returncode is not None:
                    starts.append(self.ears.start())
                elif wanted != self.ears_device:
                    starts.append(self._move_ears(wanted))
                await asyncio.gather(*starts)
                self.voice_engine.supervise()
                self.ears.supervise()
                if self._voice:
                    await self._voice.prime()
                self.resting = False
                if self.conv:
                    self.conv.resting = self.conv.deaf = False
                    self.conv._set_state("in attesa")
                self.bus.emit("rest", sticky=True, active=False, listening=True)
                log.info("riacceso in %.1f s", time.perf_counter() - t)
                if then is None and announce and self.conv:
                    self.conv.notify("Avviso di sistema: sei di nuovo operativo dopo il riposo. Dillo in poche parole.")
        if then and self.conv:
            self.conv.resume_turn(then)

    def request_shutdown(self) -> str:
        """Spegnimento chiesto a voce: codice 0, quindi anche il guardiano si ferma."""
        self._background(self._exit_when_quiet(0))
        return "spegnimento in arrivo: mi spengo appena finisco di parlare"

    async def _restart_when_quiet(self) -> None:
        await self._exit_when_quiet(health.RESTART_CODE)

    async def _exit_when_quiet(self, code: int) -> None:
        if self.conv:  # il turno che l'ha chiesto deve ancora dire la sua frase
            await self.conv.wait_idle(timeout=20)
        log.info("esco su richiesta (codice %s)", code)
        self.exit_code = code
        self.exit_requested.set()

    async def _warm_engines(self, voice: VoiceOutput) -> None:
        import numpy as np

        t = time.perf_counter()
        self.bus.emit("state", sticky=True, state="avvio", detail="carico orecchie e voce")
        rng = np.random.default_rng(0)
        await asyncio.gather(
            self.stt((0.01 * rng.standard_normal(16000)).astype(np.float32)),
            voice.prime(),  # sintesi vera: dalla cache il modello resterebbe da caricare
        )
        log.info("motori caricati e pronti in %.1f s", time.perf_counter() - t)

    async def _keep_memory(self, cwd: Path) -> None:
        """In sottofondo: vettori di significato per frasi e ricordi nuovi; a conversazione finita il custode
        aggiorna i ricordi. Si ferma mentre Jarvis parla o lavora; se il cervello non risponde riprova più tardi."""
        keeper = MemoryKeeper(self.memory, lambda system, msg: self._ask_once(system, msg, cwd),
                              user=self.cfg.persona.user_name, on_change=lambda ch: self.bus.emit("memory", **ch))
        await asyncio.to_thread(keeper.start_from_now)
        pause = 60.0
        while True:
            await asyncio.sleep(pause)
            pause = 60.0
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.memory.embed_pending, 64)
            if not self.cfg.memory.keeper or (self.conv is not None and self.conv.jarvis_active):
                continue
            run = await keeper.run_once(self.cfg.memory.keeper_idle_s)
            if run.status == "errore":
                log.info("custode della memoria: %s (riprovo tra 10 minuti)", run.detail)
                pause = 600.0
            elif run.rejected:
                log.info("custode della memoria: scartate %s", "; ".join(run.rejected))

    async def _ask_once(self, system: str, message: str, cwd: Path) -> str:
        """Una domanda sola a un cervello usa e getta (il custode): Claude Haiku, un modello gratis di OpenCode o il
        cloud gratuito di Ollama, poi chiuso."""
        cfg = self.cfg
        kit = getattr(self, "opencode", None)
        first = cfg.memory.keeper_brain
        if first == "auto":
            first = {"ollama-cloud": "cloud", "opencode": "opencode"}.get(cfg.brain.provider, "claude")
        errors = []
        for kind in dict.fromkeys((first, "claude", "opencode", "cloud")):
            if kind == "cloud" and not cfg.brain.cloud_fallback:
                continue
            if kind == "opencode" and (kit is None or not cfg.brain.opencode):
                continue
            if kind == "claude":
                brain = ClaudeBrain(system, ToolRegistry(), cfg.brain.fast_claude_model, cfg.brain.fast_claude_effort,
                                    builtin_tools=[], cwd=cwd, max_turns=1, name="custode")
            elif kind == "opencode":
                brain = kit.text_brain(system)
            else:
                brain = LocalBrain(system, ToolRegistry(), cfg.brain.ollama_base_url, cfg.brain.cloud_model,
                                   kind="ollama", name="custode-cloud", provider="ollama-cloud")
            try:
                await brain.start()
                async for ev in brain.respond(message):
                    if isinstance(ev, Done):
                        if ev.error:
                            raise BrainUnavailable(ev.error)
                        if ev.text.strip():
                            return ev.text
                errors.append(f"{kind}: risposta vuota")
            except Exception as e:
                errors.append(f"{kind}: {e}")
            finally:
                with contextlib.suppress(Exception):
                    await brain.stop()
        raise BrainUnavailable("; ".join(errors) or "nessun cervello per il custode")

    async def _journal_turns(self) -> None:
        q = self.bus.subscribe()
        while True:
            ev = await q.get()
            if ev["type"] == "user":  # le frasi degli ospiti restano, ma non come dell'utente
                self.memory.log_turn("user" if ev.get("owner", True) else "ospite", ev.get("text", ""))
            elif ev["type"] == "jarvis_done":
                self.memory.log_turn("jarvis", ev.get("text", ""))

    # ------------------------------------------------------------ diario degli errori

    async def _keep_diary(self) -> None:
        """Annota in silenzio gli errori dagli eventi; di rado, al momento giusto, fa proporre quelli ripetuti."""
        if self.backend is None:  # le prove non leggono le cadute vere
            await asyncio.to_thread(self._note_crashes)
        q = self.bus.subscribe(2000)

        async def listen() -> None:
            while True:
                ev = await q.get()
                with contextlib.suppress(Exception):  # il diario non deve mai disturbare Jarvis
                    self.recorder.handle(ev)

        listener = asyncio.ensure_future(listen())
        try:
            while True:
                await asyncio.sleep(60)
                with contextlib.suppress(Exception):
                    self._maybe_propose()
        finally:
            listener.cancel()
            self.bus.unsubscribe(q)

    def _note_crashes(self) -> None:
        """Le cadute nei rapporti del guardiano dall'ultima volta (al primo avvio si comincia da adesso)."""
        seen = self.diary.get_meta("cadute_viste")
        if seen is None:  # le cadute di prima del diario non contano
            self.diary.set_meta("cadute_viste", str(time.time()))
            return
        reports = [p for p in sorted((paths.logs_dir() / "cadute").glob("*.txt")) if p.stat().st_mtime > float(seen)]
        for e in crash_entries(reports):
            self.diary.note(e)
        if reports:  # fin dove si è letto: l'ora dei rapporti, non quella di adesso (mai contati due volte)
            self.diary.set_meta("cadute_viste", str(max(p.stat().st_mtime for p in reports)))

    def _quiet_for_proposals(self, now: float) -> bool:
        """Mai mentre lavora o gioca: l'utente presente (ha parlato negli ultimi 10 minuti), conversazione finita da
        30 s, Jarvis zitto, nessun compito in corso, non a riposo."""
        conv, rec = self.conv, self.recorder
        if conv is None or self.resting or conv.jarvis_active or conv.thinking:
            return False
        if self.tasks is not None and any(t.status in ACTIVE for t in self.tasks.tasks.values()):
            return False
        return now - rec.last_owner_at < 600 and now - rec.last_activity_at > 30

    def _maybe_propose(self, now: float | None = None) -> bool:
        now = now or time.time()
        if not self._quiet_for_proposals(now) or not self.diary.due(self.cfg.system.proposals, now):
            return False
        found = self.diary.candidates(now)
        if not found:  # niente di ripetuto: nessun riepilogo (e si riguarda più avanti)
            return False
        self.diary.listed = [p.key for p in found]
        self.diary.summary_done(now)
        log.info("auto-miglioramento: %d proposte nel riepilogo", len(found))
        self.conv.notify(summary_event(found, self.cfg.persona.user_name))
        return True

    async def stop(self) -> None:
        for t in list(self._bg):
            t.cancel()
        if self.hotkeys:
            self.hotkeys.stop()
        if self.ui:
            await self.ui.stop()
        if self.conv:
            with contextlib.suppress(Exception):
                await self.conv.stop_all()
            with contextlib.suppress(Exception):
                await self.conv.brain.stop()
        if self.pipeline:
            self.pipeline.stop()
        if self.whatsapp is not None:  # Chrome di WhatsApp si chiude con garbo (la sessione resta salvata)
            with contextlib.suppress(Exception):
                await self.whatsapp.bridge.stop()
        if self.opencode is not None:
            with contextlib.suppress(Exception):
                await self.opencode.stop()
        for e in (self.ears, self.voice_engine):
            if e:
                await e.stop()
        self.memory.close()
        self.people.close()
        self.diary.close()
        self.scheduler.close()


def _has_microphone() -> bool:
    try:
        import sounddevice as sd

        return any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices())
    except Exception:  # driver audio assenti o rotti: per l'autodiagnosi è "nessun microfono"
        return False


async def run_forever() -> None:
    app = Jarvis()
    try:
        await app.start()
        await asyncio.Event().wait()
    finally:
        await app.stop()

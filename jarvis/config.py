"""Impostazioni di Jarvis.

I valori predefiniti stanno qui, nel codice. L'utente (o Jarvis stesso, su richiesta a voce)
sovrascrive solo quello che vuole in `data/config.yaml`. Le chiavi sconosciute non vengono mai
ignorate in silenzio: producono un errore chiaro, così un refuso non diventa un comportamento
misterioso.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

import yaml

from . import paths


class ConfigError(ValueError):
    pass


# Valori ammessi per le impostazioni a scelta chiusa.
CHOICES: dict[str, set[str]] = {
    "audio.aec_mode": {"auto", "on", "off"},
    "audio.echo_reference": {"system", "jarvis"},
    "brain.provider": {"claude", "opencode", "ollama-cloud", "local"},
    "brain.claude_access": {"abbonamento", "chiave"},
    "brain.opencode_privacy": {"tutti", "senza addestramento"},
    "brain.claude_effort": {"low", "medium", "high", "xhigh", "max"},
    "brain.worker_claude_effort": {"low", "medium", "high", "xhigh", "max"},
    "brain.fast_claude_effort": {"off", "low", "medium", "high"},
    "hands.disposable_browser_channel": {"chrome", "msedge"},
    "speech.tts_model": {"qwen3-1.7b", "qwen3-0.6b", "omnivoice"},
    "persona.language": {"it", "en"},
    "speech.ears_device": {"cpu", "cuda"},
    "speech.stt_model": {"parakeet-ultra", "parakeet-v3"},
    "system.proposals": {"mai", "settimana", "giorno"},
    "ui.theme": {"mark3", "cinema"},
    "ui.effects": {"pieni", "sobri"},
    "memory.keeper_brain": {"auto", "claude", "opencode", "cloud"},
    "apps.whatsapp": {"su richiesta", "sempre", "spento"},
    "media.omni": {"spento", "audio-flamingo-next", "qwen2.5-omni-7b", "lfm3-omni-2b"},
}


@dataclass
class AudioConfig:
    input_device: str | None = None  # None = predefinito di Windows; altrimenti parte del nome
    output_device: str | None = None  # come input_device, per le casse o le cuffie
    aec_mode: str = "auto"  # "auto" (misura se serve), "on", "off". Con le cuffie si spegne da sola
    noise_suppression: bool = True  # riduzione del rumore di fondo sul microfono
    # da cosa si toglie l'eco: "system" = tutto ciò che suona sull'uscita (video, musica, giochi e Jarvis), così
    # Jarvis non capisce le parole di un video ascoltato in cuffia; "jarvis" = solo la sua voce
    echo_reference: str = "system"
    vad_threshold: float = 0.5  # probabilità Silero oltre cui è voce
    vad_threshold_while_speaking: float = 0.75  # più severa mentre Jarvis parla (eco residua)
    min_speech_ms: int = 160  # sotto questa durata è un rumore, non un inizio di frase
    min_silence_ms: int = 280  # silenzio minimo prima di interrogare Smart Turn
    max_silence_ms: int = 1600  # dopo questo silenzio il turno è finito comunque
    turn_threshold: float = 0.5  # probabilità Smart Turn di "ho finito"
    pre_roll_ms: int = 300  # audio tenuto prima dell'inizio voce (non tagliare la prima sillaba)
    barge_in: bool = True  # permette di interrompere Jarvis parlando
    barge_in_confirm_ms: int = 350  # voce continua necessaria per confermare l'interruzione
    duck_gain: float = 0.25  # volume di Jarvis mentre decide se è stato interrotto
    output_gain: float = 1.0  # volume della voce di Jarvis (1 = normale)


@dataclass
class SpeechConfig:
    # quale voce: una in data/voices (es. "jarvis", quella del film) o una inclusa, "base-it" o "base-en"
    tts_voice: str = "jarvis"
    tts_model: str = "qwen3-1.7b"  # chi clona la voce: qwen3-1.7b (scelta dall'utente), qwen3-0.6b, omnivoice
    tts_speed: float = 1.0  # ritmo della voce: 1 = ~19 caratteri al secondo
    # chi trascrive: parakeet-ultra (dal 26/10: stesso Parakeet rifinito da Moondream, errori 3,34 → 2,12 % su
    # FLEURS italiano alla stessa velocità) oppure parakeet-v3 (quello di prima, di riserva)
    stt_model: str = "parakeet-ultra"
    # Orecchie (Parakeet): sulla scheda video dal 26/10. Con audio.cpp 0.9.2 trascrivono 10 s di parlato in 41 ms
    # invece di 864 (processore), rallentano la voce solo del 3-4 % (il 6/10, con la 0.9.0, di 2×) e voce + udito
    # stanno in ~5,6 GB. A riposo passano da sole sul processore (la scheda video si libera tutta); se all'avvio
    # la memoria video non basta restano sul processore. "cpu" = sempre sul processore.
    ears_device: str = "cuda"
    ears_threads: int = 6  # thread del processore usati dalle orecchie


@dataclass
class BrainConfig:
    provider: str = "claude"  # chi parla per primo: "claude", "opencode", "ollama-cloud" o "local" (gli altri ripieghi)
    # come si paga Claude: "abbonamento" (login di claude.ai) o "chiave" (chiave API di Anthropic, a consumo). La
    # chiave non sta mai qui: la salva cifrata il primo avvio (data/claude), o data/claude/chiave.txt, o ANTHROPIC_API_KEY
    claude_access: str = "abbonamento"
    # cervello frontale: misurato, Opus 5.5 a sforzo basso è il più rapido alla prima parola (~0,8 s) e il più "Jarvis"
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"  # quanto ragiona il cervello frontale: più alto = più lento a rispondere
    worker_claude_model: str = "claude-opus-5-5"  # sotto-agenti: conta la qualità
    worker_claude_effort: str = "high"  # quanto ragionano i sotto-agenti
    # modello veloce: ripiego quando il principale non risponde e custode della memoria (domande brevi e frequenti)
    fast_claude_model: str = "claude-haiku-5-5"
    fast_claude_effort: str = "low"  # "off" = ragionamento spento (più rapido, se il modello lo permette)
    local_base_url: str = "http://127.0.0.1:8085/v1"  # Bonsai 2 (fork llama.cpp PrismML)
    local_model: str = "bonsai2"  # nome del modello sul server locale
    local_api_key: str = "local"  # chiave per il server locale (llama.cpp non la controlla)
    fallback_to_local: bool = True  # se Claude non risponde, prova il locale (Bonsai, se è acceso)
    # ripiego gratuito su Ollama cloud: niente VRAM, ~0,9 s alla prima parola, usa gli strumenti.
    # Solo quando Claude non risponde; i messaggi passano dai server di Ollama.
    cloud_fallback: bool = True
    cloud_model: str = "gpt-oss:120b-cloud"  # modello cloud di Ollama usato come ripiego
    ollama_base_url: str = "http://127.0.0.1:11434/v1"  # indirizzo di Ollama sul PC
    # OpenCode Zen, modelli GRATIS quando l'uso di Claude è esaurito (dopo i Claude, prima di Ollama cloud e del
    # locale), a voce e per i sotto-agenti. Un OpenCode tutto di Jarvis (data/opencode), acceso solo quando serve;
    # va anche senza chiave (la chiave dell'utente: data/opencode/chiave.txt, poi cifrata, o OPENCODE_API_KEY)
    opencode: bool = True
    # modello a voce: "auto" = il vincitore dell'esame (italiano, strumenti, prima parola), oppure un nome preciso
    opencode_model: str = "auto"
    opencode_worker_model: str = "auto"  # modello dei sotto-agenti su OpenCode: "auto" = la stessa classifica
    # dati: "tutti" i modelli gratis, oppure "senza addestramento" (esclude chi può usare i messaggi per addestrarsi)
    opencode_privacy: str = "tutti"
    opencode_exam_hours: float = 72.0  # ogni quante ore rifare l'esame dei modelli gratis (0 = mai da solo)
    opencode_workers: bool = True  # Claude esaurito: i compiti continuano su OpenCode (False = si fermano)


@dataclass
class PersonaConfig:
    user_name: str = "Utente"  # come si chiama l'utente
    address_as: str = "signore"  # come Jarvis si rivolge all'utente
    formal: bool = True  # dare del Lei
    language: str = "it"  # lingua in cui Jarvis parla: it (italiano) o en (inglese); la voce di base la segue


@dataclass
class SafetyConfig:
    confirm_destructive: bool = True  # chiedi prima di cancellare, inviare, comprare...
    workspace_dirs: list[str] = field(default_factory=list)  # dove i sotto-agenti scrivono liberi


@dataclass
class HandsConfig:
    """Cosa possono usare i sotto-agenti oltre a shell, file e web."""
    user_browser: bool = True  # il browser dell'utente con i suoi accessi (Claude in Chrome)
    disposable_browser: bool = True  # un browser pulito e isolato, per ricerche e prove
    disposable_browser_channel: str = "chrome"  # quale browser installato usare: chrome o msedge
    disposable_browser_visible: bool = False  # True = finestra visibile (ruba il fuoco mentre lavori)
    desktop: bool = True  # mouse e tastiera sulle app di Windows (un compito alla volta)


@dataclass
class UiConfig:
    port: int = 8791  # porta locale della finestra di Jarvis
    hotkey_talk: str = "ctrl+alt+j"  # premi per parlare / attiva l'ascolto
    hotkey_stop: str = "ctrl+alt+k"  # ferma tutto
    wake_word: bool = True  # serve dire "Jarvis" per iniziare (False = ascolta sempre)
    conversation_window_s: float = 30.0  # dopo uno scambio puoi continuare senza dire "Jarvis"
    theme: str = "mark3"  # grafica: mark3 (strumento al centro, bianco su nero) o cinema (linea di luce, sottotitoli)
    accent: str = ""  # colore della voce di Jarvis: vuoto = quello del tema, oppure un nome italiano o #rrggbb
    text_scale: float = 1.0  # grandezza dei testi nella finestra (0,7-1,8)
    effects: str = "pieni"  # pieni = animazioni ed effetti di luce completi, sobri = più calmi e leggeri


@dataclass
class SystemConfig:
    autostart: bool = False  # Jarvis parte da solo all'accesso a Windows (sotto il guardiano, senza console)
    # A riposo (scheda video libera) le orecchie restano accese sul processore: basta dire "Jarvis" per
    # riaccenderlo. False = a riposo non ascolta nemmeno (si riaccende dalla finestra o con Ctrl+Alt+J).
    rest_keeps_ears: bool = True
    # proposte di miglioria dal diario degli errori: "settimana" (al massimo un riepilogo a settimana, 3 proposte),
    # "giorno", oppure "mai" (resta solo "hai proposte?")
    proposals: str = "settimana"


@dataclass
class MemoryConfig:
    semantic: bool = True  # ricerca per significato (EmbeddingGemma 2 sul processore, niente VRAM); False = solo parole
    # a conversazione finita il custode rilegge gli scambi e aggiorna da solo i ricordi su di te
    keeper: bool = True
    keeper_idle_s: float = 180.0  # secondi di silenzio dopo i quali la conversazione si considera finita
    # chi fa il custode: auto = come il cervello principale (Claude Haiku, o il cloud gratuito), oppure claude o cloud
    keeper_brain: str = "auto"


@dataclass
class IdentityConfig:
    # riconoscere chi parla dalla voce: si accende da solo quando l'utente registra la sua ("impara la mia voce")
    enabled: bool = True
    threshold: float = 0.40  # quanto una frase deve somigliare all'impronta per dire "è lui" (più alto = più severo)
    unlock_minutes: float = 30.0  # dopo la parola d'ordine, per quanto vale l'identità se quella voce tace


@dataclass
class AppsConfig:
    # WhatsApp nascosto (un WhatsApp Web invisibile collegato al telefono): "su richiesta" si accende quando serve e
    # si spegne dopo 15 minuti senza usarlo (~300-400 MB di RAM mentre è acceso), "sempre" resta acceso (serve per
    # gli avvisi), "spento" mai
    whatsapp: str = "su richiesta"
    # con WhatsApp acceso: Jarvis dice chi ti ha scritto (mai il testo ad alta voce, lo legge solo se glielo chiedi)
    whatsapp_avvisi: bool = False


@dataclass
class MediaConfig:
    """Guardare video e ascoltare audio (jarvis/media): file del PC, YouTube e altri siti, video e vocali WhatsApp."""
    frames: int = 20  # quanti fotogrammi al massimo manda al cervello (i cambi di scena): ognuno costa ~420 token
    frame_px: int = 768  # lato lungo dei fotogrammi in pixel: più grandi = scritte più leggibili ma più token
    max_minutes: float = 60.0  # quanti minuti guarda al massimo per volta (il resto si chiede con inizio)
    transcript_chars: int = 30000  # quanti caratteri di trascrizione al massimo (~8.000 token)
    # scaricare video e audio dai siti (YouTube e altri, con yt-dlp) per guardarli: False = solo file del PC e WhatsApp
    web: bool = True
    # modello locale per musica, suoni e azioni, caricato solo quando serve al posto della voce (la scheda video non
    # basta per tutti e due): "spento" finché non lo scegli (audio-flamingo-next ha vinto la gara di misura)
    omni: str = "spento"


@dataclass
class ScheduleConfig:
    """Programmi: ciò che Jarvis fa da solo a un'ora, in un giorno o a ripetizione (jarvis/scheduler.py)."""
    # programmi accesi: False = non ne parte nessuno (restano salvati e ripartono quando li riaccendi)
    enabled: bool = True
    # Jarvis spento all'ora giusta: un programma ripetuto recupera l'ultima volta saltata solo entro questi minuti
    # (quelli di una volta partono sempre, in ritardo)
    catch_up_minutes: float = 120.0
    # un promemoria o una richiesta programmata riaccende Jarvis dal riposo per dirlo (ricarica la voce, ~4 GB della
    # scheda video: con un gioco aperto può pesare); False = aspetta che lo riaccendi tu
    wake_from_rest: bool = True


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    hands: HandsConfig = field(default_factory=HandsConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    apps: AppsConfig = field(default_factory=AppsConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)


# Impostazioni lette ogni volta che servono: cambiarle vale subito. Le altre valgono dal riavvio
# successivo (Jarvis lo dice e può riavviarsi da solo). Se ne aggiungi una qui, verifica davvero che
# il codice la rilegga ogni volta: dire "fatto" quando non lo è sarebbe una bugia.
LIVE_SETTINGS = {
    "speech.tts_speed", "brain.provider", "brain.worker_claude_model", "brain.worker_claude_effort",
    "ui.wake_word", "audio.barge_in", "audio.duck_gain", "audio.barge_in_confirm_ms",
    "audio.vad_threshold", "audio.vad_threshold_while_speaking", "audio.min_speech_ms", "audio.min_silence_ms",
    "audio.max_silence_ms", "audio.turn_threshold", "audio.pre_roll_ms", "system.autostart", "system.rest_keeps_ears",
    "audio.echo_reference",  # la pipeline la rilegge a ogni blocco di 10 ms
    "system.proposals",  # il controllo del riepilogo la rilegge a ogni giro
    "ui.theme", "ui.accent", "ui.text_scale", "ui.effects",  # la finestra le riceve e si ridisegna subito
    "memory.keeper", "memory.keeper_idle_s", "memory.keeper_brain",  # il custode le rilegge a ogni giro
    "identity.threshold", "identity.unlock_minutes",  # rilette a ogni frase
    "apps.whatsapp", "apps.whatsapp_avvisi",  # rilette a ogni richiesta, a ogni messaggio e ogni mezzo minuto
    # OpenCode: rilette a ogni turno, a ogni compito e a ogni giro di manutenzione (jarvis/opencode_kit.py)
    "brain.opencode", "brain.opencode_model", "brain.opencode_worker_model", "brain.opencode_privacy",
    "brain.opencode_exam_hours", "brain.opencode_workers",
}
LIVE_SECTIONS = {"hands",  # i sotto-agenti nascono a ogni compito con le impostazioni del momento
                 "media",  # guarda_video le rilegge a ogni video
                 "schedule"}  # il programmatore le rilegge a ogni giro e a ogni programma che parte


def is_live(dotted: str) -> bool:
    return dotted in LIVE_SETTINGS or dotted.split(".")[0] in LIVE_SECTIONS


def _comments(cls: type) -> dict[str, str]:
    """Il commento di ogni campo, preso dal codice: un'unica fonte per chi legge il codice e per Jarvis."""
    out: dict[str, str] = {}
    above: list[str] = []
    for line in inspect.getsource(cls).splitlines()[1:]:
        s = line.strip()
        if s.startswith("#"):
            above.append(s.lstrip("# ").strip())
            continue
        m = re.match(r"(\w+)\s*:[^=]+=.*?(?:#\s*(.*))?$", s)
        if m:
            out[m.group(1)] = " ".join([*above, m.group(2) or ""]).strip()
        above = []
    return out


def settings_catalog(cfg: Config) -> list[dict[str, Any]]:
    """Tutte le impostazioni: chiave, valore attuale, descrizione, se vale subito."""
    rows = []
    for section in dataclasses.fields(cfg):
        obj = getattr(cfg, section.name)
        notes = _comments(type(obj))
        for f in dataclasses.fields(obj):
            key = f"{section.name}.{f.name}"
            rows.append({"chiave": key, "valore": getattr(obj, f.name), "descrizione": notes.get(f.name, ""),
                         "subito": is_live(key), "scelte": sorted(CHOICES[key]) if key in CHOICES else None})
    return rows


def _check_type(value: Any, hint: Any, where: str) -> Any:
    """Controlla (e normalizza dove ha senso) il tipo di un valore letto dallo YAML."""
    origin = getattr(hint, "__origin__", None)
    args = getattr(hint, "__args__", ())
    if hint is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if hint in (int, float, str, bool):
        if isinstance(value, bool) != (hint is bool) or not isinstance(value, hint):
            raise ConfigError(f"{where}: atteso {hint.__name__}, trovato {value!r}")
        return value
    if origin is list:
        if not isinstance(value, list):
            raise ConfigError(f"{where}: attesa una lista, trovato {value!r}")
        return [_check_type(v, args[0], f"{where}[]") for v in value]
    if args and type(None) in args:  # Optional[X]
        if value is None:
            return None
        inner = next(a for a in args if a is not type(None))
        return _check_type(value, inner, where)
    return value


def _apply(obj: Any, data: dict[str, Any], where: str) -> None:
    hints = get_type_hints(type(obj))
    known = {f.name for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(f"{where}.{key}: impostazione sconosciuta (valide: {', '.join(sorted(known))})")
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{where}.{key}: attesa una sezione, trovato {value!r}")
            _apply(current, value, f"{where}.{key}")
        else:
            checked = _check_type(value, hints[key], f"{where}.{key}")
            allowed = CHOICES.get(f"{where}.{key}".removeprefix("config."))
            if allowed is not None and checked not in allowed:
                raise ConfigError(f"{where}.{key}: {checked!r} non valido (ammessi: {', '.join(sorted(allowed))})")
            setattr(obj, key, checked)


def _diff(obj: Any, default: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        a, b = getattr(obj, f.name), getattr(default, f.name)
        if dataclasses.is_dataclass(a):
            sub = _diff(a, b)
            if sub:
                out[f.name] = sub
        elif a != b:
            out[f.name] = a
    return out


class ConfigStore:
    """Configurazione condivisa, con salvataggio delle sole differenze dai predefiniti."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.DATA_DIR / "config.yaml")
        self._lock = threading.Lock()
        self.config = Config()
        if self.path.exists():
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ConfigError(f"{self.path}: il file deve contenere delle sezioni")
            _apply(self.config, raw, "config")

    def set(self, dotted: str, value: Any) -> None:
        """Cambia un'impostazione (es. "speech.tts_speed") e la salva."""
        section, _, key = dotted.partition(".")
        if not key:
            raise ConfigError(f"{dotted}: usa la forma sezione.chiave")
        with self._lock:
            _apply(self.config, {section: {key: value}}, "config")
            self.save()

    def get(self, dotted: str) -> Any:
        obj: Any = self.config
        for part in dotted.split("."):
            if not hasattr(obj, part):
                raise ConfigError(f"{dotted}: impostazione sconosciuta")
            obj = getattr(obj, part)
        return obj

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        diff = _diff(self.config, Config())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(diff, allow_unicode=True, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

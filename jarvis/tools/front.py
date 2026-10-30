"""Gli strumenti del cervello frontale di Jarvis (quelli che usa direttamente mentre parla)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..brain.tools import ToolRegistry, schema
from ..config import ConfigError, ConfigStore, is_live, settings_catalog
from ..core.bus import Bus
from ..memory.store import WIDE, Memory
from ..scheduler import ACTIONS, REPEATS, rule_said
from ..tasks.manager import TaskManager
from ..ui import look
from . import system


@dataclass
class Services:
    bus: Bus
    config: ConfigStore
    memory: Memory
    tasks: TaskManager | None = None
    voice_fx: Any = None  # VoiceFx, per regolare l'effetto a voce
    restart: Callable[[], str] | None = None  # riavvio di Jarvis tramite il guardiano
    shutdown: Callable[[], str] | None = None  # spegnimento voluto (anche il guardiano si ferma)
    rest: Callable[[], str] | None = None  # riposo: libera la scheda video, si riaccende col nome
    voices: Any = None  # VoiceAdmin: imparare voci e concedere permessi (jarvis/identity/admin.py)
    diary: Any = None  # Diary: errori ripetuti da proporre come migliorie (jarvis/improve/diary.py)
    apps: Any = None  # Apps: le abilità per WhatsApp, Gmail, Steam e giochi (jarvis/apps/base.py)
    media: Any = None  # MediaWatcher: guardare video e ascoltare audio (jarvis/media/service.py)
    free_models: Any = None  # OpenCodeKit: i modelli gratis di OpenCode quando Claude è esaurito (jarvis/opencode_kit.py)
    schedule: Any = None  # Scheduler: ciò che Jarvis fa da solo a un'ora o a ripetizione (jarvis/scheduler.py)
    # chi parla adesso è l'utente? (i programmi degli ospiti: solo promemoria, e vedono solo i loro)
    owner_speaking: Callable[[], bool] = lambda: True
    # effetti sul PC di certe impostazioni (es. avvio automatico): ricevono il valore, dicono cosa hanno fatto
    appliers: dict[str, Callable[[Any], str]] = field(default_factory=dict)


# Il permesso che serve per ogni strumento (jarvis/identity/guard.py). Base: chiunque, anche un ospite.
# Uno strumento nuovo va messo qui o in BASE_TOOLS: un test controlla che nessuno resti senza decisione.
TOOL_GROUPS = {
    "apri": "pc", "appunti": "pc", "finestra_attiva": "pc", "blocca_schermo": "pc",
    "usa_app": "pc",  # in più ogni app chiede il suo permesso: messaggi e posta solo l'utente (jarvis/apps/base.py)
    "guarda_video": "pc",  # i video di WhatsApp chiedono in più il permesso dei messaggi (jarvis/media/service.py)
    "ricorda": "memoria", "cerca_memoria": "memoria", "dimentica": "memoria",
    "impostazioni": "sistema", "impostazione": "sistema", "riavvia": "sistema", "riposo": "sistema",
    "spegniti": "sistema", "migliora_jarvis": "sistema", "proposte": "sistema", "rispondi_proposta": "sistema",
    "modelli_gratis": "sistema",
    "avvia_compito": "compiti", "messaggio_compito": "compiti", "annulla_compito": "compiti",
    "conferma_compito": "compiti", "stato_compiti": "compiti",
    "impara_voce": "voci", "voci_conosciute": "voci", "permessi_voce": "voci", "dimentica_voce": "voci",
}
BASE_TOOLS = {"nessuna_risposta", "mostra", "volume", "musica",
              # anche gli ospiti (promemoria): il programmatore stesso tiene fuori richieste, compiti e i programmi
              # dell'utente (jarvis/scheduler.py)
              "programma", "programmi", "togli_programma"}


SETTABLE = {
    "velocita_voce": ("speech.tts_speed", float),
    "risposta_senza_nome": ("ui.wake_word", lambda v: not _bool(v)),
    "interrompibile": ("audio.barge_in", lambda v: _bool(v)),
    "cervello": ("brain.provider", lambda v: _provider(v)),
    "grafica": ("ui.theme", lambda v: _theme(v)),
    "colore": ("ui.accent", lambda v: look.color(v)),
    "grandezza_testo": ("ui.text_scale", lambda v: _scale(v)),
    "effetti": ("ui.effects", lambda v: _effects(v)),
    "modello_claude": ("brain.claude_model", str),
    "come_chiamarmi": ("persona.address_as", str),
    "dare_del_lei": ("persona.formal", lambda v: _bool(v)),
    "conferme_azioni_delicate": ("safety.confirm_destructive", lambda v: _bool(v)),
    "whatsapp": ("apps.whatsapp", lambda v: _whatsapp(v)),
    "avvisi_whatsapp": ("apps.whatsapp_avvisi", lambda v: _bool(v)),
    "fotogrammi_video": ("media.frames", lambda v: _frames(v)),
    "modello_suoni": ("media.omni", lambda v: _omni(v)),
    "opencode": ("brain.opencode", lambda v: _bool(v)),
    "modello_gratis": ("brain.opencode_model", lambda v: _model(v)),
    "privacy_modelli_gratis": ("brain.opencode_privacy", lambda v: _privacy(v)),
    "compiti_su_opencode": ("brain.opencode_workers", lambda v: _bool(v)),
    "programmi_attivi": ("schedule.enabled", lambda v: _bool(v)),
    "programmi_dal_riposo": ("schedule.wake_from_rest", lambda v: _bool(v)),
    "voce_di_jarvis": ("speech.tts_voice", lambda v: _voice(v)),
}


_PROVIDERS = {"claude": "claude", "opus": "claude", "anthropic": "claude",
              "opencode": "opencode", "open code": "opencode", "zen": "opencode", "opencode zen": "opencode",
              "cloud": "ollama-cloud", "ollama": "ollama-cloud", "ollama-cloud": "ollama-cloud", "gpt-oss": "ollama-cloud",
              "locale": "local", "local": "local", "bonsai": "local", "offline": "local"}

_PRIVACY = {"tutti": "tutti", "tutte": "tutti", "qualsiasi": "tutti", "nessun limite": "tutti", "tutti i modelli": "tutti",
            "senza addestramento": "senza addestramento", "no addestramento": "senza addestramento",
            "niente addestramento": "senza addestramento", "privati": "senza addestramento",
            "solo privati": "senza addestramento", "riservati": "senza addestramento", "privacy": "senza addestramento"}


def _privacy(v: Any) -> str:
    key = " ".join(str(v).strip().lower().split())
    if key not in _PRIVACY:
        raise ValueError(f"privacy dei modelli gratis sconosciuta '{v}' (tutti, oppure senza addestramento)")
    return _PRIVACY[key]


def _model(v: Any) -> str:
    """Un nome di modello detto a voce: "automatico" -> auto, "GLM 5.3 flash free" -> glm-5.3-flash-free."""
    key = " ".join(str(v).strip().lower().split())
    if key in ("auto", "automatico", "automatica", "il migliore", "migliore", "da solo", "predefinito"):
        return "auto"
    return key.removeprefix("opencode/").replace(" ", "-")


# "film" di proposito no: vale per tutte e due (Mark III viene dal film, Cinema ha i sottotitoli da film); meglio
# un errore che fa chiedere a Jarvis che una grafica indovinata male
_THEMES = {"mark3": "mark3", "mark": "mark3", "markiii": "mark3", "mark-3": "mark3", "strumento": "mark3",
           "anello": "mark3", "cinema": "cinema", "linea": "cinema", "sottotitoli": "cinema", "pellicola": "cinema"}
_EFFECTS = {"pieni": "pieni", "completi": "pieni", "tutti": "pieni", "spettacolari": "pieni", "massimi": "pieni",
            "sì": "pieni", "si": "pieni", "on": "pieni", "sobri": "sobri", "calmi": "sobri", "pochi": "sobri",
            "leggeri": "sobri", "minimi": "sobri", "ridotti": "sobri", "meno": "sobri", "no": "sobri", "off": "sobri"}


def _theme(v: Any) -> str:
    key = str(v).strip().lower().replace(" ", "")
    if key not in _THEMES:
        raise ValueError(f"grafica sconosciuta '{v}' (mark3 o cinema)")
    return _THEMES[key]


def _effects(v: Any) -> str:
    key = str(v).strip().lower()
    if key not in _EFFECTS:
        raise ValueError(f"effetti sconosciuti '{v}' (pieni o sobri)")
    return _EFFECTS[key]


def _scale(v: Any) -> float:
    """"1,2" o "120%" -> 1.2. Fuori misura si rifiuta: salvarlo e mostrare altro sarebbe una bugia."""
    t = str(v).strip().replace(",", ".")
    x = float(t.removesuffix("%").strip()) / (100 if t.endswith("%") else 1)
    if not 0.7 <= x <= 1.8:
        raise ValueError(f"grandezza del testo {x:g} fuori misura: va da 0,7 a 1,8 (1 = normale)")
    return x


def _provider(v: Any) -> str:
    key = str(v).strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(f"cervello sconosciuto '{v}' (claude, opencode, cloud, locale)")
    return _PROVIDERS[key]


_REFERENCES = {"system": "system", "sistema": "system", "tutto": "system", "tutto ciò che suona": "system",
               "tutto quello che suona": "system", "video": "system", "pc": "system",
               "jarvis": "jarvis", "solo jarvis": "jarvis", "la sua voce": "jarvis", "solo la sua voce": "jarvis",
               "la tua voce": "jarvis", "solo la tua voce": "jarvis"}


def _reference(v: Any) -> str:
    key = " ".join(str(v).strip().lower().split())
    if key not in _REFERENCES:
        raise ValueError(f"riferimento dell'eco sconosciuto '{v}' (system = tutto ciò che suona, jarvis = solo Jarvis)")
    return _REFERENCES[key]


_PROPOSALS = {"mai": "mai", "nessuna": "mai", "no": "mai", "off": "mai", "spente": "mai", "mai più": "mai",
              "settimana": "settimana", "settimanale": "settimana", "ogni settimana": "settimana",
              "una volta a settimana": "settimana", "giorno": "giorno", "giornaliere": "giorno",
              "ogni giorno": "giorno", "una volta al giorno": "giorno", "quotidiane": "giorno"}


def _proposals(v: Any) -> str:
    key = " ".join(str(v).strip().lower().split())
    if key not in _PROPOSALS:
        raise ValueError(f"frequenza delle proposte sconosciuta '{v}' (mai, settimana o giorno)")
    return _PROPOSALS[key]


_WHATSAPP = {"su richiesta": "su richiesta", "quando serve": "su richiesta", "a richiesta": "su richiesta",
             "normale": "su richiesta", "auto": "su richiesta", "sempre": "sempre", "sempre acceso": "sempre",
             "acceso": "sempre", "on": "sempre", "spento": "spento", "mai": "spento", "off": "spento",
             "disattivato": "spento"}


def _whatsapp(v: Any) -> str:
    key = " ".join(str(v).strip().lower().split())
    if key not in _WHATSAPP:
        raise ValueError(f"modalità di WhatsApp sconosciuta '{v}' (su richiesta, sempre o spento)")
    return _WHATSAPP[key]


_OMNI = {"spento": "spento", "nessuno": "spento", "no": "spento", "off": "spento", "mai": "spento",
         "audio flamingo": "audio-flamingo-next", "flamingo": "audio-flamingo-next", "nvidia": "audio-flamingo-next",
         "audio-flamingo-next": "audio-flamingo-next", "audio flamingo next": "audio-flamingo-next",
         "qwen": "qwen2.5-omni-7b", "qwen omni": "qwen2.5-omni-7b", "qwen2.5-omni-7b": "qwen2.5-omni-7b",
         "qwen 2.5 omni": "qwen2.5-omni-7b", "lfm": "lfm3-omni-2b", "lfm3": "lfm3-omni-2b", "liquid": "lfm3-omni-2b",
         "lfm3-omni-2b": "lfm3-omni-2b", "lfm3 omni": "lfm3-omni-2b"}


def _omni(v: Any) -> str:
    key = " ".join(str(v).strip().lower().split())
    if key not in _OMNI:
        raise ValueError(f"modello per musica e suoni sconosciuto '{v}' (spento, audio flamingo, qwen, lfm)")
    return _OMNI[key]


def _ranged(name: str, lo: float, hi: float, kind: type = int):
    """Un numero dentro una misura: fuori si rifiuta (salvarlo e poi usarne un altro sarebbe una bugia)."""
    def conv(v: Any):
        x = kind(float(str(v).strip().replace(",", ".")))
        if not lo <= x <= hi:
            raise ValueError(f"{name} {x:g} fuori misura: va da {lo:g} a {hi:g}")
        return x
    return conv


_frames = _ranged("fotogrammi", 1, 50)

_VOICES = {"film": "jarvis", "del film": "jarvis", "quella del film": "jarvis", "jarvis": "jarvis",
           "base": "base-it", "di base": "base-it", "italiana": "base-it", "italiano": "base-it", "fasol": "base-it",
           "riccardo": "base-it", "base-it": "base-it", "inglese": "base-en", "english": "base-en", "evers": "base-en",
           "simon": "base-en", "base-en": "base-en"}


def _voice(v: Any) -> str:
    """La voce detta a voce: un nome che esiste davvero (una voce sbagliata farebbe cadere Jarvis all'avvio)."""
    from ..speech import engine

    key = " ".join(str(v).strip().lower().split())
    name = _VOICES.get(key, key)
    if engine.voice_dir(name) is None:
        raise ValueError(f"voce sconosciuta '{v}' (ci sono: {', '.join(engine.available_voices())})")
    return name

NORMALIZE = {"ui.accent": look.color, "ui.theme": _theme, "ui.effects": _effects, "ui.text_scale": _scale,
             "brain.provider": _provider, "audio.echo_reference": _reference, "system.proposals": _proposals,
             "apps.whatsapp": _whatsapp, "media.omni": _omni, "media.frames": _frames,
             "media.frame_px": _ranged("lato dei fotogrammi", 256, 1568),
             "media.max_minutes": _ranged("minuti", 1, 240, float),
             "media.transcript_chars": _ranged("caratteri", 2000, 200_000),
             "brain.opencode_privacy": _privacy, "brain.opencode_model": _model,
             "brain.opencode_worker_model": _model,
             "schedule.catch_up_minutes": _ranged("minuti di recupero", 0, 1440, float),
             "speech.tts_voice": _voice}


def _parse_value(raw: Any, current: Any) -> Any:
    """Il valore detto a voce, nel tipo dell'impostazione."""
    v = str(raw).strip()
    if isinstance(current, bool):
        return _bool(v)
    if isinstance(current, int):
        return int(float(v.replace(",", ".")))
    if isinstance(current, float):
        return float(v.replace(",", "."))
    if isinstance(current, list):
        return [x.strip() for x in v.split(",") if x.strip()]
    if v.lower() in ("nessuno", "nessuna", "none", "predefinito", "predefinita"):
        return None
    return v


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "sì", "si", "vero", "on", "attivo")


def build_front_tools(s: Services) -> ToolRegistry:
    reg = ToolRegistry()

    # ---------------------------------------------------------- conversazione e schermo

    @reg.register("nessuna_risposta", "Usalo quando la frase non era rivolta a te o quando ti hanno solo zittito. "
                  "Dopo non scrivere NIENTE, nemmeno per spiegare che stai zitto: qualunque testo verrebbe "
                  "pronunciato.", read_only=True)
    def nessuna_risposta(_args):
        return "fatto: turno chiuso senza parole (non aggiungere testo)"

    @reg.register("mostra", "Mostra qualcosa a schermo nella finestra di Jarvis: codice, link, tabelle, elenchi, testi "
                  "lunghi. Il contenuto è in markdown. A voce di' solo l'essenziale.",
                  schema({"titolo": {"type": "string"}, "contenuto": {"type": "string"}}, ["titolo", "contenuto"]))
    def mostra(args):
        s.bus.emit("show", title=args["titolo"], markdown=args["contenuto"])
        return "mostrato a schermo"

    # ---------------------------------------------------------- PC

    @reg.register("apri", "Apre un'app installata (per nome), un sito (indirizzo o dominio), un file, una cartella "
                  "(anche 'download', 'desktop', 'documenti').",
                  schema({"cosa": {"type": "string"}}, ["cosa"]))
    def apri(args):
        return system.open_target(args["cosa"])

    @reg.register("volume", "Legge o regola il volume generale del PC.",
                  schema({"azione": {"type": "string", "enum": ["leggi", "imposta", "alza", "abbassa", "muto", "riattiva"]},
                          "livello": {"type": "integer", "description": "0-100 per 'imposta', passo per alza/abbassa"}},
                         ["azione"]))
    def volume(args):
        return system.volume(args["azione"], args.get("livello"))

    @reg.register("musica", "Controlla la riproduzione multimediale in corso (Spotify, YouTube, ...).",
                  schema({"azione": {"type": "string", "enum": ["play_pausa", "successivo", "precedente", "stop"]}},
                         ["azione"]))
    def musica(args):
        return system.media(args["azione"])

    @reg.register("appunti", "Legge il testo copiato negli appunti oppure ci scrive un testo.",
                  schema({"azione": {"type": "string", "enum": ["leggi", "scrivi"]}, "testo": {"type": "string"}},
                         ["azione"]))
    def appunti(args):
        if args["azione"] == "leggi":
            text = system.clipboard_read()
            return text[:4000] if text else "(gli appunti sono vuoti o non contengono testo)"
        if not str(args.get("testo") or ""):  # prima svuotava gli appunti in silenzio
            return "Errore: serve il testo da copiare"
        system.clipboard_write(str(args["testo"]))
        return "testo copiato negli appunti"

    @reg.register("finestra_attiva", "Dice quale finestra/programma l'utente ha in primo piano.", read_only=True)
    def finestra_attiva(_args):
        w = system.active_window()
        return f"{w['title']} ({w['process']})"

    @reg.register("blocca_schermo", "Blocca la sessione di Windows.")
    def blocca(_args):
        return system.lock_screen()

    if s.apps is not None:
        apps = s.apps

        @reg.register("usa_app", "Usa le app del PC con gesti pronti, in background (non serve che siano aperte né in "
                      "primo piano). App e azioni: " + apps.catalog() + ". [x] = facoltativo; gli argomenti vanno in "
                      "parametri con quei nomi. Tutti i dettagli di un'app: azione aiuto. Se la risposta è DA "
                      "CONFERMARE, di' all'utente cosa farai e richiama con conferma=codice solo dopo il suo sì. Ciò "
                      "che qui manca: avvia_compito.",
                      schema({"app": {"type": "string", "enum": list(apps.apps)},
                              "azione": {"type": "string"},
                              "parametri": {"type": "object", "description": 'es. {"gioco": "minecraft"}'},
                              "conferma": {"type": "string", "description": "il codice ricevuto con DA CONFERMARE"}},
                             ["app", "azione"]))
        async def usa_app(args):
            params = args.get("parametri") or {}
            if isinstance(params, str):  # qualche modello manda l'oggetto come testo JSON
                try:
                    params = json.loads(params) if params.strip() else {}
                except ValueError:
                    params = None
            if not isinstance(params, dict):
                return 'Errore: parametri va scritto come oggetto, es. {"gioco": "minecraft"}'
            return await apps.use(args["app"], args["azione"], params, args.get("conferma"))

    if s.media is not None:
        media = s.media

        @reg.register("guarda_video", "Guarda un video o ascolta un audio: ricevi i fotogrammi dei cambi di scena "
                      "(immagini, con l'ora) e il parlato trascritto con l'ora. Da un file del PC (percorso, oppure un "
                      "nome come 'vacanze' o 'ultimo video': cerca in Desktop, Download, Video, Musica), da un "
                      "indirizzo (YouTube e altri siti) o l'ultimo video/vocale/audio di una chat WhatsApp (chat). "
                      "Ci vogliono da pochi secondi a qualche minuto: prima di' una frase breve. Video lunghi: inizio "
                      "e fine (es. 12:30). suoni=true solo se servono musica, rumori o azioni che le parole non "
                      "dicono: carica un modello locale e la tua voce tace finché non ha finito.",
                      schema({"sorgente": {"type": "string", "description": "percorso, nome del file o indirizzo"},
                              "chat": {"type": "string", "description": "chat WhatsApp: il suo ultimo video o vocale"},
                              "quale": {"type": "string", "description": "'penultimo' per quello prima (WhatsApp)"},
                              "inizio": {"type": "string", "description": "da dove, es. 1:30"},
                              "fine": {"type": "string", "description": "fino a dove, es. 4:00"},
                              "solo_audio": {"type": "boolean", "description": "solo l'audio (più veloce)"},
                              "suoni": {"type": "boolean", "description": "musica e suoni col modello locale"},
                              "domanda": {"type": "string", "description": "cosa chiedere al modello dei suoni"}}),
                      read_only=True)
        async def guarda_video(args):
            return await media.look(args)

    # ---------------------------------------------------------- memoria

    @reg.register("ricorda", "Salva in memoria un fatto stabile sull'utente (preferenze, persone, progetti, abitudini, "
                  "decisioni). Scrivilo come frase completa in terza persona.",
                  schema({"fatto": {"type": "string"},
                          "categoria": {"type": "string", "enum": ["preferenze", "persone", "progetti", "pc", "abitudini",
                                                                   "salute", "lavoro", "generale"]},
                          "importanza": {"type": "integer", "description": "1 normale, 2 importante, 3 fondamentale"}},
                         ["fatto"]))
    def ricorda(args):
        importance = max(1, min(3, int(args.get("importanza") or 1)))  # 1-3, anche se arriva "5" o "0"
        _fid, new = s.memory.remember(args["fatto"], args.get("categoria") or "generale", importance)
        return "memorizzato" if new else "aggiornato un ricordo già esistente"

    @reg.register("cerca_memoria", "Cerca nei ricordi e nelle conversazioni passate.",
                  schema({"domanda": {"type": "string"}}, ["domanda"]), read_only=True)
    def cerca_memoria(args):
        facts = s.memory.search(args["domanda"], k=6, min_relevance=WIDE, touch=True)
        turns = s.memory.search_turns(args["domanda"], k=4)
        out = [f"- {f.text}" for f in facts]
        import time as _t

        out += [f"- [{_t.strftime('%d/%m %H:%M', _t.localtime(ts))}] {role}: {text[:200]}" for ts, role, text in turns]
        return "\n".join(out) if out else "nessun ricordo pertinente"

    @reg.register("dimentica", "Cancella dalla memoria un fatto che l'utente vuole far dimenticare.",
                  schema({"cosa": {"type": "string"}}, ["cosa"]))
    def dimentica(args):
        res = s.memory.forget(args["cosa"])
        if res.removed:
            return "dimenticato: " + "; ".join(res.removed)
        if res.candidates:  # ambiguo: niente cancellato, si chiede quale (prima cancellava quello sbagliato)
            return ("Più ricordi simili, non ho cancellato niente: chiedi all'utente quale e poi richiama dimentica "
                    "con il testo esatto. " + " | ".join(f.text for f in res.candidates))
        return "non ho trovato niente del genere in memoria"

    # ---------------------------------------------------------- impostazioni

    @reg.register("impostazioni", "Elenca le impostazioni di Jarvis con il valore attuale, a cosa servono e se "
                  "valgono subito o dal riavvio. Usalo prima di cambiarne una che non conosci.",
                  schema({"filtro": {"type": "string", "description": "parola da cercare (facoltativa)"}}, []),
                  read_only=True)
    def impostazioni(args):
        word = str(args.get("filtro") or "").lower()
        lines = []
        for r in settings_catalog(s.config.config):
            if word and word not in r["chiave"].lower() and word not in r["descrizione"].lower():
                continue
            choices = f" (scelte: {', '.join(r['scelte'])})" if r["scelte"] else ""
            when = "" if r["subito"] else " [vale dal riavvio]"
            lines.append(f"{r['chiave']} = {r['valore']!r}{choices}{when} — {r['descrizione']}")
        if not word:  # con un filtro solo le righe che corrispondono
            lines.append("Scorciatoie: " + ", ".join(f"{k} = {v[0]}" for k, v in SETTABLE.items())
                         + ", intensita_effetto_voce (0-1).")
        return "\n".join(lines) or f"nessuna impostazione corrisponde a '{word}'"

    @reg.register("impostazione", "Cambia un'impostazione di Jarvis su richiesta dell'utente: qualunque chiave "
                  "sezione.nome di impostazioni (anche system.autostart, l'avvio con Windows) oppure una scorciatoia: "
                  + ", ".join(SETTABLE) + ", intensita_effetto_voce (0-1).",
                  schema({"chiave": {"type": "string"}, "valore": {"type": "string"}}, ["chiave", "valore"]))
    def impostazione(args):
        key, val = str(args["chiave"]).strip(), args["valore"]
        if key == "intensita_effetto_voce":
            if s.voice_fx is None:
                return "Errore: effetto voce non disponibile"
            s.voice_fx.p.intensity = max(0.0, min(1.0, float(str(val).replace(",", "."))))
            return f"intensità dell'effetto voce: {s.voice_fx.p.intensity:.2f}"
        try:
            if key in SETTABLE:
                dotted, conv = SETTABLE[key]
                value = conv(str(val).replace(",", ".") if conv is float else val)
            else:
                dotted = key
                current = s.config.get(dotted)
                if dataclasses.is_dataclass(current):
                    return f"Errore: '{key}' è una sezione: indica sezione.nome (vedi impostazioni)"
                if dotted in NORMALIZE:  # anche con la chiave completa: "rosso" -> #ff4d4d, "linea" -> cinema
                    value = NORMALIZE[dotted](val)
                else:
                    value = _parse_value(val, current)
            previous = s.config.get(dotted)
            s.config.set(dotted, value)
        except (ConfigError, ValueError) as e:
            return f"Errore: {e}. Usa impostazioni per vedere le chiavi e i valori ammessi."
        done = ""
        if dotted in s.appliers:
            try:
                done = "; " + s.appliers[dotted](value)
            except Exception as e:  # il PC non ha accettato la modifica: si torna al valore di prima
                s.config.set(dotted, previous)
                return f"Errore: non sono riuscito ad applicarla ({e}); l'impostazione resta com'era"
        s.bus.emit("settings_changed", key=dotted)
        when = "vale subito" if is_live(dotted) else "vale dal prossimo riavvio (se l'utente vuole, usa riavvia)"
        return f"{dotted} = {value!r}: {when}{done}"

    if s.free_models is not None:
        kit = s.free_models

        @reg.register("modelli_gratis", "I modelli gratis di OpenCode che usi quando l'uso di Claude è esaurito: "
                      "classifica, esito dell'esame (italiano, strumenti, prima parola), cosa fanno dei dati. "
                      "azione esamina = rifà l'esame adesso (circa un minuto, in sottofondo). Per sceglierne uno o "
                      "escludere chi addestra sui dati: impostazione modello_gratis o privacy_modelli_gratis.",
                      schema({"azione": {"type": "string", "enum": ["elenco", "esamina"]}}, []), read_only=True)
        def modelli_gratis(args):
            if args.get("azione") == "esamina":
                return kit.start_exam()
            return kit.describe()

    if s.restart is not None:
        @reg.register("riavvia", "Riavvia Jarvis: serve per le impostazioni che valgono dal riavvio. Prima dillo "
                      "all'utente in una frase; ti riavvii appena hai finito di parlare.")
        def riavvia(_args):
            return s.restart()

    if s.rest is not None:
        @reg.register("riposo", "Mette Jarvis a riposo e libera tutta la scheda video (es. per giocare): i modelli "
                      "si scaricano, le orecchie restano accese e basta dire \"Jarvis\" per riaccenderlo (~8 s). "
                      "Prima dillo in una frase.")
        def riposo(_args):
            return s.rest()

    if s.shutdown is not None:
        @reg.register("spegniti", "Spegne Jarvis del tutto (anche il guardiano): solo se l'utente lo chiede "
                      "esplicitamente. Prima saluta in una frase; ti spegni appena hai finito di parlare.")
        def spegniti(_args):
            return s.shutdown()

    # ---------------------------------------------------------- voci (solo l'utente)

    if s.voices is not None:
        va = s.voices

        @reg.register("impara_voce", "Impara a riconoscere una voce: quella dell'utente stesso (nome 'la mia') o di "
                      "un'altra persona (amico, familiare). La persona legge alcune frasi che compaiono nella finestra; "
                      "finché non ha finito le sue frasi non ti arrivano.",
                      schema({"nome": {"type": "string", "description": "'la mia' oppure il nome della persona"},
                              "note": {"type": "string", "description": "chi è e come trattarla (facoltativo)"}},
                             ["nome"]))
        def impara_voce(args):
            return va.learn(str(args["nome"]), str(args.get("note") or ""))

        @reg.register("voci_conosciute", "Elenca le voci che riconosci e cosa può fare ciascuna.", read_only=True)
        def voci_conosciute(_args):
            return va.known()

        @reg.register("permessi_voce", "Cambia cosa può fare una persona conosciuta oltre alle cose di base: 'pc' "
                      "(aprire app e siti, appunti, schermo) e/o 'compiti' (sotto-agenti). Lista vuota = solo base. "
                      "Memoria, impostazioni e voci restano sempre solo dell'utente.",
                      schema({"nome": {"type": "string"}, "permessi": {"type": "array", "items": {"type": "string"}}},
                             ["nome", "permessi"]))
        def permessi_voce(args):
            perms = args["permessi"]
            return va.grant(str(args["nome"]), perms if isinstance(perms, list) else str(perms).split(","))

        @reg.register("dimentica_voce", "Dimentica la voce di una persona conosciuta (tornerà un ospite).",
                      schema({"nome": {"type": "string"}}, ["nome"]))
        def dimentica_voce(args):
            return va.forget(str(args["nome"]))

    # ---------------------------------------------------------- compiti

    if s.tasks is not None:
        tm = s.tasks

        @reg.register("avvia_compito", "Affida a un sotto-agente un lavoro lungo o a più passi (codice, ricerche "
                      "approfondite, file, browser, installazioni...). Lavora in background: tu intanto continui a "
                      "conversare. Scrivi un obiettivo completo e autosufficiente: il sotto-agente non sente la "
                      "conversazione.",
                      schema({"obiettivo": {"type": "string"},
                              "titolo": {"type": "string", "description": "3-6 parole"},
                              "cartella": {"type": "string", "description": "cartella del progetto, se pertinente"}},
                             ["obiettivo", "titolo"]))
        def avvia_compito(args):
            t = tm.spawn(args["obiettivo"], args.get("titolo"), args.get("cartella"))
            return f"compito #{t.id} avviato ({t.status})"

        @reg.register("migliora_jarvis", "Affida a un sotto-agente una modifica al TUO codice (funzione nuova, "
                      "difetto da correggere, comportamento diverso) quando un'impostazione non basta. Lavora su una "
                      "copia separata con i test; prima di applicare chiede conferma all'utente; vale dal riavvio.",
                      schema({"richiesta": {"type": "string", "description": "cosa cambiare, con tutti i dettagli"},
                              "titolo": {"type": "string", "description": "3-6 parole"}}, ["richiesta", "titolo"]))
        def migliora_jarvis(args):
            t = tm.spawn(args["richiesta"], args.get("titolo"), kind="miglioria")
            return f"miglioria #{t.id} avviata ({t.status}): ti chiederò conferma prima di applicarla"

        if s.diary is not None:
            from ..improve.diary import MIN_REPEATS, improve_request, listing

            diary = s.diary

            @reg.register("proposte", "Le cose che potresti migliorare di te: errori ripetuti almeno "
                          f"{MIN_REPEATS} volte nel tuo diario (cadute, strumenti falliti, correzioni dell'utente). "
                          "Per \"hai proposte?\". Numerate: i numeri servono a rispondi_proposta.", read_only=True)
            def proposte(_args):
                found = diary.candidates()
                diary.listed = [p.key for p in found]
                if not found:
                    return f"Nessuna proposta: nessun errore si è ripetuto almeno {MIN_REPEATS} volte."
                return listing(found)

            @reg.register("rispondi_proposta", "Risposta dell'utente a una proposta (numero come nell'ultimo elenco). "
                          "Sì: parte la miglioria (copia separata con i test, conferma prima di applicare). "
                          "No: non la riproporrai mai più.",
                          schema({"numero": {"type": "integer"}, "accetta": {"type": "boolean"}}, ["numero", "accetta"]))
            def rispondi_proposta(args):
                n = int(args["numero"])
                if not 1 <= n <= len(diary.listed):
                    return "Errore: numero sconosciuto. Rileggi le proposte con lo strumento proposte."
                key = diary.listed[n - 1]
                found = {p.key: p for p in diary.candidates(limit=50)}
                if key not in found:
                    return "Errore: questa proposta non c'è più (già decisa)."
                if not args["accetta"]:
                    diary.decide(key, "rifiutata")
                    return "Va bene: non la proporrò più."
                diary.decide(key, "accettata")
                t = tm.spawn(improve_request(found[key]), found[key].title[:60], kind="miglioria")
                return f"miglioria #{t.id} avviata ({t.status}): ti chiederò conferma prima di applicarla"

        @reg.register("messaggio_compito", "Inoltra a un compito in corso un'aggiunta o una correzione dell'utente.",
                      schema({"id": {"type": "integer"}, "messaggio": {"type": "string"}}, ["id", "messaggio"]))
        async def messaggio_compito(args):
            return await tm.message(int(args["id"]), args["messaggio"])

        @reg.register("annulla_compito", "Ferma un compito in corso.",
                      schema({"id": {"type": "integer"}}, ["id"]))
        async def annulla_compito(args):
            return await tm.cancel(int(args["id"]))

        @reg.register("conferma_compito", "Risponde a un compito che ha chiesto conferma per un'azione delicata.",
                      schema({"id": {"type": "integer"}, "approvata": {"type": "boolean"}, "nota": {"type": "string"}},
                             ["id", "approvata"]))
        def conferma_compito(args):
            return tm.answer(int(args["id"]), bool(args["approvata"]), args.get("nota", ""))

        @reg.register("stato_compiti", "Elenca i compiti attivi e quelli finiti di recente, con l'esito.", read_only=True)
        def stato_compiti(_args):
            rows = []
            for t in tm.tasks.values():
                line = t.summary()
                if t.result:
                    line += f" — esito: {t.result[:300]}"
                if t.error:
                    line += f" — errore: {t.error[:200]}"
                rows.append(line)
            return "\n".join(rows) if rows else "nessun compito"

    # ---------------------------------------------------------- programmi (a un'ora, a ripetizione)

    if s.schedule is not None:
        sch = s.schedule

        @reg.register("programma", "Programma qualcosa che farai da solo più avanti, una volta o a ripetizione. cosa: "
                      "avviso = un promemoria che dirai all'ora giusta (\"ricordami tra 20 minuti il forno\"); "
                      "richiesta = una richiesta che eseguirai all'ora giusta come se l'utente te la facesse in quel "
                      "momento, con tutti i tuoi strumenti (\"ogni mattina alle 8 il riassunto delle notizie\", "
                      "\"ogni lunedì controlla le offerte su X\"); compito = un sotto-agente parte da solo con "
                      "quell'obiettivo (lavori lunghi). testo: il promemoria, la richiesta o l'obiettivo, completo e "
                      "autosufficiente (all'ora giusta non ricorderai la conversazione). Quando: ripeti no (una volta: "
                      "ora e data, oppure tra_minuti), ogni_giorno (ora), giorni (giorni + ora), ogni_mese "
                      "(giorno_mese + ora), ogni (ogni_minuti, ed eventualmente ora/data di partenza), all_avvio (a "
                      "ogni tua accensione). Facoltativi fino (data di fine) e volte. Date come 2026-11-05, ore come "
                      "08:30, ora del contesto come riferimento. Conferma all'utente cosa hai programmato e quando.",
                      schema({"cosa": {"type": "string", "enum": list(ACTIONS)},
                              "testo": {"type": "string"},
                              "titolo": {"type": "string", "description": "3-6 parole"},
                              "ripeti": {"type": "string", "enum": list(REPEATS)},
                              "ora": {"type": "string", "description": "HH:MM"},
                              "data": {"type": "string", "description": "AAAA-MM-GG (una volta, o da quando partire)"},
                              "tra_minuti": {"type": "number", "description": "una volta, tra quanti minuti"},
                              "giorni": {"type": "array", "items": {"type": "string"},
                                         "description": "lun, mar, mer, gio, ven, sab, dom, feriali, weekend"},
                              "giorno_mese": {"type": "integer", "description": "1-31 (31 = ultimo del mese)"},
                              "ogni_minuti": {"type": "integer"},
                              "fino": {"type": "string", "description": "AAAA-MM-GG, ultimo giorno"},
                              "volte": {"type": "integer", "description": "quante volte al massimo"}},
                             ["cosa", "testo"]))
        def programma(args):
            try:
                p = sch.create(args, guest=not s.owner_speaking())
            except ValueError as e:
                return f"Errore: {e}"
            return f"programmato #{p.id} «{p.title}»: {p.action}, {rule_said(p, sch.clock())}"

        @reg.register("programmi", "Elenca i programmi (promemoria, richieste e compiti a un'ora o a ripetizione) con "
                      "la prossima volta. I numeri servono a togli_programma.", read_only=True)
        def programmi(_args):
            return sch.listing(guest=not s.owner_speaking())

        @reg.register("togli_programma", "Toglie un programma (numero come in programmi). Per cambiarne uno: toglilo e "
                      "rifallo con programma.", schema({"id": {"type": "integer"}}, ["id"]))
        def togli_programma(args):
            return sch.remove(int(args["id"]), guest=not s.owner_speaking())

    for t in reg:
        t.group = TOOL_GROUPS.get(t.name, "base")
    return reg

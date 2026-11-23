"""Le due lingue di Jarvis, italiano e inglese: ciò che dice senza passare dal cervello e ciò che capisce da solo.

Quasi tutto ciò che Jarvis dice lo scrive il cervello, nella lingua che gli chiede la persona
(jarvis/brain/persona.py). Qui stanno solo le frasi fisse (mentre lavora, quando cambia cervello, quando qualcosa va
storto), le frasi tenute pronte per rispondere subito e le parole che si riconoscono senza cervello (stop). La
lingua è `persona.language`: "it" o "en"; qualsiasi altro valore vale italiano.
"""

from __future__ import annotations

LANGS = ("it", "en")
TTS_LANGUAGE = {"it": "Italian", "en": "English"}  # come la chiama il modello della voce


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else "it"


def address(lang: str | None, address_as: str) -> str:
    """Come si rivolge all'utente: "signore" in inglese diventa "sir" (gli altri nomi restano come sono)."""
    if norm(lang) == "en" and address_as.strip().lower() in ("signore", "signor"):
        return "sir"
    return address_as


PHRASES = {
    "it": {
        "checking": "Controllo subito.",
        "on_it": "Me ne occupo io.",
        "online": "Sistemi in linea.",
        "formulation": "Ho avuto un problema nel formulare la risposta, {address}. Può ripetere?",
        "backup_down": "Temo che anche il sistema di riserva si sia fermato, {address}. Riprovi tra poco.",
        "cannot_think": "Temo di non riuscire a ragionare in questo momento, {address}. Riprovo tra poco.",
        "no_backup": "{why}, e non ho un cervello di riserva disponibile.",
        "switch": "{why}: {then}.",
        "then_backup": "passo al sistema di riserva",
        "then_local": "continuo con il cervello locale",
        "then_opencode": "continuo con un modello gratuito di OpenCode",
        "spent": "Ho esaurito {what} {when}",
        "for_now": "per ora",
        "what_opencode": "l'uso gratuito di OpenCode",
        "what_claude": "l'uso di Claude",
        "brain_down": "Il mio cervello principale non risponde",
        "no_access": "Claude non accetta più il mio accesso, va rifatto il login",
        "until_today": "fino alle {hhmm}",
        "until_tomorrow": "fino a domani alle {hhmm}",
        "until_day": "fino al {day} alle {hhmm}",
    },
    "en": {
        "checking": "Let me check.",
        "on_it": "I'll take care of it.",
        "online": "Systems online.",
        "formulation": "I had trouble putting the answer together, {address}. Could you say that again?",
        "backup_down": "I'm afraid the backup system has stopped as well, {address}. Please try again shortly.",
        "cannot_think": "I'm afraid I can't think straight at the moment, {address}. I'll try again shortly.",
        "no_backup": "{why}, and I have no backup brain available.",
        "switch": "{why}: {then}.",
        "then_backup": "switching to the backup system",
        "then_local": "carrying on with the local brain",
        "then_opencode": "carrying on with a free OpenCode model",
        "spent": "I've used up {what} {when}",
        "for_now": "for now",
        "what_opencode": "the free OpenCode allowance",
        "what_claude": "my Claude allowance",
        "brain_down": "My main brain isn't responding",
        "no_access": "Claude no longer accepts my sign-in, it needs to be redone",
        "until_today": "until {hhmm}",
        "until_tomorrow": "until {hhmm} tomorrow",
        "until_day": "until {hhmm} on the {day}",
    },
}


def phrase(lang: str | None, key: str, **values) -> str:
    return PHRASES[norm(lang)][key].format(**values)


# frasi brevi tenute pronte nella cache della voce: la prima risposta suona senza attesa
COMMON_PHRASES = {
    "it": ["Subito, signore.", "Certamente, signore.", "Certamente.", "Subito.", "Come desidera.", "Fatto.",
           "Un momento.", "Un attimo, signore.", "Me ne occupo io.", "Ricevuto.", "Va bene.", "D'accordo.",
           "Ecco fatto.", "Sì, signore.", "Bentornato, signore.", "Buongiorno, signore.", "Buonasera, signore.",
           "Ai suoi ordini.", "Al suo servizio, signore.", "Ci penso io.", "Controllo subito.", "Verifico subito."],
    "en": ["Right away, sir.", "Certainly, sir.", "Certainly.", "Right away.", "As you wish.", "Done.",
           "One moment.", "Just a moment, sir.", "I'll take care of it.", "Understood.", "Very well.", "Of course.",
           "There we are.", "Yes, sir.", "Welcome back, sir.", "Good morning, sir.", "Good evening, sir.",
           "At your service.", "At your service, sir.", "Leave it with me.", "Let me check.", "Checking now."],
}

# Le parole che zittiscono Jarvis valgono in entrambe le lingue: chi parla inglese a un Jarvis italiano (o il
# contrario) deve poterlo fermare lo stesso.
STOP_WORDS = {
    "stop", "basta", "zitto", "zitta", "fermo", "fermati", "silenzio", "taci", "aspetta", "smettila", "ok basta",
    "ok stop", "jarvis stop", "jarvis basta", "basta così", "stop jarvis", "ferma",
    "enough", "quiet", "be quiet", "shut up", "hold on", "wait", "that's enough", "thats enough", "silence",
    "stop it", "cancel", "never mind", "nevermind", "ok enough",
}
FILLERS = {"", "mh", "mm", "mmh", "eh", "ehm", "ah", "oh", "uh", "boh", "hm", "ehi", "um", "umm", "er", "erm", "hmm"}
POLITE = ("per favore", "grazie", "please", "thanks", "thank you")


def follow_language(store, code: str) -> str:
    """Cambiata la lingua: se la voce è una di quelle incluse (base-it, base-en) passa a quella della lingua nuova;
    una voce tua (per esempio quella del film) resta. Restituisce cosa è successo, da dire all'utente."""
    voice = store.config.speech.tts_voice
    if voice.startswith("base-") and voice != f"base-{code}":
        store.set("speech.tts_voice", f"base-{code}")
        return f"anche la voce passa a base-{code}"
    return "la voce resta quella scelta"

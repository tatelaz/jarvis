"""Le frasi di servizio in inglese che il cervello a volte scrive prima di usare uno strumento ("Let me check the
status."): tutto ciò che scrive viene pronunciato, e il 27/10 Jarvis ha detto "Let me check the status. Direi di sì".

Si trattiene l'inizio di un pezzo di risposta solo finché può ancora essere una di quelle frasi: una risposta
italiana si riconosce al primo pezzo e passa subito, la voce non aspetta. Quella trattenuta si scarta solo se subito
dopo parte uno strumento (il caso vero); altrimenti si dice com'è, così una frase inglese chiesta apposta ("come si
dice...?") non si perde.
"""

from __future__ import annotations

OPENERS = ("let me", "let's", "i'll", "i will", "i'm going to", "i am going to", "i need to", "i should",
           "now let me", "now i'll", "first, let me", "first let me", "checking", "looking up", "okay, let me",
           "ok, let me")


def _could_be(text: str) -> bool:
    t = " ".join(text.lower().split())
    return any(o.startswith(t) or t.startswith(o) for o in OPENERS)


class NarrationFilter:
    """feed() restituisce il testo da pronunciare (vuoto se lo trattiene), tool() scarta la frase trattenuta,
    flush() restituisce ciò che resta a fine risposta."""

    def __init__(self) -> None:
        self.held = ""
        self.screening = True  # all'inizio della risposta e dopo ogni strumento

    def feed(self, text: str) -> str:
        if not self.screening:
            return text
        self.held += text
        if not self.held.strip() or _could_be(self.held):
            return ""
        out, self.held, self.screening = self.held, "", False
        return out

    def tool(self) -> None:
        """Parte uno strumento: la frase di servizio trattenuta non si dice; il testo dopo si controlla di nuovo."""
        self.held = ""
        self.screening = True

    def flush(self) -> str:
        out, self.held = self.held, ""
        return out

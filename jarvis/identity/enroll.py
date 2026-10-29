"""Imparare una voce: la persona legge qualche frase (in tutto ~40 s per l'utente, ~25 s per gli altri).

Le frasi lette non vanno al cervello. Si scartano quelle troppo brevi, quelle dell'utente quando si registra
qualcun altro, e alla fine si controlla che i campioni si somiglino tra loro (una sola voce, poco rumore).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .guard import Voice
from .people import OWNER

SENTENCES = [
    "Buongiorno Jarvis, oggi ho voglia di fare qualcosa di nuovo e interessante.",
    "Il treno per Firenze partirà alle sette e quarantacinque dal binario tre.",
    "Sabato sera andiamo a mangiare una pizza con gli amici, ci vediamo in piazza.",
    "Mi ricordi di comprare il latte, il pane e due chili di mele rosse?",
    "Ventitré, quarantotto, centoquindici: segnati questi numeri sul blocco note.",
    "La musica che ascoltavo da ragazzo mi mette ancora di buon umore.",
    "Quando piove forte preferisco restare a casa a leggere un buon libro.",
    "Accendi la luce dello studio e abbassa un po' il volume della musica.",
]
MIN_SAMPLE_S = 1.2
MIN_CONSISTENCY = 0.45  # somiglianza media tra i campioni: sotto, voci miste o troppo rumore


@dataclass
class Enrollment:
    name: str
    role: str
    note: str = ""
    need_s: float = 0.0
    samples: list[np.ndarray] = field(default_factory=list)
    seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.need_s:
            self.need_s = 40.0 if self.role == OWNER else 25.0

    def add(self, voice: Voice, seconds: float) -> str:
        """"preso", "breve", "nessuna voce" o "è l'utente" (quando si registra un'altra persona)."""
        if voice.embedding is None or seconds < MIN_SAMPLE_S:
            return "breve"
        if self.role != OWNER and voice.person is not None and voice.person.owner:
            return "è l'utente"
        self.samples.append(voice.embedding)
        self.seconds += seconds
        return "preso"

    @property
    def done(self) -> bool:
        return self.seconds >= self.need_s and len(self.samples) >= 3

    @property
    def progress(self) -> int:
        return min(100, round(100 * self.seconds / self.need_s))

    def consistency(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        m = np.stack(self.samples)
        sims = m @ m.T
        n = len(self.samples)
        return float((sims.sum() - n) / (n * (n - 1)))

    def card(self) -> str:
        lines = [f"Leggi ad alta voce, una alla volta (servono circa {round(self.need_s)} secondi di voce):", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(SENTENCES, 1)]
        lines += ["", "Per annullare: «annulla»."]
        return "\n".join(lines)

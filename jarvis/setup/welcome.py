"""La presentazione a voce dopo il primo avvio: Jarvis si presenta e conduce lui il resto della configurazione.

Il primo avvio grafico (jarvis/setup) prepara il necessario per partire; tutto il resto si dice a voce: come si chiama
l'utente e come vuole essere chiamato, il tu o il Lei, la sua voce, le app da collegare, le abitudini da ricordare.
Lo stato è un file piccolo (`data/setup/presentazione.json`): quali passi sono fatti o saltati e se è finita.

Parte al primo avvio dopo quello grafico (`data/setup/fatto.json`) con un [EVENTO] per il cervello; se resta a metà,
alla riaccensione si chiede se continuare. "Dopo" la rimanda fino alla prossima accensione, "continuiamo la
configurazione" la riprende quando si vuole. La parola d'ordine non fa parte dei passi: si imposta dalla finestra,
mai a voce.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("jarvis.setup")

STEPS = ("nome", "registro", "voce", "app", "abitudini")
WHAT = {
    "nome": "come si chiama e come vuole che ti rivolga a lui (impostazioni persona.user_name e come_chiamarmi)",
    "registro": "se preferisce il tu o il Lei (impostazione dare_del_lei)",
    "voce": "imparare la sua voce, così lo distingui dagli ospiti (impara_voce con nome 'la mia'); la parola "
            "d'ordine invece si imposta dalla finestra (registro, voce e sicurezza): mai chiederla né dirla a voce",
    "app": "le app che vuole collegare: WhatsApp (con il codice QR), Gmail, i giochi (usa_app con l'azione aiuto "
           "per sapere come)",
    "abitudini": "le cose utili di tutti i giorni (orari, preferenze, persone ricorrenti), da salvare con ricorda "
                 "solo dopo il suo sì",
}
MARKS = ("fatto", "saltato")


class Welcome:
    def __init__(self, path: Path, setup_done: Path, language: Callable[[], str] = lambda: "it",
                 clock: Callable[[], float] = time.time) -> None:
        self.path, self.setup_done = path, setup_done
        self.language, self.clock = language, clock
        self.postponed = False  # "dopo": fino alla prossima accensione

    def state(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        steps = data.get("steps") if isinstance(data, dict) else None
        steps = {k: v for k, v in (steps or {}).items() if k in STEPS and v in MARKS} if isinstance(steps, dict) else {}
        return {"steps": steps, "finished": bool(isinstance(data, dict) and data.get("finished")),
                "started": data.get("started") if isinstance(data, dict) else None}

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def remaining(self) -> list[str]:
        done = self.state()["steps"]
        # in inglese non c'è il tu o il Lei: quel passo non si chiede
        return [s for s in STEPS if s not in done and not (s == "registro" and self.language() == "en")]

    def due(self) -> bool:
        """Va proposta adesso? Solo dopo il primo avvio grafico, finché non è finita e se non è stata rimandata."""
        return self.setup_done.exists() and not self.state()["finished"] and not self.postponed \
            and bool(self.remaining())

    def mark(self, step: str, how: str) -> str:
        if step not in STEPS:
            raise ValueError(f"passo sconosciuto '{step}' (ci sono: {', '.join(STEPS)})")
        if how not in MARKS:
            raise ValueError(f"segno sconosciuto '{how}'")
        st = self.state()
        st["steps"][step] = how
        st["started"] = st["started"] or self.clock()
        if not [s for s in STEPS if s not in st["steps"] and not (s == "registro" and self.language() == "en")]:
            st["finished"] = True
        self._save(st)
        return "presentazione finita" if st["finished"] else "passi rimasti: " + ", ".join(self.remaining())

    def finish(self) -> None:
        st = self.state()
        st["finished"] = True
        self._save(st)

    def restart(self) -> None:
        self._save({"steps": {}, "finished": False, "started": self.clock()})
        self.postponed = False

    def listing(self) -> str:
        left = self.remaining()
        if self.state()["finished"] or not left:
            return "La presentazione è finita. Per rifarla: presentazione con azione ricomincia."
        return "Passi rimasti, nell'ordine:\n" + "\n".join(f"- {s}: {WHAT[s]}" for s in left)

    def start_event(self, user: str) -> str | None:
        """All'accensione: l'[EVENTO] da mandare al cervello, oppure None se non c'è niente da proporre."""
        return self.event_text(user) if self.due() else None

    def event_text(self, user: str) -> str:
        """L'[EVENTO] per il cervello: la prima volta presentarsi e cominciare, poi chiedere se continuare."""
        left = "; ".join(f"{s}: {WHAT[s]}" for s in self.remaining())
        if not self.state()["steps"]:
            return (f"Avviso di sistema: è la prima volta che parli con {user} dopo l'installazione. Presentati in due "
                    "frasi (chi sei, che vivi sul suo PC, che il resto si sistema parlando) e guida la configurazione "
                    "a voce, un passo alla volta: fai una domanda, aspetta la risposta, poi agisci. Passi: "
                    f"{left}. Segna ogni passo con lo strumento presentazione (fatto o salta). Se preferisce farla "
                    "dopo, usa presentazione con azione rimanda.")
        return (f"Avviso di sistema: la configurazione a voce con {user} è rimasta a metà. Chiedigli in una frase se "
                f"vuole continuarla adesso; se sì, passi rimasti: {left}. Se no, presentazione con azione rimanda.")

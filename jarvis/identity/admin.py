"""Gestione delle voci a voce: "impara la mia voce", "impara la voce di Marco", "Marco può usare il PC"..."""

from __future__ import annotations

import re
from collections.abc import Callable

from .enroll import Enrollment
from .guard import GRANTABLE, GROUP_NAMES
from .people import KNOWN, OWNER, People

_ME = {"mia", "la mia", "me", "io", "mio", "proprietario", "padrone"}
_ALIASES = {"pc": "pc", "computer": "pc", "app": "pc", "siti": "pc", "browser": "pc", "appunti": "pc",
            "compiti": "compiti", "sotto-agenti": "compiti", "sottoagenti": "compiti", "lavori": "compiti"}


class VoiceAdmin:
    def __init__(self, people: People, owner_name: str, start: Callable[[Enrollment], str]) -> None:
        self.people = people
        self.owner_name = owner_name
        self._start = start  # la conversazione raccoglie le frasi lette

    def learn(self, name: str, note: str = "") -> str:
        clean = re.sub(r"\s+", " ", name).strip()
        me = clean.lower() in _ME or clean.lower() == self.owner_name.lower()
        if not clean:
            return "Errore: di chi è la voce da imparare?"
        e = Enrollment(self.owner_name if me else clean.title(), OWNER if me else KNOWN, note.strip())
        return self._start(e)

    def known(self) -> str:
        people = self.people.all()
        if not people:
            return "Non conosco ancora nessuna voce: il riconoscimento è spento (tutti trattati come prima)."
        rows = []
        for p in people:
            extra = ", ".join(GROUP_NAMES[g] for g in sorted(p.groups & GRANTABLE)) or "solo le cose di base"
            role = "proprietario: tutto" if p.owner else f"conosciuto: {extra}"
            rows.append(f"- {p.name} ({role}){f' — {p.note}' if p.note else ''}")
        return "\n".join(rows)

    def grant(self, name: str, groups: list[str]) -> str:
        p = self.people.get(name.strip())
        if p is None:
            return f"Errore: non conosco la voce di {name}. Prima va imparata (impara_voce)."
        if p.owner:
            return f"{p.name} può già fare tutto."
        wanted = {_ALIASES.get(g.strip().lower(), g.strip().lower()) for g in groups if g.strip()}
        bad = wanted - GRANTABLE
        if bad:
            return f"Errore: permessi sconosciuti {sorted(bad)}; si possono concedere solo: pc, compiti."
        self.people.set_groups(p.name, wanted)
        now = ", ".join(GROUP_NAMES[g] for g in sorted(wanted)) or "solo le cose di base"
        return f"{p.name} ora può: {now}. Memoria personale, impostazioni e voci restano solo di {self.owner_name}."

    def forget(self, name: str) -> str:
        p = self.people.get(name.strip())
        if p is None:
            return f"Non conosco nessuna voce di nome {name}."
        if p.owner:
            return ("Errore: la voce del proprietario non si cancella così: per cambiarla basta reimpararla "
                    "(impara_voce con 'la mia').")
        self.people.forget(p.name)
        return f"Ho dimenticato la voce di {p.name}: d'ora in poi sarà un ospite."

"""Chi sta parlando adesso e cosa può fare (regole del progetto, decise il 7/10).

- L'utente riconosciuto dalla voce: tutto. Le azioni irreversibili chiedono comunque conferma (politica a parte).
- Persona conosciuta ("Jarvis, impara la voce di Marco"): i permessi base più quelli che l'utente le concede.
- Voce mai sentita: ospite, solo i permessi base; non riceve niente di personale sull'utente (altra sessione del
  cervello, niente memoria nel contesto: vedi app.py).
- L'utente non riconosciuto (raffreddore, rumore): parola d'ordine detta come frase a sé. Da lì quella voce vale come
  l'utente per la conversazione e fino a 30 minuti di silenzio; quei campioni migliorano la sua impronta.
- Finché l'utente non ha registrato la sua voce il riconoscimento è spento: tutti come prima (via "legacy").
I permessi li controlla il codice (ToolRegistry.set_gate), non il cervello: un ospite non li ottiene convincendolo.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .model import SpeakerModel
from .people import People, Person

log = logging.getLogger("jarvis.identity")

BASE = "base"
GRANTABLE = {"pc", "compiti"}  # si possono concedere a una persona conosciuta
# solo l'utente: i suoi dati, le impostazioni, le voci, i suoi messaggi e la sua posta (WhatsApp, Gmail)
OWNER_ONLY = {"memoria", "sistema", "voci", "messaggi"}
ALL_GROUPS = {BASE} | GRANTABLE | OWNER_ONLY
GROUP_NAMES = {"pc": "usare il PC (aprire app e siti, appunti, schermo, giochi)",
               "compiti": "affidare compiti ai sotto-agenti", "memoria": "la memoria personale",
               "sistema": "le impostazioni e lo stato di Jarvis", "voci": "le voci e i permessi",
               "messaggi": "i messaggi e la posta", BASE: "le cose di base"}


@dataclass
class Voice:
    """Chi ha detto una frase."""
    person: Person | None
    score: float = 0.0
    embedding: np.ndarray | None = None
    via: str = "voce"  # voce | parola (d'ordine) | tastiera | evento | legacy (riconoscimento spento)

    @property
    def owner(self) -> bool:
        return self.via in ("legacy", "tastiera", "evento", "parola") or bool(self.person and self.person.owner)

    @property
    def known(self) -> bool:
        return self.owner or self.person is not None

    @property
    def label(self) -> str:
        if self.person is not None:
            return self.person.name
        return "proprietario" if self.owner else "ospite"

    def groups(self) -> set[str]:
        if self.owner:
            return set(ALL_GROUPS)
        return {BASE} | ((self.person.groups & GRANTABLE) if self.person else set())


LEGACY = Voice(None, via="legacy")
TYPED = Voice(None, via="tastiera")  # dalla finestra sul PC: chi scrive è davanti al computer
EVENT = Voice(None, via="evento")  # Jarvis riferisce qualcosa (un compito finito...)


@dataclass
class _Unlock:
    embedding: np.ndarray | None
    last: float
    n: int = 1


class Identity:
    def __init__(self, people: People, model: SpeakerModel, threshold: Callable[[], float] = lambda: 0.40,
                 unlock_s: Callable[[], float] = lambda: 1800.0, same_threshold: float = 0.35,
                 max_attempts: int = 3, lockout_s: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.people = people
        self.model = model
        self.threshold = threshold  # funzioni: le impostazioni si rileggono ogni volta (valgono subito)
        self.unlock_s = unlock_s
        self.same_threshold = same_threshold
        self.max_attempts = max_attempts
        self.lockout_s = lockout_s
        self.clock = clock
        self._unlock: _Unlock | None = None
        self._attempts: deque[float] = deque()
        self._locked_until = 0.0

    @property
    def active(self) -> bool:
        """Acceso solo quando l'utente ha registrato la sua voce (prima nessuno sarebbe riconosciuto)."""
        return self.model.available and self.people.owner() is not None

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        return self.model.embed(audio) if self.model.available else None

    async def who(self, audio: np.ndarray) -> Voice:
        if not self.active:
            return LEGACY
        emb = await asyncio.to_thread(self.model.embed, audio)
        return self.judge(emb)

    def judge(self, emb: np.ndarray | None) -> Voice:
        if emb is None:  # frase troppo corta per dirlo: ospite, ma la sessione sbloccata vale
            return self._unlocked(None) or Voice(None)
        person, score = self.people.best(emb)
        thr = self.threshold()
        # nel registro chi è e quanto somiglia: il 27/10 una frase detta in due è andata all'utente e non si poteva
        # vedere quanto fosse vicina alla soglia
        log.info("voce: %s %.2f (soglia %.2f)", person.name if person else "nessuno", score, thr)
        if person is not None and score >= thr:
            if person.owner and score >= thr + 0.10:  # sicuramente lui: l'impronta segue la voce che cambia
                self.people.adapt(person.name, emb)
            return Voice(person, score, emb)
        return self._unlocked(emb) or Voice(None, score, emb)

    def _unlocked(self, emb: np.ndarray | None) -> Voice | None:
        u = self._unlock
        if u is None or self.clock() - u.last > self.unlock_s():
            self._unlock = None
            return None
        if emb is not None and u.embedding is not None:
            if float(emb @ u.embedding) < self.same_threshold:
                return None
            merged = u.embedding * u.n + emb  # la voce sbloccata si conosce meglio a ogni frase
            u.embedding, u.n = merged / max(float(np.linalg.norm(merged)), 1e-9), u.n + 1
        elif emb is None and u.embedding is not None:
            return None  # frase troppo corta per sapere se è la stessa voce
        u.last = self.clock()
        return Voice(self.people.owner(), 0.0, emb, via="parola")

    def try_passphrase(self, pw: str, voice: Voice) -> str:
        """"ok" (quella voce vale come l'utente), "sbagliata" o "bloccata" (troppi tentativi: per un po' niente)."""
        now = self.clock()
        if now < self._locked_until:
            return "bloccata"
        if not self.people.has_passphrase() or not self.people.check_passphrase(pw):
            self._attempts.append(now)
            while self._attempts and now - self._attempts[0] > self.lockout_s:
                self._attempts.popleft()
            if len(self._attempts) >= self.max_attempts:
                self._locked_until = now + self.lockout_s
                self._attempts.clear()
            return "sbagliata"
        self._attempts.clear()
        self._unlock = _Unlock(voice.embedding, now)
        owner = self.people.owner()
        if owner is not None and voice.embedding is not None:
            self.people.adapt(owner.name, voice.embedding, weight=0.10)  # la prossima volta lo riconosce meglio
        return "ok"

    def same_person(self, a: Voice, b: Voice) -> bool:
        """Due frasi della stessa persona? (per continuare una conversazione senza dire "Jarvis")"""
        if "legacy" in (a.via, b.via):
            return True
        if a.person is not None and b.person is not None:
            return a.person.name == b.person.name
        if a.owner and b.owner:
            return True
        if a.embedding is None or b.embedding is None:
            return False
        return float(a.embedding @ b.embedding) >= self.same_threshold

    @staticmethod
    def refusal(group: str, voice: Voice, owner_name: str = "Utente") -> str | None:
        """None se può; altrimenti il motivo, scritto per il cervello (mai parlare della parola d'ordine)."""
        if group in voice.groups():
            return None
        who = voice.label if voice.person else "una voce che non conosco"
        return (f"Errore: permesso negato. A parlare è {who}, che non può usare {GROUP_NAMES.get(group, group)}: "
                f"è riservato a {owner_name}. Spiegalo con garbo, senza dire come si ottiene il permesso.")

"""Due sessioni del cervello: quella dell'utente e quella degli ospiti.

Un ospite parla con una sessione che non ha mai visto le conversazioni dell'utente, la sua memoria né il suo profilo:
non c'è niente da far trapelare, anche se l'ospite prova a convincerla. La sessione ospiti nasce al primo ospite e
resta per le conversazioni successive con ospiti. Chi parla lo dice `who()` (la conversazione lo sa a ogni turno).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from .base import BrainEvent

log = logging.getLogger("jarvis.brain")


class BrainSelector:
    def __init__(self, owner: Any, make_guest: Callable[[], Any], who: Callable[[], str]) -> None:
        self.owner = owner
        self._make_guest = make_guest
        self.who = who  # "owner" oppure "guest"
        self.guest: Any = None
        self._last: Any = owner

    def __getattr__(self, name: str) -> Any:  # nome, ripieghi, pause...: quelli della sessione dell'utente
        if name == "owner":
            raise AttributeError(name)
        return getattr(self.owner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "recap":  # il riassunto per un cervello che subentra riguarda le conversazioni dell'utente
            setattr(self.owner, name, value)
        else:
            super().__setattr__(name, value)

    async def start(self) -> None:
        await self.owner.start()

    async def stop(self) -> None:
        await self.owner.stop()
        if self.guest is not None:
            await self.guest.stop()

    async def interrupt(self) -> None:
        await self._last.interrupt()

    async def respond(self, message: str) -> AsyncIterator[BrainEvent]:
        if self.who() == "owner":
            brain = self.owner
        else:
            if self.guest is None:
                log.info("prima conversazione con un ospite: apro una sessione separata")
                self.guest = self._make_guest()
                await self.guest.start()
            brain = self.guest
        self._last = brain
        async for ev in brain.respond(message):
            yield ev

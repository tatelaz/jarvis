"""Instradatore dei cervelli: il principale quando c'è, i ripieghi quando manca, e ritorno automatico.

- Un cervello che non risponde va "in pausa" fino all'ora di ripristino che conosciamo (es. il limite
  d'uso di Claude che si azzera alle 20:20) o per un minuto se non la sappiamo; poi si riprova.
- Se il limite è dell'account, vanno in pausa tutti i cervelli dello stesso fornitore: inutile passare
  da Opus a Haiku se l'abbonamento è esaurito per entrambi.
- Il cervello che subentra (o che torna dopo una pausa) non ha sentito gli ultimi scambi: riceve un
  riassunto, così capisce ancora a cosa si riferisce l'utente.
- Ogni cambio produce una Notice("brain_switch") che la conversazione trasforma in una frase.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from .base import BrainEvent, BrainUnavailable, Done, Notice, TextDelta, ToolCall, ToolResult, ToolStart

log = logging.getLogger("jarvis.brain")

DEFAULT_PAUSE_S = 60.0


def provider_of(brain: Any) -> str:
    return getattr(brain, "provider", None) or getattr(brain, "name", "?")


def _enabled(brain: Any) -> bool:
    """Un cervello spento nelle impostazioni (es. brain.opencode, a voce) si salta senza metterlo in pausa."""
    enabled = getattr(brain, "enabled", None)
    return bool(enabled()) if callable(enabled) else True


class BrainRouter:
    def __init__(self, brains: list[Any], recap: Callable[[], str] | None = None,
                 clock: Callable[[], float] = time.time, preferred: Callable[[], str] | None = None) -> None:
        assert brains, "serve almeno un cervello"
        self.brains = brains
        self.recap = recap
        self.preferred = preferred  # fornitore scelto dall'utente ("claude", "ollama-cloud", "local"): va in testa
        self.clock = clock
        self.paused_until: dict[int, float] = {}
        self.reasons: dict[int, BrainUnavailable] = {}
        self._active: Any = None
        self._last_used: Any = brains[0]  # chi ha risposto all'ultimo turno (il principale ha già il contesto)

    @property
    def name(self) -> str:
        return getattr(self._active or self._last_used, "name", "?")

    def order(self) -> list[Any]:
        """La catena nell'ordine di oggi: prima il fornitore preferito, poi gli altri nell'ordine dato."""
        pref = self.preferred() if self.preferred else None
        if not pref:
            return list(self.brains)
        return sorted(self.brains, key=lambda b: provider_of(b) != pref)  # stabile

    @property
    def primary(self) -> Any:
        return self.order()[0]

    def available(self) -> list[Any]:
        now = self.clock()
        return [b for b in self.brains if self.paused_until.get(id(b), 0.0) <= now and _enabled(b)]

    def _pause(self, brain: Any, err: BrainUnavailable) -> None:
        until = err.retry_at if err.retry_at and err.retry_at > self.clock() else self.clock() + DEFAULT_PAUSE_S
        targets = [b for b in self.brains if provider_of(b) == provider_of(brain)] if err.scope == "provider" else [brain]
        for b in targets:
            self.paused_until[id(b)] = max(self.paused_until.get(id(b), 0.0), until)
            self.reasons[id(b)] = err
        log.warning("cervello in pausa fino a %s: %s (%s)", time.strftime("%H:%M", time.localtime(until)),
                    ", ".join(getattr(b, "name", "?") for b in targets), err)

    def earliest_return(self) -> float | None:
        return min(self.paused_until.values(), default=None)

    def provider_paused_until(self, provider: str) -> float | None:
        """Fino a quando l'account di un fornitore è esaurito (pausa per il limite dell'account), se lo è adesso.
        Un cervello solo lento o caduto non conta: i sotto-agenti continuano a usarlo."""
        now = self.clock()
        found = [self.paused_until[id(b)] for b in self.brains
                 if provider_of(b) == provider and self.paused_until.get(id(b), 0.0) > now
                 and getattr(self.reasons.get(id(b)), "scope", "brain") == "provider"]
        return max(found, default=None)

    async def start(self) -> None:
        """Avvia il preferito; se non c'è (es. Bonsai spento) Jarvis parte lo stesso coi ripieghi."""
        primary = self.primary
        try:
            await primary.start()
        except BrainUnavailable as e:
            self._pause(primary, e)

    async def stop(self) -> None:
        for b in self.brains:
            stop = getattr(b, "stop", None)
            if stop is not None:
                await stop()

    async def interrupt(self) -> None:
        if self._active is not None:
            await self._active.interrupt()

    def _with_recap(self, brain: Any, message: str) -> str:
        if brain is self._last_used or self.recap is None:
            return message
        recap = self.recap().strip()
        if not recap:
            return message
        return (f"<riassunto>\nGli ultimi scambi li ha gestiti un altro sistema mentre eri non disponibile:\n{recap}\n"
                f"</riassunto>\n{message}")

    def _switch_info(self, brain: Any, why: BrainUnavailable | None, primary: Any) -> dict:
        back = brain is primary
        return {"to": getattr(brain, "name", "?"), "to_provider": provider_of(brain), "back": back,
                "from_provider": provider_of(primary),
                "reason": str(why) if why else "", "scope": why.scope if why else "brain",
                "until": None if back else self.paused_until.get(id(primary))}

    async def respond(self, message: str) -> AsyncIterator[BrainEvent]:
        first_error: BrainUnavailable | None = None  # il motivo che conta per l'utente: quello del primo
        chain = self.order()
        primary = chain[0]
        for brain in chain:
            if brain not in self.available():
                first_error = first_error or self.reasons.get(id(brain))
                continue
            announce = brain is not self._last_used
            self._active = brain
            produced = False
            try:
                async for ev in brain.respond(self._with_recap(brain, message)):
                    if not produced and isinstance(ev, TextDelta | ToolStart | ToolCall | ToolResult | Done):
                        produced = True
                        # si annuncia il cambio solo quando il nuovo cervello risponde davvero
                        if announce:
                            yield Notice("brain_switch", self._switch_info(brain, first_error, primary))
                    yield ev
                self._last_used = brain
                return
            except BrainUnavailable as e:
                self._pause(brain, e)
                if produced:  # a metà risposta: meglio fermarsi che ripetere la frase con un altro cervello
                    raise
                first_error = first_error or e
            finally:
                self._active = None
        raise BrainUnavailable(str(first_error) if first_error else "nessun cervello disponibile",
                               retry_at=self.paused_until.get(id(primary)) or self.earliest_return(),
                               scope=first_error.scope if first_error else "brain", exhausted=True)

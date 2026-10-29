"""Tipi comuni a tutti i cervelli (Claude, locale)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


class BrainUnavailable(RuntimeError):
    """Il cervello non risponde (rete, limiti d'uso, processo caduto): si passa al ripiego."""

    def __init__(self, message: str = "", retry_at: float | None = None, scope: str = "brain",
                 exhausted: bool = False) -> None:
        super().__init__(message)
        self.retry_at = retry_at  # quando torna disponibile (epoch), se si sa
        self.scope = scope  # "brain" = solo questo cervello; "provider" = tutto l'account (es. abbonamento esaurito)
        self.exhausted = exhausted  # nessun ripiego rimasto


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolStart:
    """Il cervello ha appena iniziato a chiamare uno strumento: gli argomenti arrivano ancora (per un compito, il
    lungo obiettivo può richiedere diversi secondi)."""
    name: str


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    name: str
    ok: bool
    preview: str = ""


@dataclass
class Done:
    text: str  # tutto il testo prodotto in questo turno
    interrupted: bool = False
    error: str | None = None
    usage: dict = field(default_factory=dict)
    model: str = ""


@dataclass
class Notice:
    """Segnalazione non bloccante (es. vicini al limite d'uso)."""
    kind: str
    info: dict = field(default_factory=dict)


BrainEvent = TextDelta | ToolStart | ToolCall | ToolResult | Done | Notice


class Brain(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def respond(self, message: str) -> AsyncIterator[BrainEvent]: ...
    async def interrupt(self) -> None: ...

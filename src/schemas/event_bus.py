from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from schemas.domain import DomainEvent


EventHandler = Callable[[DomainEvent], None]


class EventBus(ABC):
    """Interface for publishing and subscribing to domain events."""

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to all registered subscribers."""

    @abstractmethod
    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for a specific event type."""

    @abstractmethod
    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a previously registered handler."""


class InMemoryEventBus(EventBus):
    """Simple in-process event bus for synchronous dispatch."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(event.event_type, []):
            handler(event)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

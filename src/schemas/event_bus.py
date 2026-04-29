from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Type, Union

from schemas.domain import DomainEvent


EventHandler = Callable[[DomainEvent], None]
EventTypeRef = Union[str, Type[DomainEvent]]


class EventBus(ABC):
    """Interface for publishing and subscribing to domain events."""

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to all registered subscribers."""

    @abstractmethod
    def subscribe(self, event_type: EventTypeRef, handler: EventHandler) -> None:
        """Register a handler for a specific event type.

        *event_type* may be the event class itself or its string name.
        """

    @abstractmethod
    def unsubscribe(self, event_type: EventTypeRef, handler: EventHandler) -> None:
        """Remove a previously registered handler."""

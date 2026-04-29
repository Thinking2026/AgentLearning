from __future__ import annotations

import logging
from typing import Type, Union

from schemas.domain import DomainEvent
from schemas.event_bus import EventBus, EventHandler, EventTypeRef

logger = logging.getLogger(__name__)


def _resolve_key(event_type: EventTypeRef) -> str:
    """Normalise an event type reference to its string key."""
    if isinstance(event_type, str):
        return event_type
    return event_type.__name__


class InMemoryEventBus(EventBus):
    """Synchronous in-process event bus.

    Handlers are invoked in subscription order.  A failing handler is logged
    and skipped so that remaining handlers always run.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    # ------------------------------------------------------------------
    # EventBus interface
    # ------------------------------------------------------------------

    def publish(self, event: DomainEvent) -> None:
        for handler in list(self._handlers.get(event.event_type, [])):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event handler raised an exception",
                    extra={
                        "event_type": event.event_type,
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                    },
                )

    def subscribe(self, event_type: EventTypeRef, handler: EventHandler) -> None:
        self._handlers.setdefault(_resolve_key(event_type), []).append(handler)

    def unsubscribe(self, event_type: EventTypeRef, handler: EventHandler) -> None:
        key = _resolve_key(event_type)
        handlers = self._handlers.get(key, [])
        if handler in handlers:
            handlers.remove(handler)

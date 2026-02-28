"""TitanFlow Event Bus — pub/sub between modules."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

logger = logging.getLogger("titanflow.events")

# Type alias for event handlers
EventHandler = Callable[["Event"], Coroutine[Any, Any, None]]


@dataclass
class Event:
    """An event that flows through TitanFlow."""

    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"Event(topic={self.topic!r}, source={self.source!r})"


class EventBus:
    """Simple async pub/sub event bus.

    Modules subscribe to topics and publish events.
    Handlers run concurrently via asyncio.gather.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Subscribe to a specific topic. Use '*' for all events."""
        if topic == "*":
            self._wildcard_handlers.append(handler)
        else:
            self._handlers[topic].append(handler)
        logger.debug(f"Subscribed handler to topic: {topic}")

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Remove a handler from a topic."""
        if topic == "*":
            self._wildcard_handlers.remove(handler)
        else:
            self._handlers[topic].remove(handler)

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        handlers = list(self._handlers.get(event.topic, []))
        handlers.extend(self._wildcard_handlers)

        # Also match prefix patterns: "research.*" matches "research.new_item"
        for pattern, pattern_handlers in self._handlers.items():
            if pattern.endswith(".*") and event.topic.startswith(pattern[:-2]):
                handlers.extend(pattern_handlers)

        if not handlers:
            logger.debug(f"No handlers for event: {event}")
            return

        logger.debug(f"Publishing {event} to {len(handlers)} handler(s)")

        results = await asyncio.gather(
            *[self._safe_call(h, event) for h in handlers],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Event handler error for {event.topic}: {r}")

    async def emit(self, topic: str, data: dict[str, Any] | None = None, source: str = "unknown") -> None:
        """Convenience: create and publish an event in one call."""
        event = Event(topic=topic, data=data or {}, source=source)
        await self.publish(event)

    @staticmethod
    async def _safe_call(handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            logger.exception(f"Error in handler {handler.__qualname__} for {event.topic}")
            raise

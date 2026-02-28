"""TitanFlow Base Module — the contract every module follows."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from titanflow.core.engine import TitanFlowEngine

logger = logging.getLogger("titanflow.module")


class BaseModule(ABC):
    """Abstract base class for all TitanFlow modules.

    Every module gets access to the engine's shared services:
    - events: publish/subscribe event bus
    - llm: local + cloud LLM inference
    - scheduler: cron and interval job scheduling
    - db: async SQLite database
    """

    name: str = "unnamed"
    description: str = ""
    enabled: bool = True

    def __init__(self, engine: TitanFlowEngine) -> None:
        self.engine = engine
        self.events = engine.events
        self.llm = engine.llm
        self.scheduler = engine.scheduler
        self.db = engine.db
        self.config = engine.config
        self.log = logging.getLogger(f"titanflow.{self.name}")

    @abstractmethod
    async def start(self) -> None:
        """Called when the module is loaded.

        Register event subscriptions, schedule recurring jobs,
        and initialize any module-specific resources here.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Called on shutdown. Clean up resources."""
        ...

    async def handle_telegram(
        self, command: str, args: str, context: Any
    ) -> str | None:
        """Handle a Telegram command routed to this module.

        Return a response string, or None if this module
        doesn't handle the given command.
        """
        return None

    def __repr__(self) -> str:
        status = "enabled" if self.enabled else "disabled"
        return f"<Module:{self.name} [{status}]>"

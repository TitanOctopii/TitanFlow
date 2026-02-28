"""TitanFlow Engine — the central orchestration core."""

from __future__ import annotations

import logging
from typing import Any

from titanflow.config import TitanFlowConfig
from titanflow.core.database import Database
from titanflow.core.events import EventBus
from titanflow.core.llm import LLMClient
from titanflow.core.scheduler import Scheduler
from titanflow.models import AuditLog
from titanflow.modules.base import BaseModule

logger = logging.getLogger("titanflow.engine")


class TitanFlowEngine:
    """Central orchestration engine.

    Owns all shared services and manages module lifecycle.
    """

    def __init__(self, config: TitanFlowConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.llm = LLMClient(config.llm)
        self.scheduler = Scheduler()
        self.db = Database(config.database)
        self._modules: dict[str, BaseModule] = {}

    def register_module(self, module: BaseModule) -> None:
        """Register a module with the engine."""
        self._modules[module.name] = module
        logger.info(f"Registered module: {module.name}")

    async def start(self) -> None:
        """Initialize all services and start all enabled modules."""
        logger.info("═" * 50)
        logger.info("  TitanFlow Engine starting...")
        logger.info("═" * 50)

        # Initialize database
        await self.db.init()
        logger.info("✓ Database initialized")

        # Start scheduler
        self.scheduler.start()
        logger.info("✓ Scheduler started")

        # Check LLM health
        health = await self.llm.health_check()
        if health["status"] == "ok":
            logger.info(f"✓ LLM connected — {len(health['models'])} model(s) available")
        else:
            logger.warning(f"⚠ LLM health check failed: {health.get('error', 'unknown')}")

        # Start enabled modules
        for name, module in self._modules.items():
            if module.enabled:
                try:
                    await module.start()
                    logger.info(f"✓ Module started: {name}")
                except Exception:
                    logger.exception(f"✗ Failed to start module: {name}")
            else:
                logger.info(f"○ Module disabled: {name}")

        await self.events.emit("engine.started", source="engine")
        logger.info("═" * 50)
        logger.info(f"  TitanFlow ready — {len(self.active_modules)} module(s) active")
        logger.info("═" * 50)

    async def shutdown(self) -> None:
        """Stop all modules and services."""
        logger.info("TitanFlow shutting down...")

        # Stop modules in reverse order
        for name in reversed(list(self._modules.keys())):
            module = self._modules[name]
            if module.enabled:
                try:
                    await module.stop()
                    logger.info(f"Stopped module: {name}")
                except Exception:
                    logger.exception(f"Error stopping module: {name}")

        # Shutdown services
        self.scheduler.shutdown()
        await self.llm.close()
        await self.db.close()

        await self.events.emit("engine.stopped", source="engine")
        logger.info("TitanFlow shutdown complete")

    @property
    def active_modules(self) -> list[str]:
        return [name for name, m in self._modules.items() if m.enabled]

    @property
    def modules(self) -> dict[str, BaseModule]:
        return dict(self._modules)

    def get_module(self, name: str) -> BaseModule | None:
        return self._modules.get(name)

    async def route_telegram(self, command: str, args: str, context: Any) -> str:
        """Route a Telegram command to the appropriate module.

        Tries each module until one handles it.
        """
        for module in self._modules.values():
            if not module.enabled:
                continue
            result = await module.handle_telegram(command, args, context)
            if result is not None:
                return result

        return f"Unknown command: /{command}. Use /help to see available commands."

    async def audit(
        self,
        event_type: str,
        command: str = "",
        args: str = "",
        result: str = "success",
        details: str = "",
        user_id: int | None = None,
        duration_ms: int = 0,
    ) -> None:
        """Write an entry to the audit log. Fire-and-forget."""
        try:
            async with self.db.session() as session:
                entry = AuditLog(
                    event_type=event_type,
                    user_id=user_id,
                    command=command,
                    args=args[:500],
                    result=result,
                    details=details[:1000],
                    duration_ms=duration_ms,
                )
                session.add(entry)
                await session.commit()
        except Exception:
            logger.debug("Audit log write failed", exc_info=True)

    def status(self) -> dict[str, Any]:
        """Get engine status overview."""
        return {
            "name": self.config.name,
            "modules": {
                name: {
                    "enabled": m.enabled,
                    "description": m.description,
                }
                for name, m in self._modules.items()
            },
            "scheduled_jobs": self.scheduler.list_jobs(),
        }

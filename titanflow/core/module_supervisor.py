"""Module supervisor (MVP: detect disconnect and alert Papa).

Alert policy: ONE alert per disconnect event. Once a module is marked
dead the supervisor suppresses further alerts until the module
reconnects via a fresh auth.register handshake.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

logger = logging.getLogger("titanflow.supervisor")


@dataclass
class ModuleState:
    module_id: str
    last_seen: float
    connected: bool = True
    alerted: bool = False  # True once Papa has been notified; reset on reconnect


class ModuleSupervisor:
    def __init__(
        self,
        notify_fn: Callable[[str], Awaitable[None]],
        health_interval: int = 60,
    ) -> None:
        self._modules: dict[str, ModuleState] = {}
        self._notify = notify_fn
        self._health_interval = health_interval
        self._task: asyncio.Task | None = None

    def module_connected(self, module_id: str) -> None:
        """Called on successful auth.register — resets all flags."""
        self._modules[module_id] = ModuleState(
            module_id=module_id,
            last_seen=time.time(),
            connected=True,
            alerted=False,
        )
        logger.info("Module connected: %s", module_id)

    def module_heartbeat(self, module_id: str) -> None:
        state = self._modules.get(module_id)
        if state:
            state.last_seen = time.time()

    async def module_disconnected(self, module_id: str) -> None:
        """Mark module dead and alert Papa — exactly once."""
        state = self._modules.get(module_id)

        # Guard: already alerted for this failure → suppress
        if state and state.alerted:
            return

        # Mark dead + alerted in one shot (before any await)
        if state:
            state.connected = False
            state.alerted = True

        logger.warning("Module disconnected: %s", module_id)
        await self._notify(f"⚠ TitanFlow module '{module_id}' disconnected.")

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watchdog())

    async def _watchdog(self) -> None:
        """Periodic check: if a module stops heartbeating, treat it as dead."""
        while True:
            now = time.time()
            for module_id, state in list(self._modules.items()):
                # Only alert for modules that are still marked alive
                # AND haven't been alerted yet (belt + suspenders)
                if (
                    state.connected
                    and not state.alerted
                    and now - state.last_seen > self._health_interval * 3
                ):
                    await self.module_disconnected(module_id)
            await asyncio.sleep(self._health_interval)

    def status(self) -> dict[str, dict]:
        return {
            module_id: {
                "connected": state.connected,
                "last_seen": state.last_seen,
            }
            for module_id, state in self._modules.items()
        }

"""Module outbound dispatch scaffold."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from titanflow.v03.ipc_server import IPCEnvelope, IPCServer, IPCValidationError


class ModuleDispatcher:
    def __init__(self, ipc: IPCServer, socket_path: str | None = None) -> None:
        self._ipc = ipc
        self._socket_path = socket_path
        self._task: asyncio.Task | None = None

    async def start(self, module_id: str | None = None) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(module_id))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self, module_id: str | None) -> None:
        while True:
            try:
                if module_id is None:
                    envelope = await self._ipc.next_outbound_any()
                else:
                    envelope = await self._ipc.next_outbound(module_id)
            except IPCValidationError:
                continue
            await self._send(envelope)

    async def _send(self, envelope: IPCEnvelope) -> None:
        socket_path = self._socket_path or f"/run/titanflow-mod-{envelope.module_id}/{envelope.module_id}.sock"
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write(json.dumps(envelope.__dict__).encode() + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except OSError as exc:
            await self._ipc._db.insert_dead_letter(
                trace_id=envelope.trace_id,
                session_id=envelope.session_id,
                actor_id=envelope.actor_id,
                module_id=envelope.module_id,
                method=envelope.method,
                reason="dispatch_failed",
                payload={"error": str(exc)},
                priority=envelope.priority,
                queue_name="outbound",
                age_ms=0,
            )
            await self._ipc._db.increment_counter(f"dispatch_failed.module={envelope.module_id}")

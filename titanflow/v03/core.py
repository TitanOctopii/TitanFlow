"""v0.3 core runner scaffold."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from titanflow.v03.config import CoreConfig
from titanflow.v03.db_broker import SQLiteBroker
from titanflow.v03.ipc_server import IPCEnvelope, IPCServer
from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.scheduler import AsyncScheduler
from titanflow.v03.session_manager import SessionManager
from titanflow.v03.telemetry_server import TelemetryServer
from titanflow.v03.watchdog import Watchdog
from titanflow.v03.cache_manager import CacheManager
from titanflow.v03.llm_broker import LLMBroker, LLMRequest
from titanflow.v03.ipc_transport import IPCTransport
from titanflow.v03.module_dispatch import ModuleDispatcher
from titanflow.v03.ipc_inbound_loop import IPCInboundLoop

logger = logging.getLogger("titanflow.v03.core")


class Core:
    def __init__(
        self,
        *,
        config: CoreConfig,
        db_path: str,
        llm_stream_fn=None,
    ) -> None:
        self._config = config
        self._clock = KernelClock()
        self._db = SQLiteBroker(
            db_path,
            max_queue=config.db_max_queue,
            enqueue_timeout_s=config.db_job_enqueue_timeout_s,
            exec_timeout_s=config.db_job_exec_timeout_s,
            wal_pressure_bytes=config.wal_pressure_bytes,
            shutdown_deadline_s=config.shutdown_deadline_s,
        )
        self._sessions = SessionManager(self._db, session_ttl_days=config.session_ttl_days)
        self._ipc = IPCServer(db=self._db, clock=self._clock, config=config, sessions=self._sessions)
        self._scheduler = AsyncScheduler(self._clock)
        self._telemetry = TelemetryServer(config.telemetry_socket, self._db)
        core_socket = config.core_socket if hasattr(config, "core_socket") else "/run/titanflow/core.sock"
        self._ipc_transport = IPCTransport(core_socket, self._ipc)
        self._dispatcher = ModuleDispatcher(self._ipc)
        self._inbound_loop = IPCInboundLoop(ipc=self._ipc, handler=self._handle_inbound)
        # LLM broker wiring
        self._llm: LLMBroker | None = None
        self._cache: CacheManager | None = None
        if llm_stream_fn is not None:
            self._llm = LLMBroker(
                clock=self._clock,
                db=self._db,
                config=self._config,
                llm_stream_fn=llm_stream_fn,
            )
            self._cache = CacheManager(self._llm)
        self._watchdog = Watchdog(
            clock=self._clock,
            watchdog_sec=config.watchdog_sec,
            lag_max_s=config.watchdog_lag_max_s,
            health_check=self._health_check,
        )

    async def start(self) -> None:
        await self._db.start()
        await self._db.init_schema()
        await self._telemetry.start()
        await self._ipc_transport.start()
        self._scheduler.every(self._config.wal_passive_every_s, self._db.checkpoint_passive)
        self._scheduler.every(self._config.wal_truncate_every_s, self._db.checkpoint_truncate)
        self._scheduler.every(3600, self._evict_cache)
        self._scheduler.every(3600, self._sessions.cleanup_sessions)
        await self._inbound_loop.start()
        await self._dispatcher.start()
        if self._llm is not None:
            await self._llm.start()
        await self._watchdog.start()
        self._watchdog.notify_ready()
        logger.info("v0.3 Core started")

    async def stop(self) -> None:
        await self._watchdog.stop()
        await self._dispatcher.stop()
        await self._inbound_loop.stop()
        await self._ipc_transport.stop()
        await self._telemetry.stop()
        await self._scheduler.stop()
        await self._db.stop()
        logger.info("v0.3 Core stopped")

    def attach_llm(self, broker: LLMBroker) -> None:
        self._llm = broker
        self._cache = CacheManager(broker)

    async def _evict_cache(self) -> None:
        if self._cache is None:
            return
        await self._cache.evict()

    async def _health_check(self) -> bool:
        if not self._db.is_running:
            return False
        return True

    async def _handle_inbound(self, envelope) -> None:
        # Session touch + minimal routing for scaffold
        await self._sessions.touch_session(envelope.session_id, envelope.actor_id)
        if envelope.method == "sessions.create":
            await self._sessions.create_session(
                envelope.session_id,
                envelope.actor_id,
                envelope.payload.get("metadata"),
            )
            await self._db.increment_counter("sessions.created")
            return
        if envelope.method == "llm.request":
            if self._llm is None:
                await self._db.insert_dead_letter(
                    trace_id=envelope.trace_id,
                    session_id=envelope.session_id,
                    actor_id=envelope.actor_id,
                    module_id=envelope.module_id,
                    method=envelope.method,
                    reason="llm_unavailable",
                    payload=envelope.payload,
                    priority=envelope.priority,
                    queue_name="llm",
                    age_ms=int((self._clock.now() - envelope.created_monotonic) * 1000),
                )
                await self._db.increment_counter("llm.unavailable")
                return
            asyncio.create_task(self._handle_llm_request(envelope))
            return
        await self._db.increment_counter(f"inbound_seen.module={envelope.module_id}")

    async def _handle_llm_request(self, envelope) -> None:
        req = LLMRequest(
            priority=envelope.priority,
            created_monotonic=envelope.created_monotonic,
            trace_id=envelope.trace_id,
            session_id=envelope.session_id,
            actor_id=envelope.actor_id,
            module_id=envelope.module_id,
            prompt=envelope.payload.get("prompt", ""),
            system_prompt=envelope.payload.get("system_prompt", ""),
            system_prompt_version=envelope.payload.get("system_prompt_version", "v1"),
            model=envelope.payload.get("model", ""),
        )
        try:
            result = await self._llm.submit(req)
            await self._db.increment_counter("llm.requests")
            response = {
                "text": result,
                "model": req.model,
            }
            await self._ipc.send_outbound(
                IPCEnvelope(
                    trace_id=req.trace_id,
                    session_id=req.session_id or "",
                    actor_id=req.actor_id or "",
                    created_monotonic=self._clock.now(),
                    priority=req.priority,
                    module_id=req.module_id,
                    method="llm.response",
                    payload=response,
                    stream=False,
                )
            )
        except Exception as exc:
            await self._db.increment_counter("llm.errors")
            await self._ipc.send_outbound(
                IPCEnvelope(
                    trace_id=req.trace_id,
                    session_id=req.session_id or "",
                    actor_id=req.actor_id or "",
                    created_monotonic=self._clock.now(),
                    priority=req.priority,
                    module_id=req.module_id,
                    method="llm.error",
                    payload={"error": str(exc)},
                    stream=False,
                )
            )

    @property
    def ipc(self) -> IPCServer:
        return self._ipc

    @property
    def db(self) -> SQLiteBroker:
        return self._db

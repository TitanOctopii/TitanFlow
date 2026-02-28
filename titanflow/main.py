"""TitanFlow — Main entry point.

Start with:
    python -m titanflow.main
    or
    uvicorn titanflow.main:app --host 0.0.0.0 --port 8800
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from titanflow.api.routes import router
from titanflow.config import load_config
from titanflow.core.engine import TitanFlowEngine
from titanflow.modules.codeexec.module import CodeExecModule
from titanflow.modules.newspaper.module import NewspaperModule
from titanflow.modules.research.module import ResearchModule
from titanflow.telegram.bot import TelegramGateway

# ─── Logging ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

# Quiet noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger("titanflow")

# ─── Global engine reference (for API dependency injection) ─

_engine: TitanFlowEngine | None = None
_telegram: TelegramGateway | None = None


# ─── App Lifecycle ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — start/stop TitanFlow engine."""
    global _engine, _telegram

    config_path = os.environ.get("TITANFLOW_CONFIG")
    config = load_config(config_path)

    logger.info(f"Configuration loaded from: {config_path or 'defaults'}")

    # Create engine
    _engine = TitanFlowEngine(config)

    # Register Phase 1 modules
    if config.modules.research.enabled:
        _engine.register_module(ResearchModule(_engine))

    if config.modules.newspaper.enabled:
        _engine.register_module(NewspaperModule(_engine))

    if config.modules.codeexec.enabled:
        _engine.register_module(CodeExecModule(_engine))

    # TODO Phase 2+: Register additional modules
    # if config.modules.security.enabled:
    #     _engine.register_module(SecurityModule(_engine))
    # if config.modules.home.enabled:
    #     _engine.register_module(HomeModule(_engine))
    # if config.modules.automation.enabled:
    #     _engine.register_module(AutomationModule(_engine))
    # if config.modules.webpub.enabled:
    #     _engine.register_module(WebPubModule(_engine))

    # Start engine
    await _engine.start()

    # Start Telegram bot
    _telegram = TelegramGateway(_engine, config.telegram)
    await _telegram.start()

    yield

    # Shutdown
    if _telegram:
        await _telegram.stop()
    if _engine:
        await _engine.shutdown()


# ─── FastAPI App ──────────────────────────────────────────

app = FastAPI(
    title="TitanFlow",
    description="Orchestration engine for TitanArray",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/")
async def root():
    return {
        "name": "TitanFlow",
        "version": "0.1.0",
        "description": "Orchestration engine for TitanArray",
    }


# ─── CLI Entry Point ─────────────────────────────────────

if __name__ == "__main__":
    _boot_config = load_config(os.environ.get("TITANFLOW_CONFIG"))
    uvicorn.run(
        "titanflow.main:app",
        host=_boot_config.host,
        port=_boot_config.port,
        log_level="info",
        reload=False,
    )

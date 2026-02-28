"""Entry point for v0.3 core scaffold."""

from __future__ import annotations

import asyncio
import logging
import os

from titanflow.config import load_config as load_app_config
from titanflow.core.llm import LLMClient
from titanflow.v03.config import load_config
from titanflow.v03.core import Core

logging.basicConfig(level=logging.INFO)


def main() -> None:
    config = load_config()
    db_path = os.environ.get("TITANFLOW_DB_PATH", "/data/titanflow/titanflow.db")

    app_config = load_app_config()
    llm_client = LLMClient(app_config.llm)

    async def _llm_stream(req):
        model = req.model or app_config.llm.default_model
        return await llm_client.generate(
            req.prompt,
            system=req.system_prompt or "",
            model=model,
        )

    core = Core(config=config, db_path=db_path, llm_stream_fn=_llm_stream)

    async def _runner():
        try:
            await core.start()
            # Block forever
            await asyncio.Event().wait()
        finally:
            await llm_client.close()

    asyncio.run(_runner())


if __name__ == "__main__":
    main()

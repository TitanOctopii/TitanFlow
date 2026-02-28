"""TitanFlow LLM Client — local Ollama inference with cloud escalation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import ollama

from titanflow.config import LLMConfig

logger = logging.getLogger("titanflow.llm")


class LLMClient:
    """Unified LLM interface: Ollama first, cloud fallback.

    Uses a semaphore to serialize Ollama requests — since Ollama processes
    them one at a time anyway, this ensures that interactive chat requests
    (which arrive between research items) get served promptly via FIFO
    ordering instead of piling up behind a large batch.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._ollama = ollama.AsyncClient(host=config.base_url)
        self._http = httpx.AsyncClient(timeout=120.0)
        self._sem = asyncio.Semaphore(1)  # serialize Ollama access

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        force_cloud: bool = False,
    ) -> str:
        """Generate a response. Uses local Ollama by default, cloud if forced or local fails."""
        model = model or self.config.default_model

        if force_cloud:
            return await self._cloud_generate(prompt, system=system, temperature=temperature, max_tokens=max_tokens)

        try:
            return await self._ollama_generate(
                prompt, system=system, model=model, temperature=temperature
            )
        except Exception as e:
            logger.warning(f"Ollama generation failed ({e}), trying fallback model...")
            try:
                return await self._ollama_generate(
                    prompt, system=system, model=self.config.fallback_model, temperature=temperature
                )
            except Exception as e2:
                logger.warning(f"Fallback model failed ({e2}), escalating to cloud...")
                if self.config.cloud.api_key:
                    return await self._cloud_generate(
                        prompt, system=system, temperature=temperature, max_tokens=max_tokens
                    )
                raise RuntimeError(f"All LLM backends failed. Local: {e}, Fallback: {e2}") from e2

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        force_cloud: bool = False,
    ) -> str:
        """Chat-style completion with message history."""
        model = model or self.config.default_model

        if force_cloud and self.config.cloud.api_key:
            return await self._cloud_chat(messages, temperature=temperature)

        try:
            async with self._sem:
                response = await self._ollama.chat(
                    model=model,
                    messages=messages,
                    options={"temperature": temperature},
                )
            return response["message"]["content"]
        except Exception as e:
            logger.warning(f"Ollama chat failed ({e}), escalating to cloud...")
            if self.config.cloud.api_key:
                return await self._cloud_chat(messages, temperature=temperature)
            raise

    async def _ollama_generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str,
        temperature: float,
    ) -> str:
        """Generate via local Ollama (serialized via semaphore)."""
        async with self._sem:
            response = await self._ollama.generate(
                model=model,
                prompt=prompt,
                system=system or "",
                options={"temperature": temperature},
            )
            return response["response"]

    async def _cloud_generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Generate via Anthropic API."""
        messages = [{"role": "user", "content": prompt}]
        return await self._cloud_chat(messages, system=system, temperature=temperature, max_tokens=max_tokens)

    async def _cloud_chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Chat via Anthropic API."""
        payload: dict[str, Any] = {
            "model": self.config.cloud.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        response = await self._http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.config.cloud.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]

    async def health_check(self) -> dict[str, Any]:
        """Check if Ollama is reachable and list available models."""
        try:
            result = await self._ollama.list()
            # Ollama SDK >=0.4 returns Pydantic objects, not dicts
            models_list = result.models if hasattr(result, "models") else result.get("models", [])
            model_names = []
            for m in models_list:
                name = getattr(m, "model", None) or (m.get("name") if isinstance(m, dict) else str(m))
                model_names.append(name)
            return {"status": "ok", "provider": "ollama", "models": model_names}
        except Exception as e:
            return {"status": "error", "provider": "ollama", "error": str(e)}

    async def close(self) -> None:
        await self._http.aclose()

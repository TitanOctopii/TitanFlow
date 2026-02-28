"""HTTP helpers with retry/backoff."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger("titanflow.http")

DEFAULT_BACKOFFS = (2, 4, 8)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    backoff: tuple[int, ...] = DEFAULT_BACKOFFS,
    raise_for_status: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    """Make an HTTP request with simple retry + backoff."""
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if raise_for_status:
                response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # Don't retry 4xx errors (except rate limiting)
            if 400 <= status < 500 and status != 429:
                raise
            last_exc = exc
        except httpx.HTTPError as exc:
            last_exc = exc

        if attempt < attempts:
            delay = backoff[min(attempt - 1, len(backoff) - 1)]
            logger.warning(
                "HTTP %s %s failed (attempt %d/%d): %s. Retrying in %ss",
                method,
                url,
                attempt,
                attempts,
                last_exc,
                delay,
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc

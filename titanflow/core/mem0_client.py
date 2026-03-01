"""mem0-style long-term memory client for TitanFlow.

Uses Qdrant REST API + Ollama embed/generate to:
  - Extract memorable facts from conversation turns
  - Embed and store them in Qdrant
  - Recall relevant memories given a query

Hardened: validates embedding dimensions, API response shapes, URL
connectivity, and collection schema drift.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("titanflow.mem0")

# ── Defaults ──────────────────────────────────────────────────
QDRANT_URL = "http://10.0.0.32:6333"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
EXTRACT_MODEL = "cogito:14b"
COLLECTION = "titanflow_memories"
VECTOR_SIZE = 768  # nomic-embed-text — MUST match the model's output dimensions
TOP_K = 5
SCORE_THRESHOLD = 0.35

# Known embedding dimensions for sanity checking model swaps
_KNOWN_EMBED_DIMS: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
}

EXTRACT_PROMPT = """You are a memory extraction engine. Given a user message and assistant response, extract the key facts worth remembering for future conversations. Focus on:
- User preferences, opinions, and interests
- Personal facts (names, locations, relationships, work)
- Technical decisions or configurations mentioned
- Important events or dates
- Requests or goals the user expressed

Output ONLY a JSON array of short fact strings. If nothing memorable, output [].

User: {user_msg}
Assistant: {assist_msg}

Facts (JSON array):"""


def _validate_url(url: str, label: str) -> str:
    """Fail fast on malformed service URLs."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{label} must use http/https, got: {url!r}")
    if not parsed.hostname:
        raise ValueError(f"{label} missing hostname: {url!r}")
    return url.rstrip("/")


class Mem0Client:
    """Lightweight mem0-style client using Qdrant + Ollama.

    Hardened with:
    - URL validation at init
    - Embedding dimension verification on first use
    - Response shape validation for all API calls
    - Collection schema drift detection
    """

    def __init__(
        self,
        *,
        qdrant_url: str = QDRANT_URL,
        ollama_url: str = OLLAMA_URL,
        embed_model: str = EMBED_MODEL,
        extract_model: str = EXTRACT_MODEL,
        collection: str = COLLECTION,
        top_k: int = TOP_K,
    ):
        self.qdrant_url = _validate_url(qdrant_url, "Qdrant URL")
        self.ollama_url = _validate_url(ollama_url, "Ollama URL")
        self.embed_model = embed_model
        self.extract_model = extract_model
        self.collection = collection
        self.top_k = top_k
        self._http = httpx.AsyncClient(timeout=60.0)
        self._collection_ready = False
        self._embed_dim_verified = False

        # Warn if embed model has a known dimension that differs from VECTOR_SIZE
        known_dim = _KNOWN_EMBED_DIMS.get(embed_model)
        if known_dim and known_dim != VECTOR_SIZE:
            logger.error(
                "mem0: embed_model=%r expects %d-dim vectors but VECTOR_SIZE=%d. "
                "Collection will reject inserts. Fix VECTOR_SIZE or change the model.",
                embed_model, known_dim, VECTOR_SIZE,
            )

    async def close(self) -> None:
        await self._http.aclose()

    # ── Qdrant helpers ─────────────────────────────────────────

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        try:
            r = await self._http.get(f"{self.qdrant_url}/collections/{self.collection}")
            if r.status_code == 200:
                # Verify the existing collection has the right vector size
                data = r.json()
                result = data.get("result", {})
                config = result.get("config", {})
                params = config.get("params", {})
                vectors = params.get("vectors", {})
                existing_size = vectors.get("size")
                if existing_size is not None and existing_size != VECTOR_SIZE:
                    logger.error(
                        "mem0: Qdrant collection '%s' has vector size %d but we expect %d. "
                        "Searches and inserts WILL fail. Recreate the collection or fix VECTOR_SIZE.",
                        self.collection, existing_size, VECTOR_SIZE,
                    )
                    # Don't mark ready — force error on use
                    return
                self._collection_ready = True
                return
            # Create
            r = await self._http.put(
                f"{self.qdrant_url}/collections/{self.collection}",
                json={
                    "vectors": {"size": VECTOR_SIZE, "distance": "Cosine"},
                    "on_disk_payload": True,
                },
            )
            r.raise_for_status()
            self._collection_ready = True
            logger.info("Created Qdrant collection: %s (vector_size=%d)", self.collection, VECTOR_SIZE)
        except httpx.ConnectError as exc:
            logger.warning(
                "mem0: Cannot reach Qdrant at %s — memory features disabled until connectivity restored. Error: %s",
                self.qdrant_url, exc,
            )
        except Exception as exc:
            logger.warning("Qdrant collection check failed: %s", exc)

    async def _store_point(self, fact: str, vector: list[float], meta: dict[str, Any]) -> None:
        # Validate vector dimensions before sending to Qdrant
        if len(vector) != VECTOR_SIZE:
            raise ValueError(
                f"mem0: embedding dimension mismatch — got {len(vector)}, expected {VECTOR_SIZE}. "
                f"Model '{self.embed_model}' may have changed."
            )

        point_id = str(uuid.uuid4())
        r = await self._http.put(
            f"{self.qdrant_url}/collections/{self.collection}/points",
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": {
                            "text": fact,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            **meta,
                        },
                    }
                ]
            },
        )
        r.raise_for_status()

    async def _search(self, vector: list[float], limit: int) -> list[str]:
        # Validate vector dimensions
        if len(vector) != VECTOR_SIZE:
            logger.warning(
                "mem0 search: vector dim %d != expected %d; skipping search",
                len(vector), VECTOR_SIZE,
            )
            return []

        r = await self._http.post(
            f"{self.qdrant_url}/collections/{self.collection}/points/search",
            json={
                "vector": vector,
                "limit": limit,
                "with_payload": True,
                "score_threshold": SCORE_THRESHOLD,
            },
        )
        if r.status_code != 200:
            logger.warning("mem0 search: Qdrant returned %d: %s", r.status_code, r.text[:200])
            return []
        data = r.json()
        if not isinstance(data, dict):
            logger.warning("mem0 search: expected dict response, got %s", type(data).__name__)
            return []
        return [
            hit["payload"]["text"]
            for hit in data.get("result", [])
            if isinstance(hit, dict) and isinstance(hit.get("payload"), dict) and hit["payload"].get("text")
        ]

    # ── Ollama helpers ─────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        r = await self._http.post(
            f"{self.ollama_url}/api/embed",
            json={"model": self.embed_model, "input": text},
        )
        r.raise_for_status()
        data = r.json()

        # Validate response structure
        if not isinstance(data, dict):
            raise ValueError(f"Ollama embed: expected dict, got {type(data).__name__}")
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            raise ValueError(
                f"Ollama embed: 'embeddings' missing or empty. Keys: {sorted(data.keys())}. "
                f"Model '{self.embed_model}' may not support embedding."
            )
        vector = embeddings[0]
        if not isinstance(vector, list):
            raise ValueError(f"Ollama embed: embeddings[0] is {type(vector).__name__}, expected list")

        # Dimension verification (once per session)
        if not self._embed_dim_verified:
            if len(vector) != VECTOR_SIZE:
                logger.error(
                    "mem0: CRITICAL — embed model '%s' produces %d-dim vectors but VECTOR_SIZE=%d. "
                    "All mem0 operations will fail. Update VECTOR_SIZE to %d.",
                    self.embed_model, len(vector), VECTOR_SIZE, len(vector),
                )
                raise ValueError(
                    f"Embedding dimension mismatch: model produces {len(vector)}, expected {VECTOR_SIZE}"
                )
            self._embed_dim_verified = True
            logger.info("mem0: embedding dimension verified (%d-dim from %s)", len(vector), self.embed_model)

        return vector

    async def _extract_facts(self, user_msg: str, assist_msg: str) -> list[str]:
        prompt = EXTRACT_PROMPT.format(user_msg=user_msg[:500], assist_msg=assist_msg[:500])
        r = await self._http.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.extract_model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 1024, "temperature": 0.1},
            },
        )
        r.raise_for_status()
        data = r.json()

        # Validate response
        if not isinstance(data, dict):
            logger.warning("mem0 extract: Ollama returned non-dict: %s", type(data).__name__)
            return []
        raw = data.get("response", "")
        if not isinstance(raw, str):
            logger.warning("mem0 extract: 'response' is %s, expected str", type(raw).__name__)
            return []
        raw = raw.strip()
        if not raw:
            return []

        # Parse JSON array
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        try:
            arr = json.loads(cleaned)
            if isinstance(arr, list):
                return [s for s in arr if isinstance(s, str) and len(s) > 5]
        except json.JSONDecodeError:
            m = re.search(r"\[[\s\S]*?\]", cleaned)
            if m:
                try:
                    arr = json.loads(m.group())
                    if isinstance(arr, list):
                        return [s for s in arr if isinstance(s, str) and len(s) > 5]
                except json.JSONDecodeError:
                    pass
        logger.debug("mem0: could not parse facts from LLM output: %s", raw[:200])
        return []

    # ── Public API ─────────────────────────────────────────────

    async def recall(self, query: str, limit: int | None = None) -> list[str]:
        """Recall memories relevant to *query*."""
        try:
            await self._ensure_collection()
            if not self._collection_ready:
                return []  # Fail fast: don't try embed if collection is broken
            vec = await self._embed(query)
            return await self._search(vec, limit or self.top_k)
        except Exception as exc:
            logger.debug("mem0 recall error: %s", exc)
            return []

    async def capture(self, user_msg: str, assist_msg: str) -> int:
        """Extract and store memorable facts from a conversation turn.

        Returns the number of facts stored.
        """
        if len(user_msg) < 10 or user_msg.startswith("/"):
            return 0
        try:
            await self._ensure_collection()
            if not self._collection_ready:
                return 0  # Fail fast: don't burn LLM cycles if Qdrant is down
            facts = await self._extract_facts(user_msg, assist_msg)
            if not facts:
                return 0

            stored = 0
            for fact in facts:
                try:
                    vec = await self._embed(fact)
                    await self._store_point(fact, vec, {
                        "source": "conversation",
                        "user_preview": user_msg[:200],
                    })
                    stored += 1
                except Exception as exc:
                    logger.debug("mem0 store error for '%s': %s", fact[:40], exc)
            logger.info("mem0: stored %d/%d facts", stored, len(facts))
            return stored
        except Exception as exc:
            logger.debug("mem0 capture error: %s", exc)
            return 0

    async def store_fact(self, fact: str, source: str = "manual") -> bool:
        """Store a single fact directly."""
        try:
            await self._ensure_collection()
            if not self._collection_ready:
                return False
            vec = await self._embed(fact)
            await self._store_point(fact, vec, {"source": source})
            return True
        except Exception as exc:
            logger.debug("mem0 store_fact error: %s", exc)
            return False

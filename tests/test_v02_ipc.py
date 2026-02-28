"""TitanFlow v0.2 MVP acceptance tests.

Tests from the v0.2 plan:
  1. Core starts and listens on socket.
  2. Research module auth handshake succeeds.
  3. http.request works for allowed domains.
  4. http.request denied for unlisted domain.
  5. Kill research module → Telegram alert to Papa.
  6. Chat preempts research in LLM broker.

Plus additional security / plumbing tests:
  7. Unauthorized table access denied.
  8. Audit log populated.
  9. SQL identifier injection blocked.
  10. Invalid token rejected.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest
import yaml

from titanflow.core.auth import AuthManager
from titanflow.core.audit import AuditLogger
from titanflow.core.config import (
    CoreConfig,
    CoreSettings,
    DatabaseSettings,
    HttpProxySettings,
    LLMSettings,
    ModulesSettings,
    TelegramSettings,
    AuditSettings,
)
from titanflow.core.database_broker import DatabaseBroker
from titanflow.core.http_proxy import HttpProxy
from titanflow.core.ipc import IPCServer, start_ipc_server
from titanflow.core.llm_broker import LLMBroker, Priority
from titanflow.core.module_supervisor import ModuleSupervisor


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture()
def tmp_env(tmp_path):
    """Set up all temp dirs, token, manifest, and DB.

    Uses /tmp for socket (macOS 104-byte AF_UNIX limit).
    """
    # macOS limits AF_UNIX paths to 104 bytes; pytest tmp_path is too long
    short_dir = Path(tempfile.gettempdir()) / f"tf-{uuid.uuid4().hex[:8]}"
    short_dir.mkdir()
    socket_path = str(short_dir / "c.sock")
    db_path = str(tmp_path / "test.db")
    manifest_dir = tmp_path / "manifests"
    secrets_dir = tmp_path / "secrets"
    manifest_dir.mkdir()
    secrets_dir.mkdir()

    token = "test-token-abc123"
    (secrets_dir / "research.token").write_text(token)

    manifest = {
        "module": {
            "id": "research",
            "name": "Research Module",
            "version": "0.2.0",
            "description": "RSS/GitHub ingestion and summarization",
            "token_file": str(secrets_dir / "research.token"),
        },
        "permissions": {
            "llm": {
                "enabled": True,
                "priority": "research",
                "models": [],
                "max_tokens_per_request": 2048,
                "max_requests_per_minute": 30,
            },
            "database": {
                "enabled": True,
                "tables": [
                    {"name": "feed_items", "access": "readwrite"},
                    {"name": "feed_sources", "access": "readwrite"},
                    {"name": "github_releases", "access": "readwrite"},
                ],
                "max_rows_per_query": 100,
            },
            "http_outbound": {
                "enabled": True,
                "allowed_domains": [
                    "api.github.com",
                    "*.github.com",
                    "huggingface.co",
                ],
                "max_requests_per_minute": 60,
            },
        },
    }
    (manifest_dir / "research.yaml").write_text(yaml.dump(manifest))

    return {
        "socket_path": socket_path,
        "db_path": db_path,
        "manifest_dir": str(manifest_dir),
        "token": token,
    }


class MockLLMBroker:
    """Fake broker that records calls and returns canned responses."""

    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append({"kind": "generate", "prompt": prompt, **kwargs})
        return "SUMMARY: Test summary\nRELEVANCE: 0.8"

    async def chat(self, messages, **kwargs) -> str:
        self.calls.append({"kind": "chat", "messages": messages, **kwargs})
        return "Hello from the mock broker."


@pytest.fixture()
async def core_stack(tmp_env):
    """Boot full Core stack (IPC, DB, auth, supervisor) and yield components."""
    config = CoreConfig(
        core=CoreSettings(
            instance_name="test", socket_path=tmp_env["socket_path"]
        ),
        telegram=TelegramSettings(),
        llm=LLMSettings(),
        database=DatabaseSettings(path=tmp_env["db_path"]),
        modules=ModulesSettings(manifest_dir=tmp_env["manifest_dir"]),
        http_proxy=HttpProxySettings(),
        audit=AuditSettings(),
    )

    db = DatabaseBroker(config.database)
    await db.init_schema()
    http_proxy = HttpProxy(config.http_proxy)

    auth = AuthManager(config.modules.manifest_dir)
    auth.load_manifests()

    alerts: list[str] = []

    async def mock_notify(msg: str) -> None:
        alerts.append(msg)

    supervisor = ModuleSupervisor(notify_fn=mock_notify, health_interval=2)
    await supervisor.start()
    audit = AuditLogger(db)
    mock_llm = MockLLMBroker()

    ipc = IPCServer(auth, mock_llm, db, http_proxy, audit, supervisor)
    server = await start_ipc_server(tmp_env["socket_path"], ipc)

    yield {
        "server": server,
        "socket_path": tmp_env["socket_path"],
        "token": tmp_env["token"],
        "db": db,
        "alerts": alerts,
        "mock_llm": mock_llm,
    }

    server.close()
    await server.wait_closed()
    await db.close()


async def _connect_and_auth(socket_path: str, token: str):
    """Helper: connect to Core and complete handshake."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    req = {
        "id": "auth-001",
        "module": "research",
        "method": "auth.register",
        "params": {"version": "0.2.0"},
        "token": token,
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())
    return reader, writer, resp


# ── Acceptance Test 1: Core starts and listens on socket ──

@pytest.mark.asyncio
async def test_core_starts_and_listens(core_stack):
    """T1: Core starts and listens on socket."""
    reader, writer = await asyncio.open_unix_connection(
        core_stack["socket_path"]
    )
    assert reader is not None
    writer.close()
    await writer.wait_closed()


# ── Acceptance Test 2: Research module auth handshake ──

@pytest.mark.asyncio
async def test_auth_handshake(core_stack):
    """T2: Research module auth handshake succeeds."""
    reader, writer, resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    assert resp["status"] == "ok"
    assert "session_id" in resp["result"]
    perms = resp["result"]["granted_permissions"]
    assert "llm" in perms
    assert "database" in perms
    assert "http_outbound" in perms
    writer.close()
    await writer.wait_closed()


# ── Acceptance Test 3: http.request for allowed domain (domain validation) ──

@pytest.mark.asyncio
async def test_http_allowed_domain_passes_validation(core_stack):
    """T3: http.request passes domain check for allowed domain."""
    ok = HttpProxy.validate_domain(
        "https://api.github.com/repos/foo/bar",
        ["api.github.com", "*.github.com"],
    )
    assert ok is True


# ── Acceptance Test 4: http.request denied for unlisted domain ──

@pytest.mark.asyncio
async def test_http_denied_unlisted_domain(core_stack):
    """T4: http.request denied for unlisted domain."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    session_id = auth_resp["result"]["session_id"]

    req = {
        "id": "http-deny-001",
        "session_id": session_id,
        "method": "http.request",
        "params": {"url": "https://evil.example.com/steal", "method": "GET"},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())

    assert resp["status"] == "error"
    assert resp["error"]["code"] == "PERMISSION_DENIED"
    assert "Domain" in resp["error"]["message"]

    writer.close()
    await writer.wait_closed()


# ── Acceptance Test 5: Kill module → Telegram alert ──

@pytest.mark.asyncio
async def test_module_disconnect_alert(core_stack):
    """T5: Kill research module → Telegram alert to Papa."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    assert auth_resp["status"] == "ok"

    # Disconnect
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.5)

    alerts = core_stack["alerts"]
    assert any("research" in a for a in alerts), (
        f"Expected disconnect alert, got: {alerts}"
    )


# ── Acceptance Test 6: Chat preempts research in LLM broker ──

@pytest.mark.asyncio
async def test_chat_preempts_research():
    """T6: Chat (priority 0) is dequeued before research (priority 2)."""
    from titanflow.core.llm_broker import LLMRequest, Priority
    import time

    queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

    # Insert research first, then chat
    t = time.time()
    research_req = LLMRequest(
        priority=int(Priority.RESEARCH),
        timestamp=t,
        kind="generate",
        payload={"prompt": "research task"},
        future=asyncio.get_running_loop().create_future(),
    )
    chat_req = LLMRequest(
        priority=int(Priority.CHAT),
        timestamp=t + 1,  # later timestamp, but higher priority
        kind="chat",
        payload={"messages": [{"role": "user", "content": "hi"}]},
        future=asyncio.get_running_loop().create_future(),
    )

    await queue.put(research_req)
    await queue.put(chat_req)

    first = await queue.get()
    second = await queue.get()

    assert first.kind == "chat", "Chat should be dequeued first (priority 0)"
    assert second.kind == "generate", "Research should be dequeued second (priority 2)"


# ── Test 7: Unauthorized table access ──

@pytest.mark.asyncio
async def test_unauthorized_table_denied(core_stack):
    """T7: Module cannot access tables not in its manifest."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    session_id = auth_resp["result"]["session_id"]

    req = {
        "id": "db-deny-001",
        "session_id": session_id,
        "method": "db.query",
        "params": {"table": "audit_log", "query": "SELECT * FROM audit_log"},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())

    assert resp["status"] == "error"
    assert resp["error"]["code"] == "PERMISSION_DENIED"

    writer.close()
    await writer.wait_closed()


# ── Test 8: Audit log populated ──

@pytest.mark.asyncio
async def test_audit_log_populated(core_stack):
    """T8: IPC requests generate audit log entries."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    session_id = auth_resp["result"]["session_id"]

    # Do a DB insert to generate audit activity
    req = {
        "id": "audit-001",
        "session_id": session_id,
        "method": "db.insert",
        "params": {
            "table": "feed_sources",
            "data": {
                "url": "https://test.example.com/rss",
                "name": "Audit Test Feed",
                "category": "test",
                "enabled": 1,
                "created_at": "2026-02-28T00:00:00Z",
            },
        },
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())
    assert resp["status"] == "ok"

    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.3)

    # Check audit log has entries (auth.register + db.insert at minimum)
    rows = await core_stack["db"].query(
        "audit_log", "SELECT * FROM audit_log", max_rows=100
    )
    assert len(rows) >= 2, f"Expected ≥2 audit entries, got {len(rows)}"


# ── Test 9: SQL identifier injection blocked ──

@pytest.mark.asyncio
async def test_sql_identifier_injection_blocked(core_stack):
    """T9: Table/column names with injection patterns are rejected."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    session_id = auth_resp["result"]["session_id"]

    req = {
        "id": "inject-001",
        "session_id": session_id,
        "method": "db.insert",
        "params": {
            "table": "feed_sources",
            "data": {
                "url": "https://test.example.com/rss",
                "name); DROP TABLE feed_sources; --": "gotcha",
                "created_at": "2026-02-28T00:00:00Z",
            },
        },
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())

    # Should fail — INTERNAL_ERROR from _validate_identifier
    assert resp["status"] == "error"

    writer.close()
    await writer.wait_closed()


# ── Test 10: Invalid token rejected ──

@pytest.mark.asyncio
async def test_invalid_token_rejected(core_stack):
    """T10: Auth with wrong token is denied."""
    reader, writer = await asyncio.open_unix_connection(
        core_stack["socket_path"]
    )
    req = {
        "id": "bad-auth-001",
        "module": "research",
        "method": "auth.register",
        "params": {"version": "0.2.0"},
        "token": "wrong-token-xyz",
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())

    assert resp["status"] == "error"
    assert resp["error"]["code"] == "PERMISSION_DENIED"

    writer.close()
    await writer.wait_closed()


# ── Test 11: LLM generate via IPC ──

@pytest.mark.asyncio
async def test_llm_generate_via_ipc(core_stack):
    """T11: Module can call llm.generate through IPC."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    session_id = auth_resp["result"]["session_id"]

    req = {
        "id": "llm-001",
        "session_id": session_id,
        "method": "llm.generate",
        "params": {"prompt": "Summarize this article about LLMs."},
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await reader.readline())

    assert resp["status"] == "ok"
    assert "SUMMARY:" in resp["result"]["text"]

    # Verify mock broker got the call
    assert len(core_stack["mock_llm"].calls) >= 1
    assert core_stack["mock_llm"].calls[-1]["kind"] == "generate"

    writer.close()
    await writer.wait_closed()


# ── Test 12: DB roundtrip (insert → query → update → verify) ──

@pytest.mark.asyncio
async def test_db_roundtrip(core_stack):
    """T12: Full DB lifecycle through IPC."""
    reader, writer, auth_resp = await _connect_and_auth(
        core_stack["socket_path"], core_stack["token"]
    )
    session_id = auth_resp["result"]["session_id"]

    # Insert
    req = {
        "id": "db-rt-001",
        "session_id": session_id,
        "method": "db.insert",
        "params": {
            "table": "feed_sources",
            "data": {
                "url": "https://roundtrip.example.com/feed",
                "name": "Roundtrip Feed",
                "category": "test",
                "enabled": 1,
                "created_at": "2026-02-28T00:00:00Z",
            },
        },
    }
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    insert_resp = json.loads(await reader.readline())
    assert insert_resp["status"] == "ok"
    row_id = insert_resp["result"]["row_id"]

    # Query
    req2 = {
        "id": "db-rt-002",
        "session_id": session_id,
        "method": "db.query",
        "params": {
            "table": "feed_sources",
            "query": "SELECT * FROM feed_sources WHERE id = ?",
            "params": [row_id],
        },
    }
    writer.write((json.dumps(req2) + "\n").encode())
    await writer.drain()
    query_resp = json.loads(await reader.readline())
    assert query_resp["status"] == "ok"
    assert query_resp["result"]["rows"][0]["name"] == "Roundtrip Feed"

    # Update
    req3 = {
        "id": "db-rt-003",
        "session_id": session_id,
        "method": "db.update",
        "params": {
            "table": "feed_sources",
            "data": {"name": "Updated Feed"},
            "where": "id = ?",
            "params": [row_id],
        },
    }
    writer.write((json.dumps(req3) + "\n").encode())
    await writer.drain()
    update_resp = json.loads(await reader.readline())
    assert update_resp["status"] == "ok"
    assert update_resp["result"]["updated"] == 1

    # Verify update
    req4 = {
        "id": "db-rt-004",
        "session_id": session_id,
        "method": "db.query",
        "params": {
            "table": "feed_sources",
            "query": "SELECT name FROM feed_sources WHERE id = ?",
            "params": [row_id],
        },
    }
    writer.write((json.dumps(req4) + "\n").encode())
    await writer.drain()
    verify_resp = json.loads(await reader.readline())
    assert verify_resp["result"]["rows"][0]["name"] == "Updated Feed"

    writer.close()
    await writer.wait_closed()

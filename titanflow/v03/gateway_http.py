"""HTTP gateway that forwards requests to the core IPC socket."""

from __future__ import annotations

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from titanflow.v03.kernel_clock import KernelClock
from titanflow.v03.trace_id import new_session_id, new_trace_id


class GatewayHTTPServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, core_socket: str) -> None:
        super().__init__((host, port), GatewayRequestHandler)
        self.core_socket = core_socket
        self.clock = KernelClock()


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server: GatewayHTTPServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/session":
            self._handle_session()
            return
        if self.path == "/rpc":
            self._handle_rpc()
            return
        self._send_json(404, {"error": "not_found"})

    def _handle_session(self) -> None:
        data = self._read_json()
        actor_id = data.get("actor_id", "")
        metadata = data.get("metadata", {})
        if not actor_id:
            self._send_json(400, {"error": "missing_actor_id"})
            return

        session_id = new_session_id()
        envelope = {
            "trace_id": new_trace_id(),
            "session_id": session_id,
            "actor_id": actor_id,
            "created_monotonic": self.server.clock.now(),
            "priority": 0,
            "module_id": "gateway",
            "method": "sessions.create",
            "payload": {"metadata": metadata},
            "stream": False,
        }

        if not self._send_envelope(envelope):
            self._send_json(502, {"error": "core_unavailable"})
            return

        self._send_json(200, {"session_id": session_id})

    def _handle_rpc(self) -> None:
        data = self._read_json()
        required = ["session_id", "actor_id", "module_id", "method", "payload", "priority"]
        missing = [key for key in required if key not in data]
        if missing:
            self._send_json(400, {"error": "missing_fields", "fields": missing})
            return

        envelope = {
            "trace_id": data.get("trace_id") or new_trace_id(),
            "session_id": data["session_id"],
            "actor_id": data["actor_id"],
            "created_monotonic": self.server.clock.now(),
            "priority": int(data.get("priority", 0)),
            "module_id": data["module_id"],
            "method": data["method"],
            "payload": data.get("payload") or {},
            "stream": bool(data.get("stream", False)),
        }

        if not self._send_envelope(envelope):
            self._send_json(502, {"error": "core_unavailable"})
            return

        self._send_json(200, {"status": "accepted", "trace_id": envelope["trace_id"]})

    def _send_envelope(self, envelope: dict[str, Any]) -> bool:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(2.0)
                sock.connect(self.server.core_socket)
                sock.sendall(json.dumps(envelope).encode() + b"\n")
                _ = sock.recv(4096)
            return True
        except OSError:
            return False

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except json.JSONDecodeError:
            return {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

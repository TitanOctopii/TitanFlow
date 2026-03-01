"""HTTP proxy for module outbound requests (Core side)."""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

import httpx

from titanflow.core.http import request_with_retry
from titanflow.core.config import HttpProxySettings

logger = logging.getLogger("titanflow.http_proxy")


def _domain_match(domain: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            if domain.endswith(pattern[1:]) or domain == pattern[2:]:
                return True
        elif domain == pattern:
            return True
    return False


class HttpProxy:
    def __init__(self, settings: HttpProxySettings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=settings.timeout_seconds)

    _ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

    async def request(self, url: str, method: str = "GET", headers=None, body: str | None = None) -> dict:
        if method.upper() not in self._ALLOWED_METHODS:
            raise ValueError(f"HTTP method {method!r} is not allowed. Must be one of: {', '.join(sorted(self._ALLOWED_METHODS))}")
        headers = headers or {}
        response = await request_with_retry(
            self._client,
            method,
            url,
            headers=headers,
            content=body,
            attempts=3,
        )
        raw = response.content
        truncated = False
        if self.settings.max_body_bytes and len(raw) > self.settings.max_body_bytes:
            raw = raw[: self.settings.max_body_bytes]
            truncated = True
        encoding = response.encoding or "utf-8"
        text = raw.decode(encoding, errors="replace")
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": text,
            "truncated": truncated,
        }

    # Private/internal IP networks for SSRF protection
    _PRIVATE_NETWORKS = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
    ]

    @staticmethod
    def _is_private_ip(host: str) -> bool:
        """Return True if host resolves to a private/internal IP address."""
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            # Not a raw IP — try resolving the hostname
            import socket
            try:
                resolved = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
                addrs = [ipaddress.ip_address(r[4][0]) for r in resolved]
            except socket.gaierror:
                return False
            return any(
                any(a in net for net in HttpProxy._PRIVATE_NETWORKS)
                for a in addrs
            )
        return any(addr in net for net in HttpProxy._PRIVATE_NETWORKS)

    @staticmethod
    def validate_domain(url: str, allowed_domains: list[str]) -> bool:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        if HttpProxy._is_private_ip(host):
            logger.warning("SSRF blocked: URL %r resolves to a private/internal address", url)
            return False
        return _domain_match(host, allowed_domains)

    async def close(self) -> None:
        await self._client.aclose()

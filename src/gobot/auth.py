"""Pairing-token access control for LAN mode.

* Requests from the Mac itself (loopback) are always allowed.
* With ``require_token`` (LAN mode) every other client must present the pairing cookie, obtained by
  POSTing the token from the QR code / pairing URL to ``/api/pair``, or an
  ``Authorization: Bearer <token>`` header.
* Static frontend files are public (they contain no data), so the phone can load the page and
  complete pairing; every ``/api`` route and websocket is protected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

COOKIE_NAME = "gobot_pair"
PUBLIC_API_PATHS = {"/api/pair", "/api/health"}

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def load_or_create_token(path: Path, regenerate: bool = False) -> str:
    if path.is_file() and not regenerate:
        tok = path.read_text().strip()
        if len(tok) >= 16:
            return tok
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(18)
    path.write_text(tok + "\n")
    os.chmod(path, 0o600)
    return tok


def cookie_value(token: str) -> str:
    return hashlib.sha256(("gobot-cookie:" + token).encode()).hexdigest()


def is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost") or bool(host and host.startswith("127."))


class TokenAuth:
    def __init__(self, token: str | None, require: bool):
        self.token = token
        self.require = require and bool(token)

    def check_token(self, candidate: str | None) -> bool:
        return bool(self.token and candidate and hmac.compare_digest(candidate, self.token))

    def check_scope(self, scope: Scope) -> bool:
        if not self.require:
            return True
        client = scope.get("client")
        if client and is_loopback(client[0]):
            return True
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and self.check_token(auth[7:].strip()):
            return True
        raw_cookie = headers.get("cookie")
        if raw_cookie and self.token:
            c: SimpleCookie = SimpleCookie()
            try:
                c.load(raw_cookie)
            except Exception:
                return False
            m = c.get(COOKIE_NAME)
            if m and hmac.compare_digest(m.value, cookie_value(self.token)):
                return True
        return False


def _host_only(value: str) -> str:
    """'example.local:8000' -> 'example.local'; '[::1]:8000' -> '::1'."""
    v = value.strip().lower()
    if v.startswith("["):
        return v[1 : v.find("]")] if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def _origin_host(origin: str) -> str:
    o = origin.strip().lower()
    if "://" in o:
        o = o.split("://", 1)[1]
    return o.split("/", 1)[0]


class AuthMiddleware:
    """Pairing check plus two browser protections for a server that trusts loopback:

    * Host allow-list (``allowed_hosts``): blocks DNS-rebinding pages that reach 127.0.0.1 under
      their own domain name.
    * Same-origin check for websockets and state-changing requests: a page on another site open in
      the Mac's browser cannot drive the API (cross-site websocket / form posts).
    """

    def __init__(self, app: ASGIApp, auth: TokenAuth, allowed_hosts: set[str] | None = None):
        self.app = app
        self.auth = auth
        self.allowed_hosts = {h.lower() for h in allowed_hosts} if allowed_hosts else None

    async def _reject(self, scope: Scope, send: Send, status: int, message: str, ws_code: int) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": ws_code})
            return
        body = json.dumps({"detail": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            host_header = headers.get("host", "")
            if self.allowed_hosts is not None and _host_only(host_header) not in self.allowed_hosts:
                await self._reject(scope, send, 421, "Unknown host name for this server.", 4421)
                return
            origin = headers.get("origin")
            unsafe = scope["type"] == "websocket" or scope.get("method", "GET") not in (
                "GET",
                "HEAD",
                "OPTIONS",
            )
            if origin and unsafe and origin != "null" and _origin_host(origin) != host_header.strip().lower():
                await self._reject(scope, send, 403, "Cross-site request refused.", 4403)
                return
            path: str = scope.get("path", "")
            protected = path.startswith("/api") and path not in PUBLIC_API_PATHS
            if protected and not self.auth.check_scope(scope):
                await self._reject(
                    scope,
                    send,
                    401,
                    "Not paired. Scan the QR code shown on the Mac (or open its pairing link).",
                    4401,
                )
                return
        await self.app(scope, receive, send)

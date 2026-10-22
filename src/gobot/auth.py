"""Access control: pairing token (LAN), sign-in sessions, and the Cloudflare tunnel.

* Requests from the Mac itself (loopback) are always allowed.
* With ``require_token`` (LAN mode) every other client must present the pairing cookie, obtained by
  POSTing the token from the QR code / pairing URL to ``/api/pair``, or an
  ``Authorization: Bearer <token>`` header, or a sign-in session cookie (see ``accounts.py``).
* Requests through the Cloudflare tunnel arrive on a separate loopback port that is wrapped in
  ``TunnelMarker``. cloudflared runs on the Mac, so these requests look local; the marker makes sure
  they are never trusted as loopback and never accept the pairing token: only a sign-in session.
* Static frontend files are public (they contain no data), so the phone can load the page and
  sign in or pair; every ``/api`` route and websocket is protected.
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

from gobot.accounts import SESSION_COOKIE, AccountStore

COOKIE_NAME = "gobot_pair"
PUBLIC_API_PATHS = {"/api/pair", "/api/health", "/api/auth/info", "/api/login", "/api/logout"}
TUNNEL_SCOPE_KEY = "gobot.tunnel"

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


def is_tunnel(scope: Scope) -> bool:
    return bool(scope.get(TUNNEL_SCOPE_KEY))


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def get_cookie(scope: Scope, name: str) -> str | None:
    raw = _headers(scope).get("cookie")
    if not raw:
        return None
    c: SimpleCookie = SimpleCookie()
    try:
        c.load(raw)
    except Exception:
        return None
    m = c.get(name)
    return m.value if m else None


class TunnelMarker:
    """Wraps the app for the port cloudflared connects to.

    Marks every request as coming through the tunnel and replaces the client address (always
    127.0.0.1, cloudflared itself) with the visitor's address from ``Cf-Connecting-Ip``, so loopback
    checks fail and sign-in rate limits apply per visitor.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            visitor = _headers(scope).get("cf-connecting-ip", "").strip() or "tunnel"
            if is_loopback(visitor):
                visitor = "tunnel"
            scope = {**scope, TUNNEL_SCOPE_KEY: True, "client": (visitor, 0)}
        await self.app(scope, receive, send)


class TokenAuth:
    def __init__(self, token: str | None, require: bool):
        self.token = token
        self.require = require and bool(token)

    def check_token(self, candidate: str | None) -> bool:
        return bool(self.token and candidate and hmac.compare_digest(candidate, self.token))

    def check_scope(self, scope: Scope) -> bool:
        if is_tunnel(scope):
            return False  # from the internet: a sign-in session is the only way in
        if not self.require:
            return True
        client = scope.get("client")
        if client and is_loopback(client[0]):
            return True
        auth = _headers(scope).get("authorization", "")
        if auth.lower().startswith("bearer ") and self.check_token(auth[7:].strip()):
            return True
        paired = get_cookie(scope, COOKIE_NAME)
        return bool(paired and self.token and hmac.compare_digest(paired, cookie_value(self.token)))


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

    def __init__(
        self,
        app: ASGIApp,
        auth: TokenAuth,
        allowed_hosts: set[str] | None = None,
        accounts: AccountStore | None = None,
    ):
        self.app = app
        self.auth = auth
        self.accounts = accounts
        self.allowed_hosts = {h.lower() for h in allowed_hosts} if allowed_hosts else None

    def allowed(self, scope: Scope) -> bool:
        if self.auth.check_scope(scope):
            return True
        return bool(self.accounts and self.accounts.valid_session(get_cookie(scope, SESSION_COOKIE)))

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
            headers = _headers(scope)
            host_header = headers.get("host", "")
            # The tunnel's public name is chosen by Cloudflare (quick tunnels) and the port is only
            # reachable through cloudflared, so the host allow-list does not apply there.
            check_host = self.allowed_hosts is not None and not is_tunnel(scope)
            if check_host and _host_only(host_header) not in self.allowed_hosts:  # type: ignore[operator]
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
            if protected and not self.allowed(scope):
                await self._reject(scope, send, 401, "Please sign in.", 4401)
                return
        await self.app(scope, receive, send)

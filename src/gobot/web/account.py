"""Sign-in, sign-out, the admin account settings, and the tunnel address."""

from __future__ import annotations

import asyncio
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from gobot.accounts import SESSION_COOKIE, SESSION_DAYS, AccountError, AccountStore
from gobot.auth import TokenAuth, is_loopback, is_tunnel


class LoginRequest(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=2000)


class AccountUpdate(BaseModel):
    currentPassword: str | None = Field(default=None, max_length=2000)
    username: str | None = Field(default=None, max_length=200)
    newPassword: str | None = Field(default=None, max_length=2000)


def is_mac(request: Request) -> bool:
    """The request comes from a browser on the Mac itself (not through the tunnel)."""
    client = request.client.host if request.client else None
    return not is_tunnel(request.scope) and is_loopback(client)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def attach(app: Any, accounts: AccountStore, auth: TokenAuth) -> None:
    router = APIRouter()

    def signed_in(request: Request) -> bool:
        return accounts.valid_session(request.cookies.get(SESSION_COOKIE))

    async def check_current_password(request: Request, password: str | None) -> None:
        key = _client_key(request)
        loop = asyncio.get_running_loop()
        wait = accounts.limiter.wait_seconds(key, loop.time())
        if wait > 0:
            raise HTTPException(429, f"Too many attempts. Try again in {int(wait) + 1} s.")
        ok = await asyncio.to_thread(accounts.check_password, password or "")
        if not ok:
            accounts.limiter.failed(key, loop.time())
            raise HTTPException(403, "The current password is wrong.")
        accounts.limiter.succeeded(key)

    @router.get("/api/auth/info")
    async def auth_info(request: Request) -> dict[str, Any]:
        mac = is_mac(request)
        session = signed_in(request)
        paired = auth.check_scope(request.scope)  # loopback, LAN pairing, or no LAN mode at all
        return {
            "required": not (session or paired),
            "signedIn": session,
            "user": accounts.username if (session or mac) else None,
            "mac": mac,
            "passwordSet": accounts.password_set,
            "pairing": auth.require and not is_tunnel(request.scope),
            "tunnel": is_tunnel(request.scope),
        }

    @router.post("/api/login")
    async def login(body: LoginRequest, request: Request) -> Response:
        if not accounts.password_set:
            raise HTTPException(
                409, "No password has been set yet. On the Mac, open Engine > Account and choose one."
            )
        key = _client_key(request)
        loop = asyncio.get_running_loop()
        wait = accounts.limiter.wait_seconds(key, loop.time())
        if wait > 0:
            raise HTTPException(429, f"Too many attempts. Try again in {int(wait) + 1} s.")
        ok = await asyncio.to_thread(accounts.check_login, body.username, body.password)
        if not ok:
            accounts.limiter.failed(key, loop.time())
            raise HTTPException(401, "Wrong username or password.")
        accounts.limiter.succeeded(key)
        value = accounts.create_session(request.headers.get("user-agent", ""))
        resp = JSONResponse({"signedIn": True, "user": accounts.username})
        resp.set_cookie(
            SESSION_COOKIE,
            value,
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            samesite="strict",
            # Cloudflare serves the tunnel over HTTPS even though cloudflared talks HTTP to us.
            secure=request.url.scheme == "https" or is_tunnel(request.scope),
        )
        return resp

    @router.post("/api/logout")
    async def logout(request: Request) -> Response:
        accounts.end_session(request.cookies.get(SESSION_COOKIE))
        resp = JSONResponse({"signedIn": False})
        resp.delete_cookie(SESSION_COOKIE, samesite="strict")
        return resp

    @router.get("/api/account")
    async def account(request: Request) -> dict[str, Any]:
        return {"username": accounts.username, "passwordSet": accounts.password_set, "mac": is_mac(request)}

    @router.post("/api/account")
    async def update_account(body: AccountUpdate, request: Request) -> dict[str, Any]:
        # On the Mac the owner is trusted (and may set the first password); elsewhere the current
        # password is required, even for a signed-in session.
        if not is_mac(request):
            if not accounts.password_set:
                raise HTTPException(403, "Set the first password on the Mac itself.")
            await check_current_password(request, body.currentPassword)
        username = body.username if body.username and body.username.strip() != accounts.username else None
        password = body.newPassword or None
        if username is None and password is None:
            raise HTTPException(400, "Nothing to change.")
        try:
            await asyncio.to_thread(
                accounts.set_credentials, username, password, request.cookies.get(SESSION_COOKIE)
            )
        except AccountError as e:
            raise HTTPException(400, str(e)) from e
        return {"username": accounts.username, "passwordSet": accounts.password_set}

    @router.post("/api/account/logout-all")
    async def logout_all(request: Request) -> dict[str, Any]:
        if not (is_mac(request) or signed_in(request)):
            raise HTTPException(403, "Sign in first.")
        n = accounts.end_all_sessions(keep_session=request.cookies.get(SESSION_COOKIE))
        return {"ended": n}

    @router.get("/api/tunnel")
    async def tunnel_info(request: Request) -> dict[str, Any]:
        t = request.app.state.tunnel
        return t.status() if t else {"mode": None, "state": "off", "url": None, "error": None}

    @router.get("/api/tunnel/qr.svg")
    async def tunnel_qr(request: Request) -> Response:
        t = request.app.state.tunnel
        if not (t and t.url):
            raise HTTPException(404, "Remote access is off. Start with ./scripts/start.sh --tunnel")
        import segno

        buf = io.BytesIO()
        segno.make(t.url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light="#fff")
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    app.include_router(router)

"""FastAPI application: API + websocket + built frontend, served from one origin."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ValidationError

from gobot import __version__, hardware
from gobot.auth import COOKIE_NAME, AuthMiddleware, TokenAuth, cookie_value, is_loopback
from gobot.engine.katago import EngineError, EngineUnavailable, QueryError
from gobot.engine.query import RULES, AnalyzeParams, Position
from gobot.engine.service import EngineService
from gobot.settings import Settings

log = logging.getLogger("gobot.app")

PRESETS = [
    {"id": "quick", "label": "Quick", "seconds": 2.0},
    {"id": "standard", "label": "Standard", "seconds": 6.0},
    {"id": "deep", "label": "Deep", "seconds": 20.0},
]
TOP_N_SENT = 10


class ModelSwitch(BaseModel):
    modelId: str


class PairRequest(BaseModel):
    token: str


def create_app(
    settings: Settings | None = None,
    token: str | None = None,
    start_engine: bool = True,
    pair_url: str | None = None,
) -> FastAPI:
    settings = settings or Settings()
    auth = TokenAuth(token, settings.require_token)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        svc = EngineService(settings)
        app.state.engine = svc
        warm: asyncio.Task[Any] | None = None
        if start_engine:

            async def _warm() -> None:
                try:
                    await svc.manager.ensure_started()
                except (EngineError, EngineUnavailable) as e:
                    log.warning("KataGo not started: %s", e)

            warm = asyncio.create_task(_warm())
        yield
        if warm is not None and not warm.done():
            warm.cancel()
        await svc.manager.shutdown()

    app = FastAPI(title="gobot", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = auth
    app.state.pair_url = pair_url

    # ------------------------------------------------------------------ basics
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        svc: EngineService = request.app.state.engine
        return {
            "app": {"version": __version__},
            "engine": svc.manager.status(),
            "hardware": {"summary": hardware.summary(svc.hardware), **svc.hardware},
            "presets": PRESETS,
            "rules": list(RULES),
            "lan": settings.lan,
            "https": settings.https,
            "recognition": request.app.state.recognition_status()
            if hasattr(request.app.state, "recognition_status")
            else None,
        }

    @app.get("/api/models")
    async def models(request: Request) -> dict[str, Any]:
        svc: EngineService = request.app.state.engine
        return {"models": svc.models_status(), "current": svc.manager.model_id}

    @app.post("/api/engine/model")
    async def switch_model(body: ModelSwitch, request: Request) -> dict[str, Any]:
        svc: EngineService = request.app.state.engine
        if svc.manifest.net(body.modelId) is None:
            raise HTTPException(404, f"unknown model {body.modelId}")
        try:
            await svc.manager.restart(body.modelId)
        except (EngineError, EngineUnavailable) as e:
            raise HTTPException(503, str(e)) from e
        return svc.manager.status()

    @app.post("/api/engine/restart")
    async def restart_engine(request: Request) -> dict[str, Any]:
        svc: EngineService = request.app.state.engine
        try:
            await svc.manager.restart()
        except (EngineError, EngineUnavailable) as e:
            raise HTTPException(503, str(e)) from e
        return svc.manager.status()

    # ------------------------------------------------------------------ pairing
    @app.post("/api/pair")
    async def pair(body: PairRequest, request: Request) -> Response:
        if not auth.require:
            return JSONResponse({"paired": True, "required": False})
        if not auth.check_token(body.token.strip()):
            raise HTTPException(403, "Wrong pairing code. Re-scan the QR code shown on the Mac.")
        resp = JSONResponse({"paired": True, "required": True})
        resp.set_cookie(
            COOKIE_NAME,
            cookie_value(auth.token or ""),
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return resp

    @app.get("/api/pair/status")
    async def pair_status() -> dict[str, Any]:
        # Reaching this endpoint means the middleware accepted us.
        return {"paired": True, "required": auth.require}

    @app.get("/api/pair/info")
    async def pair_info(request: Request) -> dict[str, Any]:
        client = request.client.host if request.client else None
        if not is_loopback(client):
            raise HTTPException(403, "Pairing details are only shown on the Mac itself.")
        return {"url": request.app.state.pair_url, "lan": settings.lan, "https": settings.https}

    @app.get("/api/pair/qr.svg")
    async def pair_qr(request: Request) -> Response:
        client = request.client.host if request.client else None
        if not is_loopback(client):
            raise HTTPException(403, "Pairing details are only shown on the Mac itself.")
        url = request.app.state.pair_url
        if not url:
            raise HTTPException(404, "LAN mode is off. Restart with ./scripts/start.sh --lan")
        import io

        import segno

        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light="#fff")
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ analysis websocket
    @app.websocket("/api/analyze")
    async def analyze_ws(ws: WebSocket) -> None:
        await ws.accept()
        svc: EngineService = ws.app.state.engine
        client = uuid.uuid4().hex[:8]
        tasks: set[asyncio.Task[Any]] = set()
        current: dict[str, str | None] = {"requestId": None}
        send_lock = asyncio.Lock()

        async def send(msg: dict[str, Any]) -> None:
            async with send_lock:
                with contextlib.suppress(Exception):
                    await ws.send_json(msg)

        async def run(request_id: str, pos: Position, params: AnalyzeParams) -> None:
            mgr = svc.manager
            if not (mgr.proc and mgr.proc.alive and mgr.proc.state == "ready"):
                await send({"type": "engine-starting", "requestId": request_id})
            try:
                gen = mgr.analyze(client, pos, params, top_n=TOP_N_SENT)
                async with contextlib.aclosing(gen):  # type: ignore[type-var]
                    async for res in gen:
                        if current["requestId"] != request_id:
                            continue  # superseded on our side too
                        res["requestId"] = request_id
                        if res.get("type") in ("partial", "final"):
                            res["engine"] = {
                                "modelId": mgr.model_id,
                                "version": mgr.status()["version"],
                                "backend": mgr.probe.get("backend"),
                            }
                        await send(res)
            except QueryError as e:
                await send({"type": "error", "requestId": request_id, "kind": "query", "message": str(e)})
            except EngineUnavailable as e:
                await send(
                    {"type": "error", "requestId": request_id, "kind": "unavailable", "message": str(e)}
                )
            except EngineError as e:
                await send({"type": "error", "requestId": request_id, "kind": "engine", "message": str(e)})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - defensive
                log.exception("analysis failed")
                await send({"type": "error", "requestId": request_id, "kind": "internal", "message": str(e)})

        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                rid = str(msg.get("requestId") or uuid.uuid4().hex[:8])
                if kind == "analyze":
                    try:
                        pos = Position.model_validate(msg.get("position") or {})
                        params = AnalyzeParams.model_validate(msg.get("params") or {})
                    except ValidationError as e:
                        await send(
                            {"type": "error", "requestId": rid, "kind": "invalid", "message": _short(e)}
                        )
                        continue
                    current["requestId"] = rid
                    t = asyncio.create_task(run(rid, pos, params))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
                elif kind == "cancel":
                    if current["requestId"] == rid:
                        await svc.manager.cancel(client)
                elif kind == "ping":
                    await send({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            await svc.manager.cancel(client)
            for t in tasks:
                t.cancel()

    # ------------------------------------------------------------------ photos
    from gobot.web.photos import attach as attach_photos

    attach_photos(app, settings)

    # ------------------------------------------------------------------ frontend
    _mount_frontend(app, settings.frontend_dist)
    app.add_middleware(AuthMiddleware, auth=auth, allowed_hosts=allowed_hosts(settings))  # type: ignore[arg-type]
    return app


def allowed_hosts(settings: Settings) -> set[str]:
    """Host names this server answers to (DNS-rebinding protection). Extend with GOBOT_ALLOWED_HOSTS."""
    import os

    from gobot.netinfo import lan_ip, local_hostname

    hosts = {"localhost", "127.0.0.1", "::1"}
    if settings.lan or settings.host not in ("127.0.0.1", "localhost", "::1"):
        ip = lan_ip()
        if ip:
            hosts.add(ip)
        name = local_hostname().lower()
        hosts |= {name, name.removesuffix(".local")}
    hosts |= {h.strip().lower() for h in os.environ.get("GOBOT_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return hosts


def _short(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return str(e)
    first = errs[0]
    loc = ".".join(str(x) for x in first.get("loc", ()))
    return f"{loc}: {first.get('msg')}" if loc else str(first.get("msg"))


NOT_BUILT = """<!doctype html><meta charset=utf-8><title>gobot</title>
<body style="font-family:system-ui;max-width:40em;margin:3em auto;padding:0 1em">
<h1>Frontend not built</h1><p>Run <code>npm --prefix frontend ci && npm --prefix frontend run build</code>
(or <code>./scripts/setup_mac.sh</code>) and reload. The API is running.</p></body>"""


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404)
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(NOT_BUILT)
        if path:
            candidate = (dist / path).resolve()
            if candidate.is_file() and dist.resolve() in candidate.parents:
                headers = (
                    {"Cache-Control": "public, max-age=31536000, immutable"}
                    if path.startswith("assets/")
                    else {}
                )
                return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

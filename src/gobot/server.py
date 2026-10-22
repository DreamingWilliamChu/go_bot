"""Start the web server(s): main app, an HTTP helper for CA download in HTTPS mode, and with
``--tunnel`` a loopback port for cloudflared plus the cloudflared process itself."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import html
import os
import signal
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from gobot.accounts import AccountStore
from gobot.app import create_app
from gobot.auth import TunnelMarker, load_or_create_token
from gobot.netinfo import lan_ip, local_hostname
from gobot.settings import Settings


def _setup_page(https_url: str) -> str:
    u = html.escape(https_url)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>gobot - phone setup</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;max-width:36em;margin:1.5em auto;padding:0 1em;line-height:1.5}}
a.btn{{display:inline-block;background:#1f6f43;color:#fff;padding:.7em 1.1em;border-radius:.6em;text-decoration:none;font-weight:600}}
li{{margin:.4em 0}} code{{background:#eee;padding:0 .3em;border-radius:.3em}}</style></head><body>
<h1>Phone setup for live camera</h1>
<p>This is only needed once per phone, and only for the <em>live camera</em> view. Taking a photo with the
camera app or choosing one from the library works without it.</p>
<ol>
<li><a class="btn" href="/ca.crt">Download the gobot certificate</a><br>
Safari asks to allow a configuration profile download: tap <b>Allow</b>.</li>
<li>Open <b>Settings</b> &rarr; <b>Profile Downloaded</b> (or General &rarr; VPN &amp; Device Management) &rarr;
<b>Install</b>.</li>
<li>Open <b>Settings &rarr; General &rarr; About &rarr; Certificate Trust Settings</b> and switch on full trust
for <b>gobot local CA</b>.</li>
<li>Open the app: <a href="{u}">{u}</a> (scan the pairing QR code on the Mac to sign in).</li>
</ol>
<p>The certificate authority was created on your Mac, never leaves your network and can only vouch for
this Mac's local address. You can remove it at any time in Settings &rarr; General &rarr; VPN &amp; Device Management.</p>
</body></html>"""


def _helper_app(settings: Settings, https_url: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/setup")
    async def setup() -> HTMLResponse:
        return HTMLResponse(_setup_page(https_url))

    @app.get("/ca.crt")
    async def ca_crt() -> Response:
        data = (settings.certs_dir / "ca.der").read_bytes()
        return Response(
            data,
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="gobot-local-ca.crt"'},
        )

    @app.get("/ca.pem")
    async def ca_pem() -> Response:
        return Response((settings.certs_dir / "ca.pem").read_bytes(), media_type="application/x-pem-file")

    @app.get("/{path:path}")
    async def other(path: str) -> RedirectResponse:
        return RedirectResponse("/setup")

    return app


def banner(lines: list[str]) -> None:
    w = max(len(s) for s in lines) + 4
    print("+" + "-" * (w - 2) + "+")
    for s in lines:
        print("| " + s.ljust(w - 4) + " |")
    print("+" + "-" * (w - 2) + "+")


def print_qr(url: str) -> None:
    try:
        import segno

        segno.make(url, error="m").terminal(compact=True)
    except Exception:  # pragma: no cover - cosmetic
        pass


def _tunnel_ready(url: str) -> None:
    banner(
        [
            f"Remote access (Cloudflare tunnel):  {url}",
            "Anyone with this address sees the sign-in page; only your username and password let them in.",
        ]
    )
    print("Scan to open it on your phone (works on mobile data too):")
    print_qr(url)
    sys.stdout.flush()


def serve(settings: Settings, args: argparse.Namespace) -> int:
    settings.lan = bool(args.lan)
    settings.https = bool(args.https)
    settings.port = int(args.port)
    settings.host = args.host or ("0.0.0.0" if settings.lan else "127.0.0.1")
    settings.require_token = settings.lan
    settings.tunnel_port = int(getattr(args, "tunnel_port", 0) or settings.port + 1)
    from gobot.tunnel import plan_tunnel

    plan = plan_tunnel(
        settings.root,
        settings.data_dir,
        flag=bool(getattr(args, "tunnel", False)),
        no_tunnel=bool(getattr(args, "no_tunnel", False)),
        hostname=getattr(args, "tunnel_hostname", None),
    )
    settings.tunnel = plan.enabled
    tunnel_token, tunnel_host, shared_host = plan.token, plan.hostname, plan.shared_host
    no_tunnel = bool(getattr(args, "no_tunnel", False))
    if args.model:
        settings.model = args.model
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.lan:
        print(
            "Refusing to bind a non-loopback address without --lan (which enables the pairing token).",
            file=sys.stderr,
        )
        return 2

    if getattr(args, "lan_ip", None):
        os.environ["GOBOT_LAN_IP"] = args.lan_ip
    token = (
        load_or_create_token(settings.data_dir / "token", regenerate=args.new_token) if settings.lan else None
    )
    scheme = "https" if settings.https else "http"
    ip = lan_ip() if settings.lan else None
    ssl_kwargs: dict[str, str] = {}
    if settings.https:
        from gobot.certs import ensure_certs

        paths = ensure_certs(settings.certs_dir, ip)
        ssl_kwargs = {"ssl_certfile": str(paths["cert"]), "ssl_keyfile": str(paths["key"])}

    pair_url = None
    lines = [f"gobot is running:  {scheme}://localhost:{settings.port}/  (on this Mac)"]
    if settings.lan:
        if ip is None:
            lines.append("WARNING: no private LAN IPv4 address found; is Wi-Fi connected?")
        else:
            pair_url = f"{scheme}://{ip}:{settings.port}/#pair={token}"
            lines += [
                f"Phone (same Wi-Fi):  {scheme}://{ip}:{settings.port}/",
                f"Pairing link:        {pair_url}",
                f"Also reachable as    {scheme}://{local_hostname()}:{settings.port}/",
            ]
            if settings.https:
                lines.append(
                    f"First time on a phone? Open http://{ip}:{args.http_helper_port}/setup to trust the certificate."
                )
        lines.append("Only devices with the pairing code can use the API. Keep this on trusted networks.")
    elif not settings.tunnel:
        lines.append("Localhost only. Use ./scripts/start.sh --lan to connect your phone.")
    accounts = AccountStore(settings.account_json)
    if settings.tunnel and tunnel_token:
        lines += [
            f"Remote access:  https://{tunnel_host}/  (your Cloudflare tunnel)"
            if tunnel_host
            else "Remote access: starting your named Cloudflare tunnel.",
            f"Cloudflare must send that hostname to http://127.0.0.1:{settings.tunnel_port}",
            "Run the app with this tunnel on one device at a time (Cloudflare splits traffic otherwise).",
        ]
    elif settings.tunnel:
        lines.append("Remote access: starting a Cloudflare quick tunnel; its address is printed below.")
    if shared_host and not tunnel_token and not no_tunnel:
        lines += [
            f"This repository is set up for remote access at https://{shared_host}/,",
            "but this device has no tunnel token yet. Add it once with:  uv run gobot tunnel token",
        ]
    if settings.tunnel and not accounts.password_set:
        lines += [
            "NO SIGN-IN PASSWORD YET: nobody can sign in remotely until you set one.",
            f"Set it on this Mac: {scheme}://localhost:{settings.port}/ > Engine > Account",
            "(or run: uv run gobot account set)",
        ]
    banner(lines)
    if pair_url:
        print("Scan with the iPhone camera:")
        print_qr(pair_url)
    sys.stdout.flush()

    app = create_app(
        settings,
        token=token,
        start_engine=not args.no_engine_warmup,
        pair_url=pair_url,
        accounts=accounts,
    )
    main_cfg = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=False,
        **ssl_kwargs,  # type: ignore[arg-type]
    )
    servers = [uvicorn.Server(main_cfg)]
    if settings.https and settings.lan:
        https_url = f"https://{ip}:{settings.port}/" if ip else f"https://localhost:{settings.port}/"
        helper_cfg = uvicorn.Config(
            _helper_app(settings, https_url),
            host=settings.host,
            port=int(args.http_helper_port),
            log_level="warning",
        )
        servers.append(uvicorn.Server(helper_cfg))

    tunnel = None
    if settings.tunnel:
        from gobot.tunnel import Tunnel

        local = f"http://127.0.0.1:{settings.tunnel_port}"
        tunnel = Tunnel(local, token=tunnel_token, hostname=tunnel_host, on_url=_tunnel_ready)
        app.state.tunnel = tunnel
        tunnel_cfg = uvicorn.Config(
            TunnelMarker(app),  # type: ignore[arg-type]
            host="127.0.0.1",  # only cloudflared on this Mac connects here
            port=settings.tunnel_port,
            log_level="info",
            lifespan="off",  # the main server runs the app's startup/shutdown
            proxy_headers=False,
        )
        servers.append(uvicorn.Server(tunnel_cfg))
        if not tunnel.start():
            print(f"Remote access is OFF: {tunnel.error}", file=sys.stderr)
        elif tunnel.mode == "named" and not tunnel.url:
            print("Named Cloudflare tunnel starting (run: uv run gobot tunnel setup --hostname <name>).")

    async def run_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    if tunnel:
        # uvicorn re-raises SIGTERM after its own shutdown, which would end the process before the
        # `finally` below and leave cloudflared running. Turn SIGTERM/SIGHUP (closing the terminal)
        # into a normal interrupt so the tunnel always stops with the server.
        def _interrupt(signum: int, frame: object) -> None:
            raise KeyboardInterrupt

        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, _interrupt)
        atexit.register(tunnel.stop)

    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run_all())
    finally:
        if tunnel:
            tunnel.stop()
    return 0

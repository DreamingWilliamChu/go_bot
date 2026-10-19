"""Start the web server(s): main app, plus an HTTP helper for CA download in HTTPS mode."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import html
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from gobot.app import create_app
from gobot.auth import load_or_create_token
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


def serve(settings: Settings, args: argparse.Namespace) -> int:
    settings.lan = bool(args.lan)
    settings.https = bool(args.https)
    settings.port = int(args.port)
    settings.host = args.host or ("0.0.0.0" if settings.lan else "127.0.0.1")
    settings.require_token = settings.lan
    if args.model:
        settings.model = args.model
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.lan:
        print(
            "Refusing to bind a non-loopback address without --lan (which enables the pairing token).",
            file=sys.stderr,
        )
        return 2

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
    else:
        lines.append("Localhost only. Use ./scripts/start.sh --lan to connect your phone.")
    banner(lines)
    if pair_url:
        try:
            import segno

            print("Scan with the iPhone camera:")
            segno.make(pair_url, error="m").terminal(compact=True)
        except Exception:  # pragma: no cover - cosmetic
            pass
    sys.stdout.flush()

    app = create_app(settings, token=token, start_engine=not args.no_engine_warmup, pair_url=pair_url)
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

    async def run_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_all())
    return 0

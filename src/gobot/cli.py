"""Command line entry point: ``gobot serve|account|tunnel|models|hardware|bench|eval|certs``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from gobot import hardware
from gobot.settings import Settings


def _cmd_models(args: argparse.Namespace, settings: Settings) -> int:
    from gobot.engine.models import NetInfo, download, installed_networks, load_manifest, print_progress

    manifest = load_manifest(settings.manifest_path)
    dirs = [settings.models_dir, *settings.extra_model_dirs()]
    if args.action == "list":
        installed = {n.id: p for n, p in installed_networks(manifest, dirs)}
        for n in manifest.networks:
            mark = "installed" if n.id in installed else "-"
            print(f"{n.id:14s} {n.role:20s} {n.size / 1e6:7.1f} MB  elo {n.kata1_elo}  {mark}")
        for r in manifest.recognition:
            mark = "installed" if (settings.models_dir / r.file).is_file() else "-"
            print(f"{r.id:14s} {'recognition':20s} {r.size / 1e6:7.1f} MB  {mark}")
        return 0
    # download
    targets: list[NetInfo] = []
    if args.ids:
        for i in args.ids:
            found = manifest.net(i) or manifest.recog(i)
            if found is None:
                print(f"unknown model id {i}", file=sys.stderr)
                return 2
            targets.append(found)
    else:
        for n in manifest.networks:
            if args.all or n.default_download or (args.max and n.role == "max"):
                # Homebrew already ships the b18 net; reuse it instead of downloading again.
                if n.homebrew_bundled and any((d / n.file).is_file() for d in settings.extra_model_dirs()):
                    print(f"{n.id}: provided by Homebrew, skipping download")
                    continue
                targets.append(n)
        if not args.no_recognition:
            targets += [r for r in manifest.recognition if r.default_download or args.all]
    ok = True
    for n in targets:
        try:
            p = download(n, settings.models_dir, progress=print_progress(n.file))
            print(f"OK  {n.id} -> {p} (sha256 verified)")
        except Exception as e:  # report and continue with the others
            ok = False
            print(f"ERR {n.id}: {e}", file=sys.stderr)
    return 0 if ok else 1


def _cmd_hardware(args: argparse.Namespace, settings: Settings) -> int:
    info = hardware.detect()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.hardware_json.write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps(info, indent=2))
    print("Summary:", hardware.summary(info))
    return 0


def _cmd_account(args: argparse.Namespace, settings: Settings) -> int:
    import getpass

    from gobot.accounts import AccountError, AccountStore

    store = AccountStore(settings.account_json)
    if args.action == "show":
        print(f"username: {store.username}")
        print(f"password: {'set' if store.password_set else 'NOT SET'}")
        return 0
    if args.action == "logout-all":
        print(f"Signed out {store.end_all_sessions()} session(s).")
        return 0
    pw = getpass.getpass(f"New password for {args.username or store.username}: ")
    if getpass.getpass("Repeat it: ") != pw:
        print("The passwords do not match.", file=sys.stderr)
        return 1
    try:
        store.set_credentials(args.username, pw)
    except AccountError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Saved. Sign in as '{store.username}'. Every other signed-in device was signed out.")
    return 0


def _cmd_tunnel(args: argparse.Namespace, settings: Settings) -> int:
    import getpass

    from gobot import tunnel as tn

    token_file = settings.data_dir / "tunnel_token"
    cfg_path = tn.tunnel_config_path(settings.root)
    if args.action == "show":
        cfg = tn.load_tunnel_config(settings.root)
        tok = tn.load_tunnel_token(settings.data_dir)
        tid = None
        if tok:
            try:
                tid = tn.parse_tunnel_token(tok)[1]
            except ValueError:
                tid = "INVALID TOKEN"
        print(f"hostname:   {cfg.get('hostname') or '(none: quick tunnels with --tunnel)'}  [{cfg_path}]")
        print(f"autostart:  {cfg.get('autostart', True) if cfg else '-'}")
        print(f"token:      {'tunnel ' + tid if tid else 'none on this device'}")
        print(f"cloudflared: {tn.find_cloudflared() or 'not installed (brew install cloudflared)'}")
        return 0
    if args.action == "forget-token":
        token_file.unlink(missing_ok=True)
        print("Removed this device's tunnel token.")
        return 0
    if args.action == "create":
        import subprocess

        binary = tn.find_cloudflared()
        if not binary:
            print("cloudflared is not installed. Run: brew install cloudflared", file=sys.stderr)
            return 1
        try:
            host = tn.normalize_hostname(
                args.hostname or tn.load_tunnel_config(settings.root).get("hostname") or ""
            )
        except ValueError:
            print("Pass --hostname, e.g. --hostname go.example.com", file=sys.stderr)
            return 2
        if args.hostname:
            p = tn.save_tunnel_config(settings.root, host, autostart=not args.no_autostart)
            print(f"Saved {p} (commit it so every checkout uses this hostname; it contains no secret).")
        if not tn.origin_cert_present():
            print("Opening Cloudflare in the browser: sign in, then choose the domain of", host)
            if subprocess.run([binary, "tunnel", "login"]).returncode != 0:
                print("cloudflared login did not finish.", file=sys.stderr)
                return 1
        try:
            token = tn.create_named_tunnel(binary, args.name, host)
        except (tn.TunnelSetupError, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        tn.save_tunnel_token(settings.data_dir, token)
        print(f"Saved the tunnel token to {token_file} (this device only; never committed).")
        print("Start the app with ./scripts/start.sh and remote access starts by itself.")
        return 0
    if args.action == "setup":
        if not args.hostname:
            print("Pass --hostname, e.g. --hostname go.example.com", file=sys.stderr)
            return 2
        try:
            p = tn.save_tunnel_config(settings.root, args.hostname, autostart=not args.no_autostart)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"Saved {p} (commit it so every checkout uses this hostname; it contains no secret).")
        if tn.load_tunnel_token(settings.data_dir):
            return 0
    raw = getpass.getpass("Paste the Cloudflare tunnel token (input hidden): ")
    try:
        token, tunnel_id = tn.parse_tunnel_token(raw)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    tn.save_tunnel_token(settings.data_dir, token)
    print(f"Saved the token for tunnel {tunnel_id} to {token_file} (this device only; never committed).")
    print("Start the app with ./scripts/start.sh and remote access starts by itself.")
    return 0


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    from gobot.server import serve

    return serve(settings, args)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gobot", description="Local Go photo recognition + KataGo analysis")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default=None, help="bind address (default 127.0.0.1; 0.0.0.0 with --lan)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--lan", action="store_true", help="allow phones on the local network (token required)")
    s.add_argument(
        "--https", action="store_true", help="serve HTTPS with a local CA (needed for live camera)"
    )
    s.add_argument(
        "--http-helper-port", type=int, default=8080, help="plain-HTTP port for CA download (--https)"
    )
    s.add_argument("--new-token", action="store_true", help="generate a new pairing token")
    s.add_argument("--lan-ip", default=None, help="LAN address to advertise (default: auto-detect)")
    s.add_argument("--model", default=None, help="model id from models/manifest.json or a .bin.gz path")
    s.add_argument("--no-engine-warmup", action="store_true", help="do not start KataGo until first request")
    s.add_argument(
        "--tunnel", action="store_true", help="remote access through a Cloudflare tunnel (sign-in required)"
    )
    s.add_argument(
        "--tunnel-port", type=int, default=0, help="loopback port for cloudflared (default port+1)"
    )
    s.add_argument("--tunnel-hostname", default=None, help="public hostname of a named tunnel (for display)")
    s.add_argument(
        "--no-tunnel", action="store_true", help="do not start the tunnel set up in config/tunnel.json"
    )

    t = sub.add_parser(
        "tunnel", help="set up remote access through your own domain (named Cloudflare tunnel)"
    )
    t.add_argument("action", choices=["create", "setup", "token", "show", "forget-token"])
    t.add_argument("--hostname", default=None, help="public hostname, e.g. go.example.com (create, setup)")
    t.add_argument("--name", default="gobot", help="tunnel name in your Cloudflare account (create)")
    t.add_argument(
        "--no-autostart", action="store_true", help="only start the tunnel with --tunnel (create, setup)"
    )

    a = sub.add_parser("account", help="set the sign-in username/password, or sign out every device")
    a.add_argument("action", choices=["set", "show", "logout-all"])
    a.add_argument("--username", default=None, help="new username (set)")

    m = sub.add_parser("models", help="list or download networks")
    m.add_argument("action", choices=["list", "download"])
    m.add_argument("ids", nargs="*", help="specific model ids (default: the default set)")
    m.add_argument("--max", action="store_true", help="also download the maximum-strength network")
    m.add_argument("--all", action="store_true", help="download every network in the manifest")
    m.add_argument("--no-recognition", action="store_true", help="skip the moku recognition model")

    sub.add_parser("hardware", help="detect hardware and write data/hardware.json")

    b = sub.add_parser("bench", help="benchmark installed models (see scripts/benchmark.sh)")
    b.add_argument("--models", nargs="*", default=None)
    b.add_argument("--quick", action="store_true", help="short run (fewer thread counts/positions)")
    b.add_argument("--no-select", action="store_true", help="do not update data/engine.json")
    b.add_argument("--modes", nargs="*", default=None, help="metal modes to test: gpu mux ane")
    b.add_argument("--visits", type=int, default=None, help="visits per position for katago benchmark")

    e = sub.add_parser("eval", help="evaluate photo recognition on downloaded test sets")
    e.add_argument("--sets", nargs="*", default=None)
    e.add_argument("--detectors", nargs="*", default=None)
    e.add_argument("--limit", type=int, default=None)
    e.add_argument("--out", default=None)

    c = sub.add_parser("certs", help="create/refresh the local HTTPS certificate authority")
    c.add_argument("--force-ca", action="store_true")

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = Settings()
    if args.cmd == "serve":
        return _cmd_serve(args, settings)
    if args.cmd == "models":
        return _cmd_models(args, settings)
    if args.cmd == "account":
        return _cmd_account(args, settings)
    if args.cmd == "tunnel":
        return _cmd_tunnel(args, settings)
    if args.cmd == "hardware":
        return _cmd_hardware(args, settings)
    if args.cmd == "bench":
        from gobot.bench.benchmark import run_cli

        return run_cli(args, settings)
    if args.cmd == "eval":
        from gobot.eval.recognition import run_cli as eval_cli

        return eval_cli(args, settings)
    if args.cmd == "certs":
        from gobot.certs import ensure_certs
        from gobot.netinfo import lan_ip

        paths = ensure_certs(settings.certs_dir, lan_ip(), force_ca=args.force_ca)
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Command line entry point: ``gobot serve|models|hardware|bench|eval|certs``."""

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
    s.add_argument("--model", default=None, help="model id from models/manifest.json or a .bin.gz path")
    s.add_argument("--no-engine-warmup", action="store_true", help="do not start KataGo until first request")

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

"""Benchmark installed KataGo networks on this computer and pick a default.

    ./scripts/benchmark.sh            # or: uv run gobot bench [--quick] [--models ...] [--modes gpu mux]

For each installed network (and, on Apple Silicon, each Metal mode):
 1. ``katago benchmark`` over several search-thread counts -> visits/s, best thread count
 2. real analysis queries through gobot's engine manager on representative positions for each
    thinking-time preset -> visits reached, elapsed time
 3. peak resident memory of the KataGo process (psutil sampling)

Selection rule (an explicit assumption, see docs/MODELS.md): among networks that loaded and ran,
pick the one with the highest kata1 Elo that reaches at least ``MIN_STANDARD_VISITS`` visits on
the 19x19 middle-game position in the Standard preset and whose peak RSS stays below
``MAX_RSS_FRACTION`` of RAM. The fastest network is kept as the light fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from gobot import hardware
from gobot.bench.positions import positions
from gobot.engine.config import default_engine_settings, render_config
from gobot.engine.katago import EngineManager, EngineSpec, probe_binary
from gobot.engine.models import installed_networks, load_manifest
from gobot.engine.query import AnalyzeParams
from gobot.settings import Settings

MIN_STANDARD_VISITS = 1000
MAX_RSS_FRACTION = 0.35
PRESETS = {"quick": 2.0, "standard": 6.0, "deep": 20.0}

LINE_RE = re.compile(
    r"numSearchThreads\s*=\s*(\d+):\s*(\d+)\s*/\s*(\d+) positions, visits/s = ([\d.]+) nnEvals/s = ([\d.]+)"
    r".*?avgBatchSize = ([\d.]+)"
)


@dataclass
class PeakRss:
    pid_root: int
    peak: int = 0
    stop: bool = False

    def run(self) -> None:
        try:
            root = psutil.Process(self.pid_root)
        except psutil.Error:
            return
        while not self.stop:
            total = 0
            try:
                procs = [root, *root.children(recursive=True)]
                for p in procs:
                    if "katago" in (p.name() or "").lower() or p.pid == self.pid_root:
                        total = max(total, p.memory_info().rss)
            except psutil.Error:
                pass
            self.peak = max(self.peak, total)
            time.sleep(0.2)


def _bench_config(cfg: dict[str, Any], backend: str, threads: int) -> str:
    c = dict(cfg)
    c["num_search_threads"] = threads
    if backend == "metal" and c.get("metal_mode") == "mux":
        c["nn_max_batch_size"] = max(1, threads // 2)
    else:
        c["nn_max_batch_size"] = threads
    return render_config(c, backend) + f"numSearchThreads = {threads}\n"


def katago_benchmark(
    binary: str, model: Path, cfg_path: Path, threads: list[int], visits: int, timeout: float
) -> dict[str, Any]:
    cmd = [
        binary,
        "benchmark",
        "-model",
        str(model),
        "-config",
        str(cfg_path),
        "-v",
        str(visits),
        "-t",
        ",".join(map(str, threads)),
        "-no-half-batch-size-test",
        "-no-server-thread-test",
    ]
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    mon = PeakRss(proc.pid)
    th = threading.Thread(target=mon.run, daemon=True)
    th.start()
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        out += "\n[gobot] benchmark timed out"
    mon.stop = True
    th.join(timeout=2)
    results: dict[int, dict[str, float]] = {}
    for m in LINE_RE.finditer(out.replace("\r", "\n")):
        t, done, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if done == total:
            results[t] = {
                "visits_per_s": float(m.group(4)),
                "nn_evals_per_s": float(m.group(5)),
                "avg_batch": float(m.group(6)),
            }
    ok = bool(results) and proc.returncode == 0
    best = max(results, key=lambda t: results[t]["visits_per_s"]) if results else None
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "threads": {str(k): v for k, v in sorted(results.items())},
        "best_threads": best,
        "best_visits_per_s": results[best]["visits_per_s"] if best else None,
        "peak_rss_mb": round(mon.peak / 2**20, 1),
        "seconds": round(time.monotonic() - t0, 1),
        "log_tail": "\n".join(out.strip().splitlines()[-12:]) if not ok else "",
    }


async def analysis_timings(
    binary: str, model: Path, cfg_path: Path, presets: dict[str, float], quick: bool
) -> dict[str, Any]:
    spec = EngineSpec(binary=binary, model_path=model, config_path=cfg_path, startup_timeout=900)
    mgr = EngineManager(spec_factory=lambda _m: spec)
    t0 = time.monotonic()
    await mgr.ensure_started()
    startup = time.monotonic() - t0
    assert mgr.proc and mgr.proc.proc
    mon = PeakRss(mgr.proc.proc.pid)
    th = threading.Thread(target=mon.run, daemon=True)
    th.start()
    rows = []
    try:
        poss = positions()
        if quick:
            poss = [p for p in poss if "middle game" in p.name]
        for bp in poss:
            for preset, seconds in presets.items():
                if quick and preset == "deep":
                    continue
                last = None
                async for r in mgr.analyze(
                    "bench", bp.position, AnalyzeParams(max_time=seconds, include_ownership=True)
                ):
                    last = r
                assert last is not None
                rows.append(
                    {
                        "position": bp.name,
                        "preset": preset,
                        "seconds": seconds,
                        "visits": last.get("rootVisits"),
                        "elapsed": last.get("elapsed"),
                        "top_move": (last.get("candidates") or [{}])[0].get("gtp"),
                    }
                )
    finally:
        mon.stop = True
        th.join(timeout=2)
        await mgr.shutdown()
    return {"startup_seconds": round(startup, 2), "queries": rows, "peak_rss_mb": round(mon.peak / 2**20, 1)}


def select(results: list[dict[str, Any]], mem_bytes: int) -> dict[str, Any]:
    usable = []
    for r in results:
        if not r.get("analysis") or not r["benchmark"].get("ok"):
            continue
        std = [
            q
            for q in r["analysis"]["queries"]
            if q["preset"] == "standard" and "19x19 middle" in q["position"]
        ]
        visits = std[0]["visits"] if std else 0
        rss = max(r["benchmark"].get("peak_rss_mb", 0), r["analysis"].get("peak_rss_mb", 0)) * 2**20
        r["standard_visits_19x19_middle"] = visits
        r["fits_memory"] = rss <= MAX_RSS_FRACTION * mem_bytes
        r["meets_visits"] = visits >= MIN_STANDARD_VISITS
        usable.append(r)
    if not usable:
        return {"model_id": None, "reason": "no network ran successfully"}
    ok = [r for r in usable if r["fits_memory"] and r["meets_visits"]]
    pool = ok or [r for r in usable if r["fits_memory"]] or usable
    best = max(pool, key=lambda r: (r["elo"] or 0, r["benchmark"]["best_visits_per_s"] or 0))
    fastest = max(usable, key=lambda r: r["benchmark"]["best_visits_per_s"] or 0)
    reason = (
        f"highest kata1 Elo with >= {MIN_STANDARD_VISITS} visits in Standard on 19x19 and RSS <= {int(MAX_RSS_FRACTION * 100)}% RAM"
        if ok
        else "no network met the visit target; chose the strongest that fits in memory"
    )
    return {
        "model_id": best["model_id"],
        "mode": best["mode"],
        "threads": best["benchmark"]["best_threads"],
        "light_model_id": fastest["model_id"],
        "reason": reason,
    }


def markdown(report: dict[str, Any]) -> str:
    hw = report["hardware"]
    lines = [
        f"# KataGo benchmark - {report['date']}",
        "",
        f"* Computer: {hardware.summary(hw)}",
        f"* KataGo: {report['katago']['version']} ({report['katago']['backend']})",
        "* Measured on this machine. Elo is kata1 rating-game Elo from katagotraining.org (not measured here).",
        "",
        "| Network | Mode | kata1 Elo | Best threads | visits/s | Standard (6 s) visits, 19x19 middle | Peak RSS MB | Startup s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        b = r["benchmark"]
        a = r.get("analysis") or {}
        std = [
            q for q in a.get("queries", []) if q["preset"] == "standard" and "19x19 middle" in q["position"]
        ]
        lines.append(
            f"| {r['model_id']} | {r['mode']} | {r['elo'] or '–'} | {b.get('best_threads') or '–'} | "
            f"{b.get('best_visits_per_s') or 'failed'} | {std[0]['visits'] if std else '–'} | "
            f"{max(b.get('peak_rss_mb', 0), a.get('peak_rss_mb', 0))} | {a.get('startup_seconds', '–')} |"
        )
    sel = report["selection"]
    lines += [
        "",
        f"**Selected default:** `{sel.get('model_id')}` (mode {sel.get('mode')}, threads {sel.get('threads')}) - {sel.get('reason')}.",
        f"**Light fallback:** `{sel.get('light_model_id')}`.",
        "",
        "## Per-preset analysis",
        "",
        "| Network | Mode | Position | Preset | Visits | Elapsed s |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        for q in (r.get("analysis") or {}).get("queries", []):
            lines.append(
                f"| {r['model_id']} | {r['mode']} | {q['position']} | {q['preset']} | {q['visits']} | {q['elapsed']} |"
            )
    return "\n".join(lines) + "\n"


def run_cli(args: argparse.Namespace, settings: Settings) -> int:
    hw = hardware.detect()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.hardware_json.write_text(json.dumps(hw, indent=2) + "\n")
    binary = settings.find_katago()
    if not binary:
        print("KataGo not found. Install it with: brew install katago")
        return 2
    probe = probe_binary(binary)
    backend = probe["backend_kind"]
    manifest = load_manifest(settings.manifest_path)
    inst = installed_networks(manifest, [settings.models_dir, *settings.extra_model_dirs()])
    if args.models:
        inst = [(n, p) for n, p in inst if n.id in args.models]
    if not inst:
        print("No networks installed. Run ./scripts/download_models.sh first.")
        return 2
    quick = bool(args.quick)
    if backend == "metal":
        modes = args.modes or (["gpu"] if quick else ["gpu", "mux"])
        threads = [8, 16] if quick else [4, 8, 12, 16, 24]
    else:
        modes = ["none"]
        cpus = int(hw.get("logical_cpus") or 4)
        threads = sorted({max(1, cpus // 2), cpus})
    visits = args.visits or (300 if quick else 600)
    presets = {k: v for k, v in PRESETS.items() if not (quick and k == "deep")}
    base_cfg = default_engine_settings(hw, backend)
    out_dir = settings.data_dir / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Hardware: {hardware.summary(hw)}")
    print(
        f"KataGo {probe['version']} backend {probe['backend']}; networks: {', '.join(n.id for n, _ in inst)}; modes: {modes}"
    )
    results = []
    for net, path in sorted(inst, key=lambda t: t[0].kata1_elo or 0):
        for mode in modes:
            cfg = dict(base_cfg, metal_mode=mode)
            bench_cfg = out_dir / f"bench_{net.id}_{mode}.cfg"
            bench_cfg.write_text(_bench_config(cfg, backend, max(threads)))
            print(
                f"\n== {net.id} [{mode}] katago benchmark (threads {threads}, {visits} visits/position) ...",
                flush=True,
            )
            b = katago_benchmark(binary, path, bench_cfg, threads, visits, timeout=3600)
            print(f"   visits/s by threads: {b['threads']} peak RSS {b['peak_rss_mb']} MB ({b['seconds']} s)")
            entry: dict[str, Any] = {
                "model_id": net.id,
                "file": net.file,
                "elo": net.kata1_elo,
                "mode": mode,
                "benchmark": b,
            }
            if b["ok"] and b["best_threads"]:
                cfg_best = dict(
                    cfg,
                    num_search_threads=b["best_threads"],
                    nn_max_batch_size=max(1, b["best_threads"] // 2) if mode == "mux" else b["best_threads"],
                )
                acfg = out_dir / f"analysis_{net.id}_{mode}.cfg"
                acfg.write_text(render_config(cfg_best, backend))
                print("   analysis queries per preset ...", flush=True)
                try:
                    entry["analysis"] = asyncio.run(analysis_timings(binary, path, acfg, presets, quick))
                    entry["katago_settings"] = {
                        k: cfg_best[k] for k in ("num_search_threads", "nn_max_batch_size", "metal_mode")
                    }
                    for q in entry["analysis"]["queries"]:
                        print(
                            f"   {q['position']:32s} {q['preset']:8s} visits {q['visits']:>7} in {q['elapsed']}s"
                        )
                except Exception as e:  # report, keep going
                    entry["analysis_error"] = str(e)
                    print(f"   analysis failed: {e}")
            else:
                print(f"   benchmark failed:\n{b['log_tail']}")
            results.append(entry)
    sel = select(results, int(hw["memory_bytes"]))
    report = {
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "hardware": hw,
        "katago": {"binary": binary, "version": probe["version"], "backend": probe["backend"]},
        "quick": quick,
        "results": results,
        "selection": sel,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out_dir / "report.md").write_text(markdown(report))
    print("\n" + markdown(report))
    if sel.get("model_id") and not args.no_select:
        chosen = next(r for r in results if r["model_id"] == sel["model_id"] and r["mode"] == sel["mode"])
        eng = settings.load_engine_json()
        eng.update(
            {
                "model_id": sel["model_id"],
                "katago": chosen.get("katago_settings", {}),
                "per_model": {
                    r["model_id"]: r.get("katago_settings", {})
                    for r in results
                    if r.get("katago_settings") and r["mode"] == sel["mode"]
                },
                "fallbacks": [
                    m for m in (sel.get("light_model_id"), "b18c384-conv") if m and m != sel["model_id"]
                ],
                "selected_by": f"benchmark {report['date']}: {sel['reason']}",
            }
        )
        settings.save_engine_json(eng)
        print(f"Saved default network '{sel['model_id']}' to {settings.engine_json}")
    print(f"Report: {out_dir / 'report.md'}")
    return 0

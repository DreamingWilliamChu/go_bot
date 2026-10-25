"""``gobot variant ...``: bench, pipeline run/resume/status, evaluate, targeted, models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gobot.settings import Settings


def add_parser(sub: Any) -> None:
    v = sub.add_parser("variant", help="bonus-turn Go: train and evaluate the variant network")
    vs = v.add_subparsers(dest="vcmd", required=True)
    b = vs.add_parser("bench", help="measure self-play and training speed for a preset")
    b.add_argument("--preset", default="local_small")
    p = vs.add_parser("pipeline", help="self-play -> train -> gate -> promote, then evaluate and install")
    p.add_argument("action", choices=["run", "resume", "status"])
    p.add_argument("--preset", default="smoke_cpu")
    p.add_argument("--run", default=None, help="run directory name under data/variant/runs")
    p.add_argument("--no-katago", action="store_true", help="no KataGo teacher or KataGo-evaluator baseline")
    p.add_argument(
        "--reserve-eval-minutes", type=float, default=None, help="time kept for the final evaluation"
    )
    e = vs.add_parser("evaluate", help="play a paired match between two agents")
    e.add_argument(
        "a", help="agent: random | greedy | heuristic | katago | net:<model.onnx> | blind:<model.onnx>"
    )
    e.add_argument("b")
    e.add_argument("--pairs", type=int, default=20)
    e.add_argument("--visits", type=int, default=64)
    e.add_argument("--size", type=int, default=11)
    e.add_argument("--workers", type=int, default=8)
    t = vs.add_parser("targeted", help="build the 11x11 targeted suite, or score a network on it")
    t.add_argument("--src", default="tests/fixtures/variant/targeted.json")
    t.add_argument("--size", type=int, default=11)
    t.add_argument("--eval", default=None, metavar="MODEL", help="score this model.onnx on the 11x11 suite")
    t.add_argument("--visits", type=int, default=192)
    vs.add_parser("models", help="list installed variant networks")


def _katago(settings: Settings) -> dict[str, Any] | None:
    from gobot.engine.service import EngineService
    from gobot.variant.sources import katago_evaluator_spec

    return katago_evaluator_spec(EngineService(settings))


def _agent(spec: str, visits: int, settings: Settings) -> Any:
    from gobot.variant.training.evaluate import AgentSpec

    if spec in ("random", "greedy"):
        return AgentSpec(spec, spec)
    if spec == "heuristic":
        return AgentSpec("search+heuristic", "search", "heuristic", visits=visits)
    if spec == "katago":
        return AgentSpec("search+katago", "search", "katago", visits=visits, katago=_katago(settings))
    kind, _, path = spec.partition(":")
    if kind in ("net", "blind") and path:
        return AgentSpec(spec, "search", "net", path, visits, blind=kind == "blind")
    raise SystemExit(f"unknown agent {spec!r}")


def run_cli(args: argparse.Namespace, settings: Settings) -> int:
    if args.vcmd == "bench":
        from gobot.variant.training.bench import run
        from gobot.variant.training.pipeline import load_preset

        res = run(load_preset(args.preset), settings.variant_dir / "bench" / f"{args.preset}.json")
        print(json.dumps(res, indent=2, default=float))
        return 0
    if args.vcmd == "pipeline":
        from gobot.variant.training.pipeline import load_preset, main_run

        if args.action == "status":
            if not args.run:
                runs = sorted((settings.variant_dir / "runs").glob("*"))
                for r in runs:
                    st = r / "run_state.json"
                    s = json.loads(st.read_text()) if st.is_file() else {}
                    print(
                        f"{r.name:40s} phase={s.get('phase')} generation={s.get('generation')} best={s.get('best')} games={s.get('games')}"
                    )
                return 0
            print((settings.variant_dir / "runs" / args.run / "run_state.json").read_text())
            return 0
        if args.action == "resume" and not args.run:
            print("resume needs --run", file=sys.stderr)
            return 2
        katago = None if args.no_katago else _katago(settings)
        preset = load_preset(args.preset)
        reserve = (
            args.reserve_eval_minutes
            if args.reserve_eval_minutes is not None
            else (15 if preset.name == "local_small" else 2)
        ) * 60
        return main_run(settings, args.preset, args.run, args.action == "resume", katago, reserve)
    if args.vcmd == "evaluate":
        from gobot.variant.training.evaluate import play_match

        a, b = _agent(args.a, args.visits, settings), _agent(args.b, args.visits, settings)
        if a.name == b.name:
            b.name += "#2"
        allocs = [(0, 0), (1, 1), (2, 2), (3, 3), (2, 1), (1, 2), (3, 1), (1, 3)]
        match = play_match(a, b, args.pairs, args.size, allocs, workers=args.workers)
        print(json.dumps(match.public(), indent=2))
        return 0
    if args.vcmd == "targeted":
        from gobot.variant.training.targeted import embedded_suite, embedded_suite_path, run_targeted

        if args.eval:
            report = run_targeted(embedded_suite_path(settings.root, args.size), args.eval, args.visits)
            print(json.dumps(report, indent=2))
            return 0

        src = json.loads(Path(args.src).read_text())
        kept = embedded_suite(src["positions"], args.size)
        out = embedded_suite_path(settings.root, args.size)
        out.write_text(json.dumps({"from": args.src, "positions": kept}, indent=1) + "\n")
        print(f"{len(kept)} positions -> {out}")
        return 0
    if args.vcmd == "models":
        from gobot.variant.models.registry import list_models

        for m in list_models(settings.variant_dir):
            print(json.dumps(m.public()))
        return 0
    return 2

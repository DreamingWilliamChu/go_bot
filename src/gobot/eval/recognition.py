"""Recognition evaluation: ``uv run gobot eval [--sets ...] [--detectors ...]``.

For every photo with ground truth it runs the full pipeline (automatic corners) and reports:
  * grid found: share of photos where a grid was accepted (the rest ask the user for corners)
  * corner error: worst corner distance in grid cells (where corner labels exist)
  * perfect boards / errors per board among accepted photos (all 4 rotations allowed where the
    labels are orientation-free)
  * flagged: points shown with '?' for the user to check; silent errors: wrong AND not flagged
It also evaluates stones with the labelled corners ("oracle corners") to separate the two stages.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from gobot.eval import datasets
from gobot.eval.datasets import Sample
from gobot.settings import Settings
from gobot.vision import stones
from gobot.vision.geometry import order_corners
from gobot.vision.pipeline import Recognizer

ALL_SETS = ("tengen", "moku-v3-test", "goban_data_set", "synthetic")


def load_sets(root: Path, names: Iterable[str]) -> dict[str, list[Sample]]:
    out: dict[str, list[Sample]] = {}
    for name in names:
        if name == "tengen" and (root / "tengen-go").is_dir():
            out[name] = list(datasets.tengen(root / "tengen-go"))
        elif name == "moku-v3-test" and (root / "moku-v3" / "test.parquet").is_file():
            out[name] = [
                s for s in datasets.moku_parquet(root / "moku-v3" / "test.parquet", "test") if not s.note
            ]
        elif name == "goban_data_set" and (root / "goban_data_set").is_dir():
            out[name] = list(datasets.goban_data_set(root / "goban_data_set"))[::4]  # every 4th photo
        elif name == "synthetic":
            from gobot.eval.synthetic import synthetic

            out[name] = list(synthetic())
    return out


def corner_error(pred: np.ndarray, gt: np.ndarray, n: int) -> float:
    gt = order_corners(gt)
    cell = np.mean([np.linalg.norm(gt[i] - gt[(i + 1) % 4]) for i in range(4)]) / (n - 1)
    return float(max(min(np.linalg.norm(p - g) for g in gt) for p in np.asarray(pred)) / cell)


def compare(pred: np.ndarray, truth: np.ndarray, flagged: np.ndarray, rotation_free: bool) -> tuple[int, int]:
    best = None
    for k in range(4) if rotation_free else [0]:
        t = np.rot90(truth, k)
        wrong = t != pred
        cand = (int(wrong.sum()), int((wrong & ~flagged).sum()))
        if best is None or cand < best:
            best = cand
    assert best is not None
    return best


def evaluate(
    sets: dict[str, list[Sample]], recognizers: dict[str, Recognizer], limit: int | None = None
) -> dict:
    report: dict = {"sets": {}}
    for set_name, samples in sets.items():
        samples = samples[:limit] if limit else samples
        rs: dict = {}
        for det_name, rec in recognizers.items():
            m: dict[str, Any] = {
                "photos": 0,
                "grid_found": 0,
                "corner_errors": [],
                "boards": 0,
                "perfect": 0,
                "errors": 0,
                "silent": 0,
                "flagged": 0,
                "points": 0,
                "seconds": [],
            }
            for s in samples:
                rgb = s.load()
                t0 = time.perf_counter()
                r = rec.recognize(rgb, s.size)
                m["seconds"].append(time.perf_counter() - t0)
                m["photos"] += 1
                if r.status != "ok":
                    continue
                m["grid_found"] += 1
                if s.grid_corners is not None:
                    m["corner_errors"].append(corner_error(np.array(r.corners), s.grid_corners, s.size))
                if s.board is not None and r.board is not None:
                    pred = np.array(r.board)
                    flagged = np.zeros_like(pred, bool)
                    for x, y in r.uncertain:
                        flagged[y, x] = True
                    e, silent = compare(pred, s.board, flagged, s.rotation_free)
                    m["boards"] += 1
                    m["perfect"] += e == 0
                    m["errors"] += e
                    m["silent"] += silent
                    m["flagged"] += int(flagged.sum())
                    m["points"] += s.size * s.size
            rs[det_name] = summarize(m)
        # oracle corners: stones only
        o = {"boards": 0, "perfect": 0, "errors": 0, "points": 0}
        for s in samples:
            if s.board is None or s.grid_corners is None:
                continue
            sr = stones.classify(s.load(), order_corners(s.grid_corners), s.size)
            e, _ = compare(sr.labels, s.board, np.zeros_like(sr.labels, bool), s.rotation_free)
            o["boards"] += 1
            o["perfect"] += e == 0
            o["errors"] += e
            o["points"] += s.size * s.size
        rs["oracle-corners/classical-stones"] = {
            "boards": o["boards"],
            "perfect_boards": o["perfect"],
            "perfect_rate": o["perfect"] / o["boards"] if o["boards"] else None,
            "errors_per_board": o["errors"] / o["boards"] if o["boards"] else None,
            "point_accuracy": 1 - o["errors"] / o["points"] if o["points"] else None,
        }
        report["sets"][set_name] = {"photos": len(samples), "results": rs}
    return report


def summarize(m: dict) -> dict:
    ce = np.array(m["corner_errors"])
    return {
        "photos": m["photos"],
        "grid_found": m["grid_found"],
        "grid_found_rate": m["grid_found"] / m["photos"] if m["photos"] else None,
        "corner_labelled": int(len(ce)),
        "corners_within_half_cell": float(np.mean(ce < 0.5)) if len(ce) else None,
        "corner_error_median_cells": float(np.median(ce)) if len(ce) else None,
        "wrong_grids_accepted": int(np.sum(ce >= 0.5)) if len(ce) else None,
        "boards": m["boards"],
        "perfect_boards": m["perfect"],
        "perfect_rate": m["perfect"] / m["boards"] if m["boards"] else None,
        "errors_per_board": m["errors"] / m["boards"] if m["boards"] else None,
        "point_accuracy": 1 - m["errors"] / m["points"] if m["points"] else None,
        "flagged_per_board": m["flagged"] / m["boards"] if m["boards"] else None,
        "silent_errors_per_board": m["silent"] / m["boards"] if m["boards"] else None,
        "seconds_median": float(np.median(m["seconds"])) if m["seconds"] else None,
    }


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{100 * v:.1f}%"


def _num(v: float | None, d: int = 2) -> str:
    return "–" if v is None else f"{v:.{d}f}"


def markdown(report: dict) -> str:
    lines = [
        "| Set | Detector | Photos | Grid found | Corners ≤ ½ cell | Wrong grids accepted | Boards scored | "
        "Perfect boards | Point accuracy | Errors / board | Flagged '?' / board | Silent errors / board | s / photo |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for set_name, s in report["sets"].items():
        for det, r in s["results"].items():
            if det.startswith("oracle"):
                continue
            lines.append(
                f"| {set_name} | {det} | {r['photos']} | {r['grid_found']} ({_pct(r['grid_found_rate'])}) | "
                f"{_pct(r['corners_within_half_cell'])} (n={r['corner_labelled']}) | {r['wrong_grids_accepted'] if r['wrong_grids_accepted'] is not None else '–'} | "
                f"{r['boards']} | {r['perfect_boards']} ({_pct(r['perfect_rate'])}) | {_pct(r['point_accuracy'])} | "
                f"{_num(r['errors_per_board'])} | {_num(r['flagged_per_board'])} | {_num(r['silent_errors_per_board'])} | "
                f"{_num(r['seconds_median'], 1)} |"
            )
    lines += [
        "",
        "Stones only, with the labelled corners (isolates the stone classifier):",
        "",
        "| Set | Boards | Perfect | Point accuracy | Errors / board |",
        "|---|---|---|---|---|",
    ]
    for set_name, s in report["sets"].items():
        r = s["results"].get("oracle-corners/classical-stones")
        if r and r["boards"]:
            lines.append(
                f"| {set_name} | {r['boards']} | {r['perfect_boards']} ({_pct(r['perfect_rate'])}) | "
                f"{_pct(r['point_accuracy'])} | {_num(r['errors_per_board'])} |"
            )
    return "\n".join(lines) + "\n"


def run_cli(args: argparse.Namespace, settings: Settings) -> int:
    root = settings.data_dir / "testsets"
    names = args.sets or list(ALL_SETS)
    sets = load_sets(root, names)
    missing = [n for n in names if n not in sets]
    if missing:
        print(f"Missing test sets (run scripts/fetch_test_data.sh): {', '.join(missing)}")
    moku = settings.models_dir / "moku-v4.onnx"
    dets = args.detectors or ["classical", "classical+moku"]
    recs: dict[str, Recognizer] = {}
    if "classical" in dets:
        recs["classical"] = Recognizer(None)
    if "classical+moku" in dets:
        if moku.is_file():
            recs["classical+moku"] = Recognizer(moku)
        else:
            print("moku-v4.onnx not downloaded; skipping classical+moku")
    report = evaluate(sets, recs, limit=args.limit)
    md = markdown(report)
    print(md)
    out = Path(args.out) if args.out else settings.data_dir / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "recognition.json").write_text(json.dumps(report, indent=2))
    (out / "recognition.md").write_text(md)
    print(f"Wrote {out / 'recognition.md'}")
    return 0

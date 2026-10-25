"""Targeted positions with exact, solver-proven answers (docs/VARIANT_TRAINING.md, "Targeted suite").

Small boards with a few empty points and a short placement cap (``max_placements``: the game is scored
exactly as the board stands at the cap) are solved by ``gobot.variant.search.exact`` under different
public bonus states. A position is kept when the bonus state changes the correct move and the correct
moves are a small fraction of the legal ones, so choosing well is evidence of handling bonuses, not luck.

Categories:

* ``three_move``  - the mover has a fresh 3-move bonus turn; the winning moves differ from a normal turn
* ``survival``    - the opponent's next turn is a certain bonus (P = 1); the mover must prepare for it
* ``same_board``  - the same board without any bonus to come: the best moves differ from ``survival``
* ``no_bonus``    - ordinary alternation (no bonuses at all) with a small set of winning moves
* ``unequal``     - the opponent's next turn is a bonus with P = 1/2 (one bonus over two eligible turns)
  while the mover has none

``python -m gobot.variant.training.targeted --out tests/fixtures/variant/targeted.json`` regenerates the
committed fixture (fixed seed); ``check_agent`` scores an agent on it.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gobot.variant.config import VariantConfig
from gobot.variant.rules import BLACK, WHITE, Board
from gobot.variant.search.exact import TooLarge, solve
from gobot.variant.state import VariantState, imported_state

CATEGORIES = ("three_move", "survival", "same_board", "no_bonus", "unequal")
HORIZON = 4
CAP = 3


def make_state(rec: dict[str, Any]) -> VariantState:
    board = Board.from_rows(rec["rows"])
    rem = tuple(rec["remaining"])
    cfg = VariantConfig(
        size=board.n,
        komi=rec["komi"],
        black_bonuses=rem[0],
        white_bonuses=rem[1],
        moves_per_bonus=rec.get("k", 3),
        horizon=rec.get("horizon", HORIZON),
        max_placements=rec.get("cap", CAP),
    )
    return imported_state(
        cfg,
        board,
        rec["toMove"],
        (rem[0], rem[1]),
        (rec["turnsStarted"][0], rec["turnsStarted"][1]),
        bonus_active=rec.get("bonusActive", False),
        moves_left=rec.get("movesLeft", 1),
    )


def _random_board(rng: random.Random, n: int, empties: int) -> Board | None:
    for _ in range(200):
        cells = [rng.choice((BLACK, WHITE)) for _ in range(n * n)]
        for i in rng.sample(range(n * n), empties):
            cells[i] = 0
        b = Board.from_stones(n, [(i % n, i // n, c) for i, c in enumerate(cells) if c])
        if not b.dead_chains():
            return b
    return None


def _record(
    category: str, b: Board, mover: int, komi: float, remaining: list[int], started: list[int], **kw: Any
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "category": category,
        "rows": b.rows(),
        "toMove": mover,
        "komi": komi,
        "remaining": remaining,
        "turnsStarted": started,
        "horizon": HORIZON,
        "cap": CAP,
        "k": 3,
        **kw,
    }
    sol = solve(make_state(rec), max_nodes=40_000)
    rec["best"] = sorted(sol.best)
    rec["value"] = sol.value
    rec["legal"] = len(sol.action_values)
    return rec


def _good_for_mover(rec: dict[str, Any]) -> bool:
    """The mover can win, but only with a few of its legal moves."""
    v = rec["value"] if rec["toMove"] == BLACK else 1 - rec["value"]
    return v >= 0.75 and len(rec["best"]) <= max(1, rec["legal"] // 3)


def _discriminates(bonus: dict[str, Any], calm: dict[str, Any]) -> bool:
    """Choosing well needs the bonus: some move that is best without it is wrong with it."""
    return bool(set(calm["best"]) - set(bonus["best"])) and len(bonus["best"]) <= max(1, bonus["legal"] // 2)


def _mover_value(rec: dict[str, Any]) -> float:
    return float(rec["value"] if rec["toMove"] == BLACK else 1 - rec["value"])


def generate(per_category: int = 6, seed: int = 20261025, budget_s: float = 600.0) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    found: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
    t0 = time.monotonic()
    while time.monotonic() - t0 < budget_s and any(len(v) < per_category for v in found.values()):
        b = _random_board(rng, 5, rng.randint(4, 7))
        if b is None:
            continue
        mover = rng.choice((BLACK, WHITE))
        oi = 2 - mover  # index of the opponent
        komi = rng.choice((0.5, 1.5, 2.5, 3.5, -0.5, -1.5, -2.5))
        started = [2, 2]
        started[oi] = 3  # the opponent has one eligible turn left (H = 4)
        try:
            forced = [0, 0]
            forced[oi] = 1
            calm = _record("no_bonus", b, mover, komi, [0, 0], started)
            surv = _record("survival", b, mover, komi, forced, started)
            if (
                _discriminates(surv, calm)
                and _mover_value(surv) > 0
                and len(found["survival"]) < per_category
            ):
                found["survival"].append(surv)
                if _discriminates(calm, surv) and len(found["same_board"]) < per_category:
                    found["same_board"].append({**calm, "category": "same_board"})
            elif _good_for_mover(calm) and len(found["no_bonus"]) < per_category:
                found["no_bonus"].append(calm)
            three = _record("three_move", b, mover, komi, [0, 0], [2, 2], bonusActive=True, movesLeft=3)
            normal = _record("no_bonus", b, mover, komi, [0, 0], [2, 2])
            if (
                _discriminates(three, normal)
                and _mover_value(three) > _mover_value(normal)
                and len(found["three_move"]) < per_category
            ):
                found["three_move"].append(three)
            if len(found["unequal"]) < per_category:
                half = [0, 0]
                half[oi] = 1
                st = [2, 2]
                st[oi] = 2  # two eligible turns left: P = 1/2
                uneq = _record("unequal", b, mover, komi, half, st)
                calm2 = _record("no_bonus", b, mover, komi, [0, 0], st)
                if _discriminates(uneq, calm2) and _mover_value(uneq) > 0:
                    found["unequal"].append(uneq)
        except TooLarge:
            continue
    return [r for c in CATEGORIES for r in found[c]]


# Hand-built positions (still solver-verified when added): a White group whose eye space is a
# straight three. A single Black move at the vital point (1, 0) kills it; three consecutive Black
# moves capture it outright.
HANDMADE_ROWS = ["...OX", "OOOOX", "XXXXX", "X.X.X", "XXXXX"]


def handmade() -> list[dict[str, Any]]:
    b = Board.from_rows(HANDMADE_ROWS)
    return [
        # Black's certain 3-move bonus is coming: White survives only by taking the vital point now.
        _record("survival", b, WHITE, 9.5, [1, 0], [3, 3], horizon=HORIZON),
        # Black has the 3-move bonus now: filling the three liberties captures the group.
        _record("three_move", b, BLACK, 9.5, [0, 0], [2, 2], bonusActive=True, movesLeft=3, cap=4),
    ]


def _good_for_mover_soft(rec: dict[str, Any]) -> bool:
    return len(rec["best"]) <= max(1, rec["legal"] // 3)


def check_agent(
    positions: list[dict[str, Any]], choose: Callable[[VariantState], int]
) -> dict[str, dict[str, float]]:
    """Fraction of positions per category where ``choose`` picks a solver-optimal move, with the
    accuracy a uniformly random legal move would get."""
    out: dict[str, dict[str, float]] = {}
    for c in CATEGORIES:
        recs = [r for r in positions if r["category"] == c]
        if not recs:
            continue
        hits = sum(1 for r in recs if choose(make_state(r)) in set(r["best"]))
        chance = sum(len(r["best"]) / r["legal"] for r in recs) / len(recs)
        out[c] = {"n": len(recs), "correct": hits, "accuracy": hits / len(recs), "random_baseline": chance}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate the solver-verified targeted suite")
    p.add_argument("--out", required=True)
    p.add_argument("--per-category", type=int, default=6)
    p.add_argument("--seed", type=int, default=20261025)
    p.add_argument("--budget", type=float, default=600.0)
    a = p.parse_args(argv)
    sys.setrecursionlimit(20000)
    recs = handmade() + generate(a.per_category, a.seed, a.budget)
    Path(a.out).write_text(json.dumps({"seed": a.seed, "positions": recs}, indent=1) + "\n")
    counts = {c: sum(1 for r in recs if r["category"] == c) for c in CATEGORIES}
    print(f"wrote {len(recs)} positions to {a.out}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------- 11x11 versions for trained networks
# The trained network only knows 11x11. Each 5x5 position is placed in the top-left corner of an 11x11
# board whose remainder is settled: a Black group (top right, with the wall at x = 5) and a White group
# (bottom, with the wall at y = 5), each with two single-point eyes. The walls touch the corner
# position, so the answer is re-solved exactly on the 11x11 board and the position is kept only when
# the bonus state still changes the correct moves.
EYES = {(8, 1): BLACK, (8, 4): BLACK, (2, 8): WHITE, (8, 8): WHITE}


def embed_rows(rows: list[str], size: int = 11) -> list[str]:
    grid = [["." for _ in range(size)] for _ in range(size)]
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            grid[y][x] = ch
    for y in range(size):
        for x in range(size):
            if x < 5 and y < 5:
                continue
            if (x, y) in EYES:
                continue
            black = (x >= 5 and y <= 5) and not (y == 5 and x < 5)
            grid[y][x] = "X" if black else "O"
    return ["".join(r) for r in grid]


def _outside_lead(size: int) -> float:
    rows = embed_rows(["....."] * 5, size)
    b = sum(r.count("X") for r in rows) + sum(1 for c in EYES.values() if c == BLACK)
    w = sum(r.count("O") for r in rows) + sum(1 for c in EYES.values() if c == WHITE)
    return float(b - w)


def embed(rec: dict[str, Any], size: int = 11) -> dict[str, Any]:
    out = {
        **rec,
        "rows": embed_rows(rec["rows"], size),
        "komi": rec["komi"] + _outside_lead(size),
        "embedded_from": rec["rows"],
    }
    sol = solve(make_state(out), max_nodes=200_000)
    out.update(best=sorted(sol.best), value=sol.value, legal=len(sol.action_values))
    return out


def embedded_suite(positions: list[dict[str, Any]], size: int = 11) -> list[dict[str, Any]]:
    """11x11 versions of the suite that still discriminate (the bonus changes the correct moves)."""
    kept = []
    for rec in positions:
        try:
            e = embed(rec, size)
            if rec["category"] == "no_bonus":
                if len(e["best"]) <= max(1, e["legal"] // 3):
                    kept.append(e)
                continue
            calm_rec = {**rec, "remaining": [0, 0], "bonusActive": False, "movesLeft": 1}
            calm = embed(calm_rec, size)
            if _discriminates(e, calm):
                kept.append(e)
        except TooLarge:
            continue
    return kept


def embedded_suite_path(root: Path, size: int) -> Path:
    return root / "tests" / "fixtures" / "variant" / f"targeted_{size}.json"


def run_targeted(path: Path, model_path: str, visits: int) -> dict[str, Any]:
    """Accuracy of the trained network's bonus search (and of its bonus-blind version and the heuristic
    search) on the 11x11 targeted positions."""
    from gobot.variant.models.heuristic import HeuristicEvaluator
    from gobot.variant.models.net_onnx import OnnxEvaluator
    from gobot.variant.search.mcts import CachedEvaluator, SearchParams, run_search

    positions = json.loads(path.read_text())["positions"]
    agents = {
        "bonus search + trained net": (CachedEvaluator(OnnxEvaluator(Path(model_path))), True),
        "bonus-blind search + net without bonus inputs": (
            CachedEvaluator(OnnxEvaluator(Path(model_path), blind=True)),
            False,
        ),
        "bonus search + heuristic": (CachedEvaluator(HeuristicEvaluator()), True),
    }
    report: dict[str, Any] = {}
    for name, (ev, aware) in agents.items():

        def choose(st: VariantState, ev: Any = ev, aware: bool = aware) -> int:
            s = run_search(
                st, ev, visits=visits, params=SearchParams(batch=4, model_bonuses=aware), rng=random.Random(0)
            )
            return s.result().stats[0].action

        report[name] = check_agent(positions, choose)
    return report

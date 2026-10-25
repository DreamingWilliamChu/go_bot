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
            surv = _record("survival", b, mover, komi, forced, started)
            same = _record("same_board", b, mover, komi, [0, 0], started)
            if set(surv["best"]) != set(same["best"]) and surv["value"] != same["value"]:
                if _good_for_mover(surv) and len(found["survival"]) < per_category:
                    found["survival"].append(surv)
                if _good_for_mover(same) and len(found["same_board"]) < per_category:
                    found["same_board"].append(same)
            elif _good_for_mover(same) and len(found["no_bonus"]) < per_category:
                found["no_bonus"].append({**same, "category": "no_bonus"})
            three = _record("three_move", b, mover, komi, [0, 0], [2, 2], bonusActive=True, movesLeft=3)
            normal = _record("three_move", b, mover, komi, [0, 0], [2, 2])
            if (
                set(three["best"]) != set(normal["best"])
                and _good_for_mover(three)
                and three["value"] != normal["value"]
                and len(found["three_move"]) < per_category
            ):
                found["three_move"].append(three)
            if len(found["unequal"]) < per_category:
                half = [0, 0]
                half[oi] = 1
                st = [2, 2]
                st[oi] = 2  # two eligible turns left: P = 1/2
                uneq = _record("unequal", b, mover, komi, half, st)
                if (
                    0 < uneq["value"] < 1
                    and uneq["value"] not in (surv["value"], same["value"])
                    and _good_for_mover_soft(uneq)
                ):
                    found["unequal"].append(uneq)
        except TooLarge:
            continue
    return [r for c in CATEGORIES for r in found[c]]


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
    recs = generate(a.per_category, a.seed, a.budget)
    Path(a.out).write_text(json.dumps({"seed": a.seed, "positions": recs}, indent=1) + "\n")
    counts = {c: sum(1 for r in recs if r["category"] == c) for c in CATEGORIES}
    print(f"wrote {len(recs)} positions to {a.out}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The bonus search finds solver-proven moves in the targeted positions (tests/fixtures/variant/targeted.json)."""

import json
import random
from pathlib import Path

import pytest

from gobot.variant.models.heuristic import HeuristicEvaluator
from gobot.variant.search.exact import solve
from gobot.variant.search.mcts import SearchParams, run_search
from gobot.variant.training.targeted import CATEGORIES, check_agent, make_state

POSITIONS = json.loads((Path(__file__).parents[1] / "fixtures" / "variant" / "targeted.json").read_text())[
    "positions"
]


def test_fixture_covers_every_category() -> None:
    assert {r["category"] for r in POSITIONS} == set(CATEGORIES)


@pytest.mark.parametrize("rec", POSITIONS[:8], ids=lambda r: r["category"])
def test_solver_answers_still_hold(rec: dict) -> None:
    sol = solve(make_state(rec), max_nodes=60_000)
    assert sorted(sol.best) == rec["best"] and sol.value == pytest.approx(rec["value"])


def test_same_board_different_bonus_changes_the_answer() -> None:
    # the hand-built straight-three group: with Black's certain 3-move bonus coming, only the vital
    # point saves White; without it, any of the three liberties does
    surv = next(r for r in POSITIONS if r["category"] == "survival")
    assert surv["best"] == [1]
    calm = solve(make_state({**surv, "remaining": [0, 0]}))
    assert calm.best == {0, 1, 2}


def test_bonus_search_picks_proven_moves() -> None:
    def choose(state):
        s = run_search(
            state, HeuristicEvaluator(), visits=3000, params=SearchParams(batch=1), rng=random.Random(0)
        )
        return s.result().stats[0].action

    report = check_agent(POSITIONS, choose)
    for cat, r in report.items():
        assert r["accuracy"] == 1.0, (cat, r)

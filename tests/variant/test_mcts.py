import random
from collections.abc import Sequence

import numpy as np
import pytest

from gobot.variant.config import VariantConfig
from gobot.variant.env import Environment
from gobot.variant.models.evaluator import Evaluation, EvaluatorInfo, UniformEvaluator, legal_mask, normalise
from gobot.variant.models.heuristic import HeuristicEvaluator
from gobot.variant.observation import Observation, observe, search_key
from gobot.variant.randomness import HiddenSchedule
from gobot.variant.rules import BLACK, WHITE, Board
from gobot.variant.search.mcts import (
    CachedEvaluator,
    Chance,
    Decision,
    Search,
    SearchParams,
    make_node,
    run_search,
)
from gobot.variant.state import TURN_START, imported_state, initial_state


class FnEvaluator:
    """Uniform priors; value given by a function of the public state."""

    info = EvaluatorInfo("test", "test", None, approximate=True)

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]:
        self.calls += len(batch)
        out = []
        for o in batch:
            m = legal_mask(o)
            out.append(Evaluation(normalise(np.ones(m.shape, np.float32), m), float(self.fn(o.state))))
        return out

    def key(self, obs: Observation) -> bytes:
        return search_key(obs.state)


def state(rows, to_move, remaining=(0, 0), started=(1, 1), bonus=False, left=1, horizon=10, komi=0.5, k=3):
    b = Board.from_rows(rows)
    cfg = VariantConfig(
        size=b.n,
        komi=komi,
        black_bonuses=remaining[0],
        white_bonuses=remaining[1],
        horizon=horizon,
        moves_per_bonus=k,
    )
    return imported_state(cfg, b, to_move, remaining, started, bonus_active=bonus, moves_left=left)


EMPTY5 = ["....."] * 5


def test_chance_nodes_follow_public_probabilities_and_weight_values() -> None:
    # Black's normal turn; White's next turn is a bonus with P = 1/4 (1 left, 4 eligible turns).
    root = state(EMPTY5, BLACK, remaining=(0, 1), started=(1, 6), horizon=10)
    assert root.trigger_probability(WHITE) == pytest.approx(0.25)
    # White's bonus is a disaster for Black; a normal White turn is fine.
    ev = FnEvaluator(lambda s: 0.0 if (s.to_move == WHITE and s.bonus_active) else 1.0)
    s = run_search(root, ev, visits=1600, params=SearchParams(batch=1), rng=random.Random(0))
    chances = [c for c in s.root.children.values() if isinstance(c, Chance)]
    assert chances and all(c.probs[0] == pytest.approx(0.25) for c in chances)
    big = max(chances, key=lambda c: c.n)
    assert big.N[0] / big.n == pytest.approx(0.25, abs=0.03)  # visit share tracks the probability
    assert big.value() == pytest.approx(0.75, abs=0.02)  # Σ p·Q
    res = s.result()
    assert res.stats[0].black_win == pytest.approx(0.75, abs=0.02)
    assert res.stats[0].pv[0].get("turnStart", {}).get("probability") == pytest.approx(0.25)


def test_certain_outcomes_are_not_branched() -> None:
    pending = state(EMPTY5, BLACK, remaining=(0, 1), started=(1, 9), horizon=10)  # W: 1 left, 1 eligible
    from gobot.variant.state import apply_action

    after = apply_action(pending, 12)
    assert after.phase == TURN_START and after.trigger_probability(WHITE) == 1.0
    node = make_node(after)
    assert isinstance(node, Decision) and node.state.bonus_active and node.state.to_move == WHITE
    none_left = apply_action(state(EMPTY5, BLACK), 12)
    node = make_node(none_left)
    assert isinstance(node, Decision) and not node.state.bonus_active
    # bonus-blind search imagines a game without any further bonus, even a certain one
    mid = apply_action(state(EMPTY5, BLACK, remaining=(0, 1), started=(1, 6)), 12)
    assert isinstance(make_node(mid), Chance)
    blind = Search(state(EMPTY5, BLACK, remaining=(0, 1), started=(1, 9)), SearchParams(model_bonuses=False))
    assert blind.root.state.remaining == (0, 0)


@pytest.mark.parametrize("mover", [BLACK, WHITE])
def test_values_back_up_across_same_player_moves(mover: int) -> None:
    # A 2-move bonus turn. The mover's ideal is to own both (1,1) and (3,3) after its turn.
    target = {1 * 5 + 1, 3 * 5 + 3}
    root = state(EMPTY5, mover, bonus=True, left=2, k=2)

    def value(s):
        mine = {a for a in target if s.board.at_action(a) == mover}
        good = 1.0 if len(mine) == 2 else 0.3 if mine else 0.0
        return good if mover == BLACK else 1.0 - good

    s = run_search(root, FnEvaluator(value), visits=3000, params=SearchParams(batch=4), rng=random.Random(1))
    res = s.result()
    assert res.stats[0].action in target
    pv = res.stats[0].pv
    assert [step["color"] for step in pv[:2]] == ["B" if mover == BLACK else "W"] * 2
    assert {pv[0]["action"], pv[1]["action"]} == target
    best_for_mover = res.stats[0].black_win if mover == BLACK else 1 - res.stats[0].black_win
    assert best_for_mover > 0.8  # not negated after the first same-colour move


def test_virtual_loss_is_fully_removed_and_batches_agree() -> None:
    root = state(EMPTY5, WHITE, remaining=(1, 1), started=(2, 2), horizon=6)
    ev = HeuristicEvaluator()
    for batch in (1, 8):
        s = run_search(root, ev, visits=400, params=SearchParams(batch=batch), rng=random.Random(2))
        nodes = [s.root]
        while nodes:
            n = nodes.pop()
            if isinstance(n, Chance):
                assert sum(n.N) == n.n
                nodes.extend(n.outcomes)
                continue
            if not n.expanded:
                continue
            assert np.all(n.W >= -1e-9) and np.all(n.W <= n.N + 1e-9)
            assert n.n == pytest.approx(n.N.sum() + 1)  # its own evaluation plus every visit below
            nodes.extend(n.children.values())
        assert s.visits >= 400


def test_search_depends_only_on_the_public_observation() -> None:
    cfg = VariantConfig(size=7, black_bonuses=2, white_bonuses=2, horizon=6)
    a = Environment(initial_state(cfg), HiddenSchedule(frozenset({1, 2}), frozenset({5, 6})))
    b = Environment(initial_state(cfg), HiddenSchedule(frozenset({1, 4}), frozenset({1, 2})))
    for env in (a, b):
        env.step(24)
        env.step(25)
    # Black's first two turns were bonuses in both games; their public states now coincide
    assert search_key(a.state) == search_key(b.state)
    res = [
        run_search(observe(env.state), HeuristicEvaluator(), visits=300, rng=random.Random(7)).result()
        for env in (a, b)
    ]
    assert [(x.action, x.visits) for x in res[0].stats] == [(x.action, x.visits) for x in res[1].stats]


def test_terminal_values_are_exact() -> None:
    # White passed (pass-only); Black passing now ends the game: Black owns the board -> 1.0.
    root = state(["X...."] + ["....."] * 4, BLACK)
    from dataclasses import replace

    root = replace(root, pass_streak=1)
    s = run_search(root, FnEvaluator(lambda s: 0.5), visits=300, rng=random.Random(3))
    pass_stat = next(x for x in s.result().stats if x.action == 25)
    assert pass_stat.black_win == pytest.approx(1.0)


def test_keys_distinguish_bonus_resources_and_cache_respects_them() -> None:
    rows = ["X....", ".O...", ".....", ".....", "....."]
    a = state(rows, BLACK, remaining=(1, 1), started=(2, 2))
    b = state(rows, BLACK, remaining=(1, 0), started=(2, 2))
    c = state(rows, BLACK, remaining=(1, 1), started=(2, 2), bonus=True, left=3)
    keys = {search_key(s) for s in (a, b, c)}
    assert len(keys) == 3
    calls = FnEvaluator(lambda s: s.remaining[1] / 2)
    cached = CachedEvaluator(calls)
    ev = cached.evaluate([observe(a), observe(b), observe(a)])
    assert calls.calls == 2 and ev[0].black_win != ev[1].black_win and ev[0].black_win == ev[2].black_win


def test_root_noise_and_uniform_evaluator_smoke() -> None:
    root = state(EMPTY5, BLACK)
    s = Search(root, SearchParams(dirichlet_alpha=0.3), random.Random(5))
    leaves = s.prepare(1)
    s.finish(leaves, UniformEvaluator().evaluate([observe(root)]))
    assert s.root.expanded and s.root.P.sum() == pytest.approx(1.0) and not np.allclose(s.root.P, s.root.P[0])


# ---------------------------------------------------------------------- judge trigger: bonuses are chance
def judge_state(rows, to_move, remaining, started=(1, 1), komi=0.5, k=3, horizon=10, cap=None):
    b = Board.from_rows(rows)
    cfg = VariantConfig(
        size=b.n,
        komi=komi,
        black_bonuses=remaining[0],
        white_bonuses=remaining[1],
        moves_per_bonus=k,
        trigger="judge",
        horizon=horizon,
        max_placements=cap,
    )
    return imported_state(cfg, b, to_move, remaining, started)


def stone_balance(s):
    b, w = s.board.count(BLACK), s.board.count(WHITE)
    return min(1.0, max(0.0, 0.5 + 0.08 * (b - w)))


def test_search_never_offers_a_bonus_as_a_move() -> None:
    root = judge_state(EMPTY5, BLACK, (1, 1))
    assert root.can_grant_bonus  # the judge could grant one now, but that is not the player's choice
    s = run_search(
        root, FnEvaluator(stone_balance), visits=1200, params=SearchParams(batch=1), rng=random.Random(0)
    )
    res = s.result()
    assert root.config.bonus_action not in [x.action for x in res.stats]
    assert all(not step.get("bonus") for x in res.stats for step in x.pv)
    assert res.policy().shape == (26,)


def test_judge_bonuses_of_both_players_are_chance_with_the_modelled_probability() -> None:
    root = judge_state(EMPTY5, BLACK, (1, 1), started=(1, 6), horizon=10)
    p_white = root.trigger_probability(WHITE)
    assert p_white == pytest.approx(1 / 4)
    s = run_search(
        root, FnEvaluator(stone_balance), visits=2000, params=SearchParams(batch=1), rng=random.Random(0)
    )
    chances = [c for c in s.root.children.values() if isinstance(c, Chance)]
    assert chances and all(c.probs[0] == pytest.approx(p_white) for c in chances)
    big = max(chances, key=lambda c: c.n)
    assert big.N[0] / big.n == pytest.approx(p_white, abs=0.05)


def test_a_judge_bonus_that_may_come_changes_the_values() -> None:
    def black_win(white_bonuses: int) -> float:
        root = judge_state(EMPTY5, BLACK, (0, white_bonuses), started=(1, 6), horizon=10)
        s = run_search(
            root, FnEvaluator(stone_balance), visits=2000, params=SearchParams(batch=1), rng=random.Random(0)
        )
        return float(s.result().stats[0].black_win or 0)

    assert black_win(2) < black_win(0)


def test_exact_solver_treats_the_judge_as_chance() -> None:
    from gobot.variant.search.exact import solve

    # White's straight-three group; Black's next turn is a bonus with modelled probability 1 (1 left, 1
    # turn before H): White must take the vital point, exactly as against a certain random bonus.
    rows = ["...OX", "OOOOX", "XXXXX", "X.X.X", "XXXXX"]
    st = judge_state(rows, WHITE, (1, 0), started=(3, 3), komi=9.5, horizon=4, cap=3)
    assert st.trigger_probability(BLACK) == 1.0
    sol = solve(st)
    assert sol.best == {1} and st.config.bonus_action not in sol.action_values
    s = run_search(st, HeuristicEvaluator(), visits=3000, params=SearchParams(batch=1), rng=random.Random(0))
    assert s.result().stats[0].action == 1

"""Exact expectimax for tiny bonus-turn Go positions: the ground truth for tactical tests.

Every legal action is searched, turn starts are averaged with their public probabilities, and terminal
positions are scored exactly (Black win 1, loss 0, draw 0.5). Positional superko makes every line
finite. Only feasible for a handful of empty points; ``max_nodes`` guards against anything larger.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from gobot.variant.observation import search_key
from gobot.variant.rules import BLACK
from gobot.variant.state import TERMINAL, TURN_START, VariantState, apply_action, start_turn


class TooLarge(RuntimeError):
    pass


@dataclass
class Solution:
    value: float  # Black's win probability under optimal play and the public chance model
    action_values: dict[int, float]  # Black value after each legal action of the root actor
    best: set[int]  # optimal actions for the root actor
    nodes: int


class _Solver:
    def __init__(self, max_nodes: int):
        self.memo: dict[bytes, float] = {}
        self.nodes = 0
        self.max_nodes = max_nodes

    def value(self, s: VariantState) -> float:
        if s.phase == TERMINAL:
            assert s.result is not None
            v = s.result.black_value
            return v if v is not None else s.result.score.black_value
        key = search_key(s)
        got = self.memo.get(key)
        if got is not None:
            return got
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise TooLarge(f"more than {self.max_nodes} positions")
        if s.phase == TURN_START:
            p = s.trigger_probability(s.to_move)
            v = 0.0
            if p > 0:
                v += p * self.value(start_turn(s, True))
            if p < 1:
                v += (1 - p) * self.value(start_turn(s, False))
        else:
            vals = [self.value(apply_action(s, a)) for a in s.legal_actions()]
            v = max(vals) if s.to_move == BLACK else min(vals)
        self.memo[key] = v
        return v


def solve(state: VariantState, max_nodes: int = 300_000, tol: float = 1e-9) -> Solution:
    if state.phase != "in_turn":
        raise ValueError("solve a position with a turn in progress")
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))  # depth is bounded by the game's cap
    sv = _Solver(max_nodes)
    action_values = {a: sv.value(apply_action(state, a)) for a in state.legal_actions()}
    black = state.to_move == BLACK
    target = max(action_values.values()) if black else min(action_values.values())
    best = {a for a, v in action_values.items() if abs(v - target) <= tol}
    return Solution(target, action_values, best, sv.nodes)

"""Play many bonus-turn games at once, batching the search leaves of every game per evaluator.

Used by self-play (one network plays both sides), gating and the evaluation arena (two agents). Each
game is an ``Environment`` with its own hidden schedule; players only ever receive the public
``Observation`` of the game. Search players run ``Search`` for a visit budget; direct players
(random, greedy heuristic) choose at once.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from gobot.variant.config import VariantConfig
from gobot.variant.env import Environment
from gobot.variant.models.evaluator import Evaluator
from gobot.variant.models.features import Raw, raw_from_state
from gobot.variant.models.heuristic import heuristic_priors
from gobot.variant.observation import observe
from gobot.variant.randomness import sample_schedule
from gobot.variant.rules import BLACK
from gobot.variant.search.mcts import Search, SearchParams
from gobot.variant.state import VariantState, initial_state


@dataclass
class Player:
    """A player. ``evaluator`` None means a direct player (``direct(state, rng)`` picks the action)."""

    name: str
    evaluator: Evaluator | None = None
    params: SearchParams = field(default_factory=SearchParams)
    visits: int = 64
    fast_visits: int = 0  # > 0: playout-cap randomisation (self-play): most moves use this budget
    full_prob: float = 1.0  # probability of a full search (only full searches give policy targets)
    temperature_moves: int = 0  # sample moves from visits for this many placements, then take the best
    direct: Callable[[VariantState, random.Random], int] | None = None


def random_player(name: str = "random") -> Player:
    """Uniformly random legal action, but never a pass while a placement is legal (otherwise games
    end at once and a random opponent is trivially beaten by anything)."""

    def choose(s: VariantState, rng: random.Random) -> int:
        acts = s.legal_actions()
        placements = [a for a in acts if a != s.config.pass_action]
        return rng.choice(placements) if placements else s.config.pass_action

    return Player(name, direct=choose)


def greedy_player(name: str = "greedy") -> Player:
    """The documented heuristic baseline as a player: the action with the largest heuristic prior
    (captures, saving chains in atari, contact moves; never filling own eyes)."""

    def choose(s: VariantState, rng: random.Random) -> int:
        pri = heuristic_priors(observe(s))
        best = float(pri.max())
        cands = [int(a) for a in np.flatnonzero(pri >= best - 1e-9)]
        return rng.choice(cands)

    return Player(name, direct=choose)


@dataclass
class Sample:
    raw: Raw
    policy: np.ndarray  # visit distribution over N*N+1 actions
    actor: int


@dataclass
class GameOutcome:
    seed: int
    black: str
    white: str
    config: VariantConfig
    schedule: dict[str, list[int]]  # privileged: for debugging and paired evaluation only
    placements: int
    passes: int
    black_value: float | None  # None: truncated (no result)
    lead: float  # area lead of the final board for Black (scored as it stands)
    truncated: bool
    samples: list[Sample] = field(default_factory=list)
    moves: list[int] = field(default_factory=list)


@dataclass
class _Game:
    env: Environment
    seed: int
    black: Player
    white: Player
    rng: random.Random
    record: bool
    search: Search | None = None
    target: int = 0
    full: bool = False
    samples: list[Sample] = field(default_factory=list)
    passes: int = 0

    def player(self) -> Player:
        return self.black if self.env.state.to_move == BLACK else self.white


def new_env(config: VariantConfig, seed: int) -> Environment:
    """A game whose hidden schedule depends only on ``seed`` (so a pair of games can share it)."""
    rng = random.Random(seed)
    sched = sample_schedule(config.black_bonuses, config.white_bonuses, config.horizon, rng)
    return Environment(initial_state(config), sched)


def choose_from_visits(policy: np.ndarray, temperature: float, rng: random.Random) -> int:
    if temperature <= 0:
        best = float(policy.max())
        return rng.choice([int(a) for a in np.flatnonzero(policy >= best - 1e-12)])
    w = policy.astype(np.float64) ** (1.0 / temperature)
    w /= w.sum()
    return int(rng.choices(range(len(w)), weights=w.tolist())[0])


def run_games(
    specs: list[tuple[VariantConfig, int, Player, Player]],
    *,
    record: bool = False,
    leaves_per_game: int = 4,
    should_stop: Callable[[], bool] | None = None,
) -> list[GameOutcome]:
    """Play ``(config, seed, black, white)`` games to the end in lockstep; returns their outcomes."""
    games = [
        _Game(new_env(cfg, seed), seed, b, w, random.Random(seed ^ 0x5F3759DF), record)
        for cfg, seed, b, w in specs
    ]
    done: list[GameOutcome | None] = [None] * len(games)
    active = list(range(len(games)))
    while active:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        pending: dict[int, list[tuple[int, list[Any]]]] = {}  # id(evaluator) -> [(game, leaves)]
        evaluators: dict[int, Evaluator] = {}
        for gi in list(active):
            g = games[gi]
            st = g.env.state
            if st.terminal:
                done[gi] = _finish(g)
                active.remove(gi)
                continue
            pl = g.player()
            if pl.evaluator is None:
                assert pl.direct is not None
                a = pl.direct(st, g.rng)
                _step(g, a)
                continue
            if g.search is None:
                g.full = pl.fast_visits <= 0 or g.rng.random() < pl.full_prob
                params = pl.params
                if not g.full and params.dirichlet_alpha > 0:
                    params = SearchParams(**{**params.__dict__, "dirichlet_alpha": 0.0})
                g.search = Search(st, params, g.rng)
                g.target = pl.visits if g.full else pl.fast_visits
            leaves = g.search.prepare(min(leaves_per_game, max(1, g.target - g.search.visits)))
            if leaves:
                pending.setdefault(id(pl.evaluator), []).append((gi, leaves))
                evaluators[id(pl.evaluator)] = pl.evaluator
        for key, items in pending.items():
            flat = [leaf for _, leaves in items for leaf in leaves]
            evs = evaluators[key].evaluate([observe(leaf.node.state) for leaf in flat])
            i = 0
            for gi, leaves in items:
                games[gi].search.finish(leaves, evs[i : i + len(leaves)])  # type: ignore[union-attr]
                i += len(leaves)
        for gi in active:
            g = games[gi]
            s = g.search
            if s is None or (s.visits < g.target and not s.root.terminal):
                continue
            res = s.result(pv_len=1)
            pi = res.policy()
            pl = g.player()
            temp = 1.0 if g.env.state.placements < pl.temperature_moves else 0.0
            a = res.best_action(temp, g.rng) if res.stats else g.env.state.config.pass_action
            if g.record and g.full:
                g.samples.append(Sample(raw_from_state(g.env.state), pi, g.env.state.to_move))
            g.search = None
            _step(g, a)
    return [d for d in done if d is not None]


def _step(g: _Game, action: int) -> None:
    if action == g.env.state.config.pass_action:
        g.passes += 1
    g.env.step(action)


def _finish(g: _Game) -> GameOutcome:
    st = g.env.state
    r = st.result
    assert r is not None
    return GameOutcome(
        seed=g.seed,
        black=g.black.name,
        white=g.white.name,
        config=st.config,
        schedule=g.env.schedule.to_json(),
        placements=st.placements,
        passes=g.passes,
        black_value=r.black_value,
        lead=r.score.lead,
        truncated=r.truncated,
        samples=g.samples,
        moves=list(g.env.actions),
    )

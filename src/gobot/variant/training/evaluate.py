"""Evaluation arena: paired-schedule matches between agents, with confidence intervals.

Pairing: game pair *i* uses seed ``seed_base + i``. Both games of a pair get the same allocations and
the same hidden bonus schedule for the Black slot and for the White slot (``lockstep.new_env`` draws the
schedule from the seed only); the agents swap colours between the two games. So each agent meets the
same bonus luck once with each colour. Evaluation seeds come from a range disjoint from self-play's.

Reported per match: wins / draws / losses of agent A, truncated games (excluded from the score, never
counted as wins or draws), score = (W + D/2) / (W + D + L), its Wilson 95% interval, the Elo difference
implied by the score and its interval, and the compute budget (visits per move, wall time).

Agents (``AgentSpec``):

* ``random``   - uniformly random legal placement (passes only when nothing else is legal)
* ``greedy``   - the documented heuristic baseline (``models/heuristic.py`` priors, best one)
* ``search``   - bonus search with an evaluator: ``heuristic``, ``katago`` (KataGo's raw network,
  standard Go) or ``net`` (a trained variant network). ``blind=True`` hides every bonus input from the
  network *and* makes the search imagine that no further bonus can trigger: the "no bonus-resource
  information" baseline.
"""

from __future__ import annotations

import math
import multiprocessing as mp
import random
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gobot.variant.config import VariantConfig
from gobot.variant.search.mcts import SearchParams
from gobot.variant.training.lockstep import GameOutcome, Player, greedy_player, random_player, run_games

EVAL_SEED_BASE = 9_000_000_000  # self-play seeds stay far below this


@dataclass
class AgentSpec:
    name: str
    kind: str  # random | greedy | search
    evaluator: str = "heuristic"  # heuristic | katago | net
    path: str | None = None  # ONNX model for evaluator "net"
    visits: int = 64
    blind: bool = False
    katago: dict[str, Any] | None = None  # KataGo evaluator spec (binary, model_path, config_path, label)


@dataclass
class MatchResult:
    a: str
    b: str
    games: int
    wins: int
    draws: int
    losses: int
    truncated: int
    score: float | None
    ci95: tuple[float, float] | None
    elo: float | None
    elo_ci95: tuple[float, float] | None
    seconds: float
    budget: dict[str, Any]
    by_color: dict[str, dict[str, int]] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def elo(score: float) -> float:
    s = min(max(score, 1e-3), 1 - 1e-3)
    return -400.0 * math.log10(1.0 / s - 1.0)


_CACHE: dict[str, Any] = {}


def make_player(spec: AgentSpec) -> Player:
    if spec.kind == "random":
        return random_player(spec.name)
    if spec.kind == "greedy":
        return greedy_player(spec.name)
    key = f"{spec.evaluator}:{spec.path}:{spec.blind}"
    ev = _CACHE.get(key)
    if ev is None:
        from gobot.variant.search.mcts import CachedEvaluator

        if spec.evaluator == "heuristic":
            from gobot.variant.models.heuristic import HeuristicEvaluator

            inner: Any = HeuristicEvaluator()
        elif spec.evaluator == "net":
            from gobot.variant.models.net_onnx import OnnxEvaluator

            assert spec.path
            inner = OnnxEvaluator(Path(spec.path), threads=1, blind=spec.blind)
        elif spec.evaluator == "katago":
            from gobot.variant.models.katago_eval import KataGoEvaluator

            k = spec.katago or {}
            inner = KataGoEvaluator(k["binary"], Path(k["model_path"]), Path(k["config_path"]), k["label"])
        else:
            raise ValueError(spec.evaluator)
        ev = _CACHE[key] = CachedEvaluator(inner, 300_000)
    return Player(
        spec.name,
        ev,
        SearchParams(batch=4, model_bonuses=not spec.blind),
        visits=spec.visits,
        temperature_moves=4,  # a little opening variety between pairs
    )


def _work(
    a: AgentSpec, b: AgentSpec, games: list[tuple[VariantConfig, int, bool]]
) -> list[tuple[bool, GameOutcome]]:
    pa, pb = make_player(a), make_player(b)
    specs = [(cfg, seed, pa, pb) if a_black else (cfg, seed, pb, pa) for cfg, seed, a_black in games]
    outs = run_games(specs)
    by_seed = {(o.seed, o.black): o for o in outs}
    return [(a_black, by_seed[(seed, a.name if a_black else b.name)]) for _, seed, a_black in games]


def paired_games(
    pairs: int,
    size: int,
    allocations: list[tuple[int, int]],
    seed_base: int = EVAL_SEED_BASE,
    cap_factor: float = 3.0,
) -> list[tuple[VariantConfig, int, bool]]:
    rng = random.Random(seed_base)
    out = []
    for i in range(pairs):
        bb, ww = rng.choice(allocations)
        cfg = VariantConfig(
            size=size, black_bonuses=bb, white_bonuses=ww, max_placements=int(cap_factor * size * size)
        )
        seed = seed_base + i
        out += [(cfg, seed, True), (cfg, seed, False)]
    return out


def play_match(
    a: AgentSpec,
    b: AgentSpec,
    pairs: int,
    size: int,
    allocations: list[tuple[int, int]],
    *,
    workers: int = 1,
    seed_base: int = EVAL_SEED_BASE,
) -> MatchResult:
    if a.name == b.name:
        raise ValueError("agents need distinct names")
    games = paired_games(pairs, size, allocations, seed_base)
    t0 = time.monotonic()
    results: list[tuple[bool, GameOutcome]] = []
    if workers <= 1 or "katago" in (a.evaluator, b.evaluator):
        results = _work(a, b, games)
    else:
        # keep both games of a pair in the same worker
        per = 2 * max(1, -(-pairs // workers))
        parts = [games[i : i + per] for i in range(0, len(games), per)]
        with ProcessPoolExecutor(
            max_workers=min(workers, len(parts)), mp_context=mp.get_context("spawn")
        ) as pool:
            for r in pool.map(_work, [a] * len(parts), [b] * len(parts), parts):
                results += r
    return summarize_match(a, b, results, time.monotonic() - t0)


def summarize_match(
    a: AgentSpec, b: AgentSpec, results: list[tuple[bool, GameOutcome]], seconds: float
) -> MatchResult:
    w = d = losses = trunc = 0
    by_color = {"black": {"w": 0, "d": 0, "l": 0}, "white": {"w": 0, "d": 0, "l": 0}}
    for a_black, o in results:
        if o.truncated or o.black_value is None:
            trunc += 1
            continue
        a_val = o.black_value if a_black else 1.0 - o.black_value
        col = by_color["black" if a_black else "white"]
        if a_val == 1.0:
            w += 1
            col["w"] += 1
        elif a_val == 0.0:
            losses += 1
            col["l"] += 1
        else:
            d += 1
            col["d"] += 1
    n = w + d + losses
    score = (w + 0.5 * d) / n if n else None
    ci = wilson(w + 0.5 * d, n) if n else None
    return MatchResult(
        a=a.name,
        b=b.name,
        games=len(results),
        wins=w,
        draws=d,
        losses=losses,
        truncated=trunc,
        score=score,
        ci95=ci,
        elo=elo(score) if score is not None else None,
        elo_ci95=(elo(ci[0]), elo(ci[1])) if ci else None,
        seconds=round(seconds, 1),
        budget={
            "a": {
                "kind": a.kind,
                "evaluator": a.evaluator if a.kind == "search" else None,
                "visits": a.visits if a.kind == "search" else 0,
                "blind": a.blind,
            },
            "b": {
                "kind": b.kind,
                "evaluator": b.evaluator if b.kind == "search" else None,
                "visits": b.visits if b.kind == "search" else 0,
                "blind": b.blind,
            },
        },
        by_color=by_color,
    )


def promote(result: MatchResult, min_games: int) -> bool:
    """Gating rule: promote the candidate (agent A) only if, over at least ``min_games`` scored games,
    the lower end of the Wilson 95% interval of its score against the current best is above 0.5."""
    n = result.wins + result.draws + result.losses
    return n >= min_games and result.ci95 is not None and result.ci95[0] > 0.5

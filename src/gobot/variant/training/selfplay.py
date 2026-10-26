"""Self-play generation across worker processes.

Each worker (spawned, one onnxruntime thread) loads the current best network, plays its share of games
in lockstep with variant-aware search (Dirichlet noise at the root, temperature for the opening
placements, playout-cap randomisation) and returns the outcomes. The parent turns them into a replay
chunk:

* inputs: the public raw facts of each recorded position (no schedule)
* policy target: the search's visit distribution (full searches only)
* value target: the final outcome from Black's perspective; NaN for games truncated at the
  placement cap (they are counted and reported, never labelled as wins or draws)
* score target: Black's final area lead / N^2 of completed games

Privileged per-game data (seed, hidden schedule, moves) goes to ``debug/`` for audits only.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from gobot.variant.config import VariantConfig
from gobot.variant.search.mcts import SearchParams
from gobot.variant.training.lockstep import GameOutcome, Player, run_games
from gobot.variant.training.replay import SELFPLAY, make_chunk

_EVALUATORS: dict[str, Any] = {}


@dataclass
class SelfPlaySettings:
    size: int = 11
    visits: int = 64
    fast_visits: int = 16
    full_prob: float = 0.25
    temperature_moves: int = 12
    dirichlet_alpha: float = 0.15
    c_puct: float = 1.5
    max_placements_factor: float = 3.0  # cap = factor * N^2 placements
    parallel_games: int = 16  # per worker, in lockstep
    komi: float = 7.5
    moves_per_bonus: int = 3
    horizon: int = 0  # 0: default for the size


def _evaluator(path: str) -> Any:
    ev = _EVALUATORS.get(path)
    if ev is None:
        from gobot.variant.models.net_onnx import OnnxEvaluator
        from gobot.variant.search.mcts import CachedEvaluator

        _EVALUATORS.clear()  # one network per worker at a time
        ev = _EVALUATORS[path] = CachedEvaluator(OnnxEvaluator(Path(path), threads=1), 400_000)
    return ev


def _work(path: str, s: SelfPlaySettings, jobs: list[tuple[int, int, int]]) -> list[GameOutcome]:
    """Worker: play ``(seed, black_bonuses, white_bonuses)`` games with the network at ``path``."""
    ev = _evaluator(path)
    player = Player(
        "selfplay",
        ev,
        SearchParams(c_puct=s.c_puct, dirichlet_alpha=s.dirichlet_alpha, batch=4),
        visits=s.visits,
        fast_visits=s.fast_visits,
        full_prob=s.full_prob,
        temperature_moves=s.temperature_moves,
    )
    cap = int(s.max_placements_factor * s.size * s.size)
    out: list[GameOutcome] = []
    for i in range(0, len(jobs), s.parallel_games):
        part = jobs[i : i + s.parallel_games]
        specs = []
        for seed, b, w in part:
            cfg = VariantConfig(
                size=s.size,
                komi=s.komi,
                black_bonuses=b,
                white_bonuses=w,
                moves_per_bonus=s.moves_per_bonus,
                horizon=s.horizon,
                max_placements=cap,
            )
            specs.append((cfg, seed, player, player))
        out += run_games(specs, record=True)
    return out


class SelfPlayPool:
    def __init__(self, workers: int):
        self.workers = workers
        self.pool = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"))

    def play(
        self, model_path: Path, settings: SelfPlaySettings, jobs: list[tuple[int, int, int]]
    ) -> list[GameOutcome]:
        per = max(1, -(-len(jobs) // self.workers))
        parts = [jobs[i : i + per] for i in range(0, len(jobs), per)]
        futs = [self.pool.submit(_work, str(model_path), settings, p) for p in parts]
        out: list[GameOutcome] = []
        for f in futs:
            out += f.result()
        return out

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def allocation_jobs(
    rng: random.Random, games: int, allocations: list[tuple[int, int]], seed_base: int
) -> list[tuple[int, int, int]]:
    """``games`` self-play jobs with allocations drawn from the curriculum's list."""
    return [(seed_base + i, *rng.choice(allocations)) for i in range(games)]


def outcomes_to_chunk(
    outcomes: list[GameOutcome], stage: int, generation: int, meta: dict[str, Any] | None = None
) -> dict[str, np.ndarray] | None:
    raws, pols, vals, scores, actors = [], [], [], [], []
    for o in outcomes:
        area = o.config.area
        for smp in o.samples:
            raws.append(smp.raw)
            pols.append(smp.policy)
            vals.append(np.nan if o.black_value is None else o.black_value)
            scores.append(np.nan if o.truncated else o.lead / area)
            actors.append(smp.actor)
    if not raws:
        return None
    return make_chunk(
        raws, pols, vals, scores, actors=actors, kind=SELFPLAY, stage=stage, generation=generation, meta=meta
    )


def summarize(outcomes: list[GameOutcome]) -> dict[str, Any]:
    done = [o for o in outcomes if not o.truncated]
    return {
        "games": len(outcomes),
        "truncated": len(outcomes) - len(done),
        "samples": sum(len(o.samples) for o in outcomes),
        "black_wins": sum(1 for o in done if o.black_value == 1.0),
        "white_wins": sum(1 for o in done if o.black_value == 0.0),
        "draws": sum(1 for o in done if o.black_value == 0.5),
        "mean_placements": float(np.mean([o.placements for o in outcomes])) if outcomes else 0.0,
        "mean_passes": float(np.mean([o.passes for o in outcomes])) if outcomes else 0.0,
        "mean_abs_lead": float(np.mean([abs(o.lead) for o in done])) if done else 0.0,
    }


def write_debug(outcomes: list[GameOutcome], path: Path) -> None:
    """Privileged per-game data (seeds, hidden schedules, moves): audits only, never model inputs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for o in outcomes:
            f.write(
                json.dumps(
                    {
                        "seed": o.seed,
                        "allocations": [o.config.black_bonuses, o.config.white_bonuses],
                        "schedule": o.schedule,
                        "moves": o.moves,
                        "black_value": o.black_value,
                        "lead": o.lead,
                        "truncated": o.truncated,
                    }
                )
                + "\n"
            )

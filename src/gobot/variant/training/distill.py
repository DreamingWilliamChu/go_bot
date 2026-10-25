"""Policy distillation from KataGo on ordinary Go (bonus allocations 0/0).

Games are played by sampling moves from KataGo's raw policy (one network evaluation per position,
batched across many games); every position records KataGo's raw policy, masked to the variant's legal
moves, as a *standard-Go teacher target* (replay kind ``TEACHER``). Value and score targets are left
empty: KataGo's win estimates are for ordinary Go and are not ground truth for bonus games. This gives
the small network basic Go shape before variant self-play; it is distillation, not fine-tuning KataGo
(no KataGo weights are reused).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from gobot.variant.config import VariantConfig
from gobot.variant.models.features import raw_from_state
from gobot.variant.observation import observe
from gobot.variant.training.lockstep import choose_from_visits, new_env
from gobot.variant.training.replay import TEACHER, make_chunk


def teacher_positions(
    evaluator: Any,
    positions: int,
    size: int,
    *,
    parallel: int = 32,
    seed: int = 1,
    opening_temperature_moves: int = 20,
    should_stop: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, np.ndarray]:
    rng = random.Random(seed)
    cfg = VariantConfig(size=size, black_bonuses=0, white_bonuses=0, max_placements=3 * size * size)
    raws: list[Any] = []
    pols: list[np.ndarray] = []
    actors: list[int] = []
    envs = [new_env(cfg, seed * 1000 + i) for i in range(parallel)]
    next_seed = seed * 1000 + parallel
    t0 = time.monotonic()
    while len(raws) < positions:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        states = [e.state for e in envs]
        evs = evaluator.evaluate([observe(s) for s in states])
        for i, (env, st, ev) in enumerate(zip(envs, states, evs, strict=True)):
            raws.append(raw_from_state(st))
            pols.append(ev.priors.astype(np.float32))
            actors.append(st.to_move)
            temp = 1.0 if st.placements < opening_temperature_moves else 0.5
            env.step(choose_from_visits(ev.priors, temp, rng))
            if env.state.terminal:
                envs[i] = new_env(cfg, next_seed)
                next_seed += 1
        if log and len(raws) % (parallel * 50) < parallel:
            log(f"distill: {len(raws)}/{positions} positions, {len(raws) / (time.monotonic() - t0):.0f}/s")
    n = len(raws)
    nan = [float("nan")] * n
    return make_chunk(
        raws[:positions],
        pols[:positions],
        nan[:positions],
        nan[:positions],
        actors=actors[:positions],
        kind=TEACHER,
        stage=0,
        generation=0,
    )

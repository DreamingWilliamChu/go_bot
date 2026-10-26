"""Bounded training presets (docs/VARIANT_TRAINING.md). Every number here is a hard limit.

* ``smoke_cpu``   - minutes on any CPU: proves self-play, real gradient updates, gating, export, resume
* ``local_small`` - about one hour on an M3 Pro (11 cores): distillation, ~8 generations, evaluation
* ``gpu_long``    - a longer run for a CUDA/MPS machine; configuration only (not run here). Its budget
  is sized from the throughput ``gobot variant bench`` measures, see the docs.

Curriculum (``stages``): ordinary Go (0/0) first, then a small allocation, the default allocation, and a
mixture of equal and unequal allocations. Each generation also replays a share of games from earlier
stages (``mix_previous``) so that earlier settings are not forgotten. Allocations are stored as raw
counts in replay.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gobot.variant.training.net import NetConfig
from gobot.variant.training.selfplay import SelfPlaySettings
from gobot.variant.training.train import TrainConfig


@dataclass
class Stage:
    name: str
    generations: int  # how many generations use this stage
    allocations: list[tuple[int, int]]


@dataclass
class Preset:
    name: str
    size: int = 11
    seed: int = 20261025
    max_runtime_s: float = 3600.0
    workers: int = 8
    net: NetConfig = field(default_factory=NetConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    selfplay: SelfPlaySettings = field(default_factory=SelfPlaySettings)
    stages: list[Stage] = field(default_factory=list)
    mix_previous: float = 0.2
    games_per_generation: int = 256
    max_games: int = 4000
    sample_reuse: float = 4.0  # optimizer samples per new replay sample
    max_steps_per_generation: int = 2000
    replay_max_samples: int = 400_000
    replay_max_bytes: int = 600 * 2**20
    checkpoint_every_steps: int = 200
    distill_positions: int = 20_000
    distill_steps: int = 600
    gate_pairs: int = 40
    gate_min_games: int = 60
    gate_visits: int = 32
    eval_pairs: int = 40
    eval_visits: int = 64
    eval_katago_pairs: int = 20
    install: bool = True  # copy the best model into the app's model directory at the end

    def stage_for(self, generation: int) -> tuple[int, Stage]:
        g = generation
        for i, st in enumerate(self.stages):
            if g < st.generations:
                return i, st
            g -= st.generations
        return len(self.stages) - 1, self.stages[-1]

    @property
    def max_generations(self) -> int:
        return sum(s.generations for s in self.stages)

    def public(self) -> dict[str, Any]:
        return asdict(self)


MIXED = [(0, 0), (1, 1), (2, 2), (3, 3), (2, 1), (1, 2), (3, 1), (1, 3), (2, 0), (0, 2)]
CURRICULUM = [
    Stage("ordinary Go (0/0)", 1, [(0, 0)]),
    Stage("small allocation (1/1)", 2, [(1, 1)]),
    Stage("default allocation (3/3)", 2, [(3, 3)]),
    Stage("mixed equal and unequal", 4, MIXED),
]

PRESETS: dict[str, Preset] = {
    "smoke_cpu": Preset(
        name="smoke_cpu",
        size=7,
        max_runtime_s=600,
        workers=2,
        net=NetConfig(blocks=2, channels=24, head_channels=12, value_hidden=24),
        train=TrainConfig(batch_size=64, device="cpu"),
        selfplay=SelfPlaySettings(
            size=7, visits=24, fast_visits=8, full_prob=0.5, temperature_moves=6, parallel_games=8
        ),
        stages=[Stage("ordinary Go (0/0)", 1, [(0, 0)]), Stage("mixed", 1, [(1, 1), (2, 1), (1, 2)])],
        games_per_generation=24,
        max_games=60,
        max_steps_per_generation=60,
        replay_max_samples=20_000,
        replay_max_bytes=32 * 2**20,
        checkpoint_every_steps=20,
        distill_positions=600,
        distill_steps=40,
        gate_pairs=6,
        gate_min_games=10,
        gate_visits=12,
        eval_pairs=4,
        eval_visits=16,
        eval_katago_pairs=2,
        install=False,
    ),
    "local_small": Preset(
        name="local_small",
        size=11,
        max_runtime_s=3600,
        workers=8,
        net=NetConfig(blocks=4, channels=48, head_channels=24, value_hidden=48),
        train=TrainConfig(batch_size=256, lr=2e-3),
        selfplay=SelfPlaySettings(
            size=11, visits=64, fast_visits=16, full_prob=0.25, temperature_moves=12, parallel_games=16
        ),
        stages=CURRICULUM,
        games_per_generation=640,
        max_games=6000,
        max_steps_per_generation=1500,
        replay_max_samples=300_000,
        replay_max_bytes=400 * 2**20,
        checkpoint_every_steps=250,
        distill_positions=24_000,
        distill_steps=800,
        gate_pairs=50,
        gate_min_games=80,
        gate_visits=32,
        eval_pairs=30,
        eval_visits=48,
        eval_katago_pairs=15,
    ),
    "gpu_long": Preset(
        name="gpu_long",
        size=11,
        max_runtime_s=48 * 3600,
        workers=32,
        net=NetConfig(blocks=10, channels=128, head_channels=32, value_hidden=128),
        train=TrainConfig(batch_size=512, lr=1e-3),
        selfplay=SelfPlaySettings(
            size=11, visits=400, fast_visits=80, full_prob=0.25, temperature_moves=14, parallel_games=64
        ),
        stages=[
            Stage("ordinary Go (0/0)", 4, [(0, 0)]),
            Stage("small allocation (1/1)", 6, [(1, 1)]),
            Stage("default allocation (3/3)", 10, [(3, 3)]),
            Stage("mixed equal and unequal", 40, MIXED),
        ],
        games_per_generation=4000,
        max_games=240_000,
        max_steps_per_generation=20_000,
        replay_max_samples=3_000_000,
        replay_max_bytes=6 * 2**30,
        checkpoint_every_steps=2000,
        distill_positions=500_000,
        distill_steps=20_000,
        gate_pairs=200,
        gate_min_games=400,
        gate_visits=200,
        eval_pairs=200,
        eval_visits=400,
        eval_katago_pairs=100,
    ),
}

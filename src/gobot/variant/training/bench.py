"""Measure this machine before choosing a training budget (``gobot variant bench``).

Measures, for the preset's network: rules speed, onnxruntime evaluations per second on one thread,
lockstep self-play visits per second in one process, the same with all workers in parallel, and
optimizer steps per second on the training device. Prints and saves estimates of the time per
generation for the preset, so budgets come from measurements rather than assumptions.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any

from gobot import hardware
from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.training.presets import Preset


def _rules_speed(size: int, seconds: float = 2.0) -> float:
    rng = random.Random(0)
    moves = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        g = VariantGame.new(
            VariantConfig(size=size, black_bonuses=2, white_bonuses=2, max_placements=3 * size * size),
            seed=rng.randrange(10**9),
        )
        while not g.state.terminal:
            acts = g.state.legal_actions()
            g.play(rng.choice(acts[:-1]) if len(acts) > 1 and rng.random() < 0.97 else acts[-1])
            moves += 1
    return moves / (time.perf_counter() - t0)


def run(preset: Preset, out: Path | None = None) -> dict[str, Any]:
    from gobot.variant.models.features import Raw, raw_from_state
    from gobot.variant.models.net_onnx import OnnxEvaluator
    from gobot.variant.search.mcts import CachedEvaluator
    from gobot.variant.training.lockstep import Player, run_games
    from gobot.variant.training.replay import ReplayBuffer
    from gobot.variant.training.selfplay import SelfPlayPool, outcomes_to_chunk
    from gobot.variant.training.train import Trainer

    n = preset.size
    res: dict[str, Any] = {"preset": preset.name, "hardware": hardware.detect(), "cpu_count": os.cpu_count()}
    res["rules_moves_per_s"] = round(_rules_speed(n))
    with tempfile.TemporaryDirectory() as td:
        tr = Trainer(preset.net, preset.train, seed=0)
        res["device"] = str(tr.device)
        res["parameters"] = tr.parameters
        path = tr.export_onnx(Path(td) / "m" / "model.onnx", n, {"id": "bench", "sizes": [n]})
        ev = OnnxEvaluator(path, threads=1)
        g = VariantGame.new(VariantConfig(size=n), seed=1)
        rng = random.Random(0)
        states: list[Any] = []
        while len(states) < 32 and not g.state.terminal:
            states.append(g.state)
            g.play(rng.choice(g.state.legal_actions()[:-1]))
        raw = Raw.concat([raw_from_state(s) for s in states])
        for bs in (1, 16):
            r = raw.take(slice(0, bs))
            k = 0
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 1.5:
                ev.run(r)
                k += bs
            res[f"onnx_evals_per_s_batch{bs}"] = round(k / (time.perf_counter() - t0))
        s = preset.selfplay
        cfg = VariantConfig(size=n, black_bonuses=2, white_bonuses=2, max_placements=3 * n * n)
        pl = Player(
            "p",
            CachedEvaluator(ev),
            visits=s.visits,
            fast_visits=s.fast_visits,
            full_prob=s.full_prob,
            temperature_moves=s.temperature_moves,
        )
        t0 = time.perf_counter()
        outs = run_games([(cfg, 1000 + i, pl, pl) for i in range(s.parallel_games)], record=True)
        dt = time.perf_counter() - t0
        moves = sum(len(o.moves) for o in outs)
        res["one_process"] = {
            "games": len(outs),
            "seconds": round(dt, 1),
            "games_per_s": round(len(outs) / dt, 3),
            "moves_per_game": round(moves / len(outs), 1),
            "samples_per_game": round(sum(len(o.samples) for o in outs) / len(outs), 1),
        }
        pool = SelfPlayPool(preset.workers)
        try:
            jobs = [(5000 + i, 2, 2) for i in range(preset.workers * s.parallel_games)]
            pool.play(path, s, jobs[: preset.workers])  # warm up the workers
            t0 = time.perf_counter()
            outs = pool.play(path, s, jobs)
            dt = time.perf_counter() - t0
        finally:
            pool.close()
        res["workers"] = {
            "workers": preset.workers,
            "games": len(outs),
            "seconds": round(dt, 1),
            "games_per_s": round(len(outs) / dt, 3),
        }
        chunk = outcomes_to_chunk(outs, 0, 0)
        assert chunk is not None
        buf = ReplayBuffer(Path(td) / "replay", 10**7, 10**10)
        buf.add(chunk, "bench")
        data = buf.load()
        res["replay_bytes_per_sample"] = round(next(buf.root.glob("*.npz")).stat().st_size / len(data), 1)
        tr.train_steps(data, 3)  # warm up (MPS kernels)
        t0 = time.perf_counter()
        tr.train_steps(data, 20)
        res["train_steps_per_s"] = round(20 / (time.perf_counter() - t0), 2)
    gps = res["workers"]["games_per_s"]
    samples = res["one_process"]["samples_per_game"]
    steps = min(
        preset.max_steps_per_generation,
        max(50, preset.sample_reuse * samples * preset.games_per_generation / preset.train.batch_size),
    )
    gate_games = 2 * preset.gate_pairs
    est = {
        "selfplay_s_per_generation": round(preset.games_per_generation / gps),
        "train_s_per_generation": round(steps / res["train_steps_per_s"]),
        # gating searches use fewer visits than self-play's full searches
        "gate_s_per_generation": round(
            gate_games
            / gps
            * preset.gate_visits
            / max(1, s.visits * s.full_prob + s.fast_visits * (1 - s.full_prob))
        ),
    }
    est["total_s_per_generation"] = sum(v for v in est.values() if isinstance(v, (int, float)))
    est["generations_in_budget"] = int(preset.max_runtime_s * 0.75 // max(1, est["total_s_per_generation"]))
    est["note"] = (
        "Game length is measured with an untrained network, which passes early; trained networks play "
        f"longer games (about {res['one_process']['moves_per_game']:.0f} moves here), so scale self-play time "
        "by the real game length (the pipeline logs it per generation)."
    )
    res["estimate"] = est
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2, default=float) + "\n")
    return res


__all__ = ["run"]

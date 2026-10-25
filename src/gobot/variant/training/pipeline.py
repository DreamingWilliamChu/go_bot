"""The training loop: self-play -> replay -> optimisation -> candidate -> gating -> promotion -> ...

Run directory layout (``<variant_dir>/runs/<run>/``)::

    config.json            preset, git commit, hardware, torch device
    run_state.json         phase, generation, best model, history (resume point)
    log.jsonl              every training step and event
    replay/                npz chunks (teacher_*.npz, gen_XXX.npz)
    debug/                 privileged per-game data (seeds, hidden schedules): audits only
    checkpoints/latest.pt  model + optimizer + RNG + step of the candidate lineage
    models/gen_XXX/        exported candidates (model.onnx + model.json)
    eval/                  gating and final evaluation results, report.md

Every step skips itself when its artifact already exists, so ``resume`` simply runs the loop again.
SIGINT stops at the next safe point (after the current training step or game batch) and saves.
"""

from __future__ import annotations

import json
import random
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from gobot import hardware
from gobot.variant.training.evaluate import AgentSpec, play_match, promote
from gobot.variant.training.presets import PRESETS, Preset
from gobot.variant.training.replay import ReplayBuffer
from gobot.variant.training.selfplay import (
    SelfPlayPool,
    allocation_jobs,
    outcomes_to_chunk,
    summarize,
    write_debug,
)

MIN_FREE_BYTES = 2 * 2**30


class Interrupted(Exception):
    pass


def git_commit(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class Pipeline:
    def __init__(self, run_dir: Path, preset: Preset, settings: Any, katago_spec: dict[str, Any] | None):
        self.dir = run_dir
        self.p = preset
        self.settings = settings
        self.katago = katago_spec
        self.stop = False
        self.state_path = run_dir / "run_state.json"
        self.log_path = run_dir / "log.jsonl"
        self.replay = ReplayBuffer(run_dir / "replay", preset.replay_max_samples, preset.replay_max_bytes)
        self.t_start = time.monotonic()
        self.state: dict[str, Any] = {}
        self.trainer: Any = None
        self.pool: SelfPlayPool | None = None

    # ------------------------------------------------------------------ bookkeeping
    def log(self, event: str, **kw: Any) -> None:
        rec = {"t": round(time.time(), 1), "elapsed": round(self.elapsed(), 1), "event": event, **kw}
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec, default=float) + "\n")
        if event != "train_step":
            short = {k: v for k, v in kw.items() if not isinstance(v, (list, dict))}
            print(f"[{self.elapsed():7.0f}s] {event} {json.dumps(short, default=float)}", flush=True)

    def elapsed(self) -> float:
        return float(self.state.get("elapsed_before", 0.0)) + time.monotonic() - self.t_start

    def save_state(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        snap = {**self.state, "elapsed_before": self.elapsed()}
        tmp.write_text(json.dumps(snap, indent=2, default=float) + "\n")
        tmp.replace(self.state_path)

    def check_stop(self) -> None:
        if self.stop:
            raise Interrupted

    def _on_sigint(self, *_: Any) -> None:
        self.stop = True
        if self.trainer is not None:
            self.trainer.request_stop()
        print("\nStopping at the next safe point (Ctrl+C again to force)...", flush=True)
        signal.signal(signal.SIGINT, signal.default_int_handler)

    # ------------------------------------------------------------------ setup
    def setup(self, resume: bool) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if resume:
            if not self.state_path.is_file():
                raise SystemExit(f"nothing to resume in {self.dir}")
            self.state = json.loads(self.state_path.read_text())
        else:
            if self.state_path.exists():
                raise SystemExit(f"{self.dir} already has a run; use 'resume' or another --run name")
            from gobot.variant.training.train import pick_device

            self.state = {
                "phase": "start",
                "generation": 0,
                "best": None,
                "games": 0,
                "history": [],
                "provenance": {},
            }
            (self.dir / "config.json").write_text(
                json.dumps(
                    {
                        "preset": self.p.public(),
                        "git_commit": git_commit(self.settings.root),
                        "hardware": hardware.detect(),
                        "torch_device": str(pick_device(self.p.train.device)),
                        "katago_teacher": self.katago,
                        "created": time.time(),
                    },
                    indent=2,
                    default=str,
                )
                + "\n"
            )
        free = shutil.disk_usage(self.dir).free
        if free < MIN_FREE_BYTES:
            raise SystemExit(
                f"only {free / 2**30:.1f} GB free; training needs at least {MIN_FREE_BYTES / 2**30:.0f} GB"
            )
        from gobot.variant.training.train import Trainer

        self.trainer = Trainer(self.p.net, self.p.train, seed=self.p.seed)
        ck = self.dir / "checkpoints" / "latest.pt"
        if ck.is_file():
            self.trainer.load(ck)
            self.log("resumed", step=self.trainer.step, generation=self.state["generation"])

    def checkpoint(self, tag: str | None = None) -> None:
        d = self.dir / "checkpoints"
        self.trainer.save(d / "latest.pt", {"generation": self.state["generation"]})
        if tag:
            shutil.copyfile(d / "latest.pt", d / f"{tag}.pt")

    # ------------------------------------------------------------------ steps
    def train(self, data: Any, steps: int, label: str, weights: np.ndarray | None = None) -> dict[str, Any]:
        from gobot.variant.training.train import StopRequested

        every = self.p.checkpoint_every_steps
        done = 0
        last: list[dict[str, Any]] = []
        try:
            while done < steps:
                k = min(every, steps - done)
                last = self.trainer.train_steps(
                    data, k, log=lambda m: self.log("train_step", phase=label, **m), weights=weights
                )
                done += k
                self.checkpoint()
        except StopRequested:
            self.checkpoint()
            raise Interrupted from None
        tail = last[-min(len(last), 20) :]
        return {
            k: float(np.mean([m[k] for m in tail]))
            for k in ("loss", "policy_ce", "value_bce", "score_huber", "policy_top1", "grad_norm")
        }

    def export(self, name: str, provenance: dict[str, Any]) -> Path:
        path = self.dir / "models" / name / "model.onnx"
        if path.is_file():
            return path
        meta = {
            "id": f"{self.dir.name}-{name}",
            "label": f"bonus-net {self.dir.name}/{name}",
            "sizes": [self.p.size],
            "provenance": provenance,
            "preset": self.p.name,
            "run": self.dir.name,
            "created": time.time(),
            "score_scale": 1.0,
        }
        return self.trainer.export_onnx(path, self.p.size, meta)

    def distill(self) -> None:
        if (self.dir / "models" / "gen_000" / "model.onnx").is_file():
            return
        tag = "random-init"
        if self.p.distill_positions > 0 and self.katago is not None:
            teacher = self.replay.root / "teacher_000.npz"
            if not teacher.is_file():
                from gobot.variant.models.katago_eval import KataGoEvaluator
                from gobot.variant.training.distill import teacher_positions

                ev = KataGoEvaluator(
                    self.katago["binary"],
                    Path(self.katago["model_path"]),
                    Path(self.katago["config_path"]),
                    self.katago["label"],
                )
                try:
                    t0 = time.monotonic()
                    chunk = teacher_positions(
                        ev,
                        self.p.distill_positions,
                        self.p.size,
                        seed=self.p.seed,
                        should_stop=lambda: self.stop,
                        log=lambda m: self.log("distill_progress", msg=m),
                    )
                finally:
                    ev.close()
                self.replay.add(chunk, "teacher_000")
                self.log(
                    "distill_data",
                    positions=int(chunk["value"].shape[0]),
                    seconds=round(time.monotonic() - t0, 1),
                    teacher=self.katago["label"],
                )
            data = self.replay.load(only_teacher=True)
            m = self.train(data, self.p.distill_steps, "distill")
            self.log("distill_trained", steps=self.p.distill_steps, **m)
            tag = f"distilled from KataGo {self.katago['label']} policy ({self.p.distill_positions} ordinary-Go positions)"
        self.checkpoint("gen_000")
        self.state["provenance"]["gen_000"] = tag
        self.export(
            "gen_000", {"init": tag, "selfplay_generations": 0, "games": 0, "train_step": self.trainer.step}
        )
        self.state["best"] = "gen_000"
        self.state["phase"] = "generations"
        self.save_state()

    def generation(self, g: int) -> None:
        p = self.p
        name = f"gen_{g + 1:03d}"
        si, stage = p.stage_for(g)
        rng = random.Random(p.seed * 1000 + g)
        chunk_path = self.replay.root / f"gen_{g:03d}.npz"
        best = self.state["best"]
        best_path = self.dir / "models" / best / "model.onnx"
        if not chunk_path.is_file():
            previous = [a for st in p.stages[:si] for a in st.allocations]
            jobs = []
            seed_base = (p.seed % 10_000) * 10_000_000 + g * 100_000
            for seed, b, w in allocation_jobs(rng, p.games_per_generation, stage.allocations, seed_base):
                if previous and rng.random() < p.mix_previous:
                    b, w = rng.choice(previous)  # replay earlier settings too (reduces forgetting)
                jobs.append((seed, b, w))
            t0 = time.monotonic()
            assert self.pool is not None
            outs = self.pool.play(best_path, p.selfplay, jobs)
            self.check_stop()
            chunk = outcomes_to_chunk(outs, si, g)
            if chunk is None:
                raise RuntimeError("self-play produced no samples")
            self.replay.add(chunk, f"gen_{g:03d}")
            write_debug(outs, self.dir / "debug" / f"selfplay_gen_{g:03d}.jsonl")
            summary = summarize(outs)
            self.state["games"] += len(outs)
            self.log(
                "selfplay",
                generation=g,
                stage=stage.name,
                model=best,
                seconds=round(time.monotonic() - t0, 1),
                **summary,
            )
            self.state.setdefault("selfplay", {})[str(g)] = {
                **summary,
                "stage": stage.name,
                "model": best,
                "seconds": round(time.monotonic() - t0, 1),
            }
            self.save_state()
        trained = self.dir / "checkpoints" / f"{name}.pt"
        if not trained.is_file():
            data = self.replay.load()
            new = int(np.load(chunk_path)["value"].shape[0])
            steps = int(min(p.max_steps_per_generation, max(50, p.sample_reuse * new / p.train.batch_size)))
            # teacher rows: at most a quarter of what is sampled
            weights = np.ones(len(data), dtype=np.float64)
            teacher = data.kind == 1
            if teacher.any():
                share = teacher.sum() / len(data)
                if share > 0.25:
                    weights[teacher] = 0.25 / share * (1 - share) / 0.75
            t0 = time.monotonic()
            m = self.train(data, steps, f"gen{g}", weights)
            self.checkpoint(name)
            self.log(
                "trained",
                generation=g,
                steps=steps,
                replay=len(data),
                seconds=round(time.monotonic() - t0, 1),
                **m,
            )
            self.state.setdefault("training", {})[str(g)] = {
                "steps": steps,
                "replay": len(data),
                **m,
                "seconds": round(time.monotonic() - t0, 1),
            }
            self.save_state()
        self.export(
            name,
            {
                "init": self.state["provenance"].get("gen_000", "random-init"),
                "selfplay_generations": g + 1,
                "games": self.state["games"],
                "train_step": self.trainer.step,
                "stage": stage.name,
            },
        )
        gate_path = self.dir / "eval" / f"gate_{name}.json"
        if not gate_path.is_file():
            cand = AgentSpec(
                name, "search", "net", str(self.dir / "models" / name / "model.onnx"), p.gate_visits
            )
            cur = AgentSpec(best, "search", "net", str(best_path), p.gate_visits)
            res = play_match(
                cand,
                cur,
                p.gate_pairs,
                p.size,
                [a for st in p.stages[: si + 1] for a in st.allocations],
                workers=p.workers,
                seed_base=8_000_000_000 + g * 10_000,
            )
            promoted = promote(res, p.gate_min_games)
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            gate_path.write_text(
                json.dumps(
                    {**res.public(), "promoted": promoted, "rule": "Wilson 95% lower bound > 0.5"}, indent=2
                )
                + "\n"
            )
            self.log(
                "gate",
                candidate=name,
                best=best,
                score=res.score,
                ci95=res.ci95,
                games=res.games,
                promoted=promoted,
            )
            if promoted:
                self.state["best"] = name
            self.state["history"].append(
                {
                    "generation": g,
                    "candidate": name,
                    "previous_best": best,
                    "promoted": promoted,
                    "score": res.score,
                    "ci95": res.ci95,
                }
            )
        self.state["generation"] = g + 1
        self.save_state()

    def final_eval(self) -> dict[str, Any]:
        p = self.p
        out_path = self.dir / "eval" / "final.json"
        if out_path.is_file():
            return dict(json.loads(out_path.read_text()))
        best = self.state["best"]
        best_path = str(self.dir / "models" / best / "model.onnx")
        me = AgentSpec(f"best:{best}", "search", "net", best_path, p.eval_visits)
        allocs = [(0, 0), (1, 1), (2, 2), (3, 3), (2, 1), (1, 2), (3, 1), (1, 3)]
        opponents = [
            AgentSpec("random", "random"),
            AgentSpec("greedy-heuristic", "greedy"),
            AgentSpec("search+heuristic", "search", "heuristic", visits=p.eval_visits),
            AgentSpec(
                "gen_000 (initial)",
                "search",
                "net",
                str(self.dir / "models" / "gen_000" / "model.onnx"),
                p.eval_visits,
            ),
            AgentSpec(f"blind:{best}", "search", "net", best_path, p.eval_visits, blind=True),
        ]
        mid = [h["candidate"] for h in self.state["history"] if h["promoted"] and h["candidate"] != best]
        if mid:
            m = mid[len(mid) // 2]
            opponents.append(
                AgentSpec(
                    f"earlier:{m}",
                    "search",
                    "net",
                    str(self.dir / "models" / m / "model.onnx"),
                    p.eval_visits,
                )
            )
        results: list[dict[str, Any]] = []
        for opp in opponents:
            self.check_stop()
            if opp.path == best_path and not opp.blind:
                continue
            r = play_match(me, opp, p.eval_pairs, p.size, allocs, workers=p.workers)
            results.append(r.public())
            self.log(
                "eval",
                opponent=opp.name,
                score=r.score,
                ci95=r.ci95,
                wins=r.wins,
                losses=r.losses,
                draws=r.draws,
                truncated=r.truncated,
            )
        if self.katago is not None and p.eval_katago_pairs > 0:
            self.check_stop()
            kg = AgentSpec(
                "search+KataGo-net (standard-Go evaluator)",
                "search",
                "katago",
                visits=p.eval_visits,
                katago=self.katago,
            )
            r = play_match(me, kg, p.eval_katago_pairs, p.size, allocs, workers=1)
            results.append(r.public())
            self.log(
                "eval",
                opponent=kg.name,
                score=r.score,
                ci95=r.ci95,
                wins=r.wins,
                losses=r.losses,
                draws=r.draws,
            )
        targeted = self.targeted(best_path)
        final = {
            "best": best,
            "matches": results,
            "targeted": targeted,
            "eval_visits": p.eval_visits,
            "allocations": allocs,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(final, indent=2, default=float) + "\n")
        return final

    def targeted(self, model_path: str) -> dict[str, Any] | None:
        from gobot.variant.training.targeted import embedded_suite_path, run_targeted

        path = embedded_suite_path(self.settings.root, self.p.size)
        if not path.is_file():
            return None
        return run_targeted(path, model_path, self.p.eval_visits * 4)

    def install(self) -> Path:
        """Copy the best model into the app's model directory with its provenance and evaluation."""
        from gobot.variant.models.registry import models_dir

        best = self.state["best"]
        src = self.dir / "models" / best
        dst = models_dir(self.settings.variant_dir) / f"{self.dir.name}-{best}"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / "model.onnx", dst / "model.onnx")
        meta = json.loads((src / "model.json").read_text())
        final = self.dir / "eval" / "final.json"
        if final.is_file():
            f = json.loads(final.read_text())
            meta["evaluation_summary"] = [
                {"opponent": m["b"], "games": m["games"], "score": m["score"], "ci95": m["ci95"]}
                for m in f["matches"]
            ]
        meta["id"] = dst.name
        (dst / "model.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.log("installed", model=dst.name)
        return dst

    # ------------------------------------------------------------------ main
    def run(self, resume: bool = False, reserve_eval_s: float = 0.0) -> int:
        self.setup(resume)
        prev = signal.signal(signal.SIGINT, self._on_sigint)
        try:
            self.distill()
            self.pool = SelfPlayPool(self.p.workers)
            gen_times: list[float] = []
            while self.state["generation"] < self.p.max_generations:
                g = self.state["generation"]
                budget_left = self.p.max_runtime_s - reserve_eval_s - self.elapsed()
                est = max(gen_times) if gen_times else 0.0
                if self.state["games"] >= self.p.max_games or budget_left < est:
                    self.log(
                        "budget",
                        reason="games" if self.state["games"] >= self.p.max_games else "time",
                        generation=g,
                    )
                    break
                t0 = time.monotonic()
                self.generation(g)
                gen_times.append(time.monotonic() - t0)
            self.pool.close()
            self.pool = None
            self.state["phase"] = "eval"
            self.save_state()
            final = self.final_eval()
            self.state["phase"] = "done"
            self.save_state()
            write_report(self.dir, final)
            where = f"installed as {self.install().name}" if self.p.install else "not installed (preset)"
            print(f"\nDone in {self.elapsed() / 60:.1f} min. Best model {self.state['best']} {where}.")
            print(f"Report: {self.dir / 'eval' / 'report.md'}")
            return 0
        except Exception as e:
            if not (self.stop or isinstance(e, Interrupted)):
                raise
            # Ctrl+C also reaches the self-play workers; whatever they were doing is redone on resume
            self.save_state()
            print(f"Interrupted; resume with: uv run gobot variant pipeline resume --run {self.dir.name}")
            return 130
        finally:
            if self.pool is not None:
                self.pool.close()
            signal.signal(signal.SIGINT, prev)


def write_report(run_dir: Path, final: dict[str, Any]) -> None:
    st = json.loads((run_dir / "run_state.json").read_text())
    cfg = json.loads((run_dir / "config.json").read_text())
    lines = [f"# Training run {run_dir.name}", ""]
    p = cfg["preset"]
    lines += [
        f"- Preset `{p['name']}`: {p['size']}x{p['size']}, network {p['net']['blocks']} blocks x {p['net']['channels']} channels",
        f"- Initialisation: {st['provenance'].get('gen_000', 'random-init')}",
        f"- Self-play games: {st['games']}; generations: {st['generation']}; best model: `{st['best']}`",
        f"- Wall time: {st.get('elapsed_before', 0) / 60:.1f} min; device: {cfg['torch_device']}; git: {cfg.get('git_commit')}",
        "",
        "## Generations",
        "",
        "| gen | stage | games | samples | truncated | B/W wins | mean placements | train steps | policy CE | value BCE | gate score (95% CI) | promoted |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for h in st["history"]:
        g = str(h["generation"])
        sp = st.get("selfplay", {}).get(g, {})
        tr = st.get("training", {}).get(g, {})
        ci = h.get("ci95") or (None, None)
        sc = f"{h['score']:.3f} ({ci[0]:.3f}-{ci[1]:.3f})" if h.get("score") is not None else "-"
        lines.append(
            f"| {g} | {sp.get('stage', '')} | {sp.get('games', '')} | {sp.get('samples', '')} | {sp.get('truncated', '')} | "
            f"{sp.get('black_wins', '')}/{sp.get('white_wins', '')} | {sp.get('mean_placements', 0):.0f} | {tr.get('steps', '')} | "
            f"{tr.get('policy_ce', float('nan')):.3f} | {tr.get('value_bce', float('nan')):.3f} | {sc} | {h['promoted']} |"
        )
    lines += [
        "",
        f"## Final evaluation of `{final['best']}` ({final['eval_visits']} visits per move, paired schedules, alternating colours)",
        "",
    ]
    lines += [
        "| opponent | games | W | D | L | truncated | score | 95% CI | Elo (95% CI) | minutes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in final["matches"]:
        ci = m["ci95"] or (0, 0)
        eci = m["elo_ci95"] or (0, 0)
        score = f"{m['score']:.3f}" if m["score"] is not None else "-"
        lines.append(
            f"| {m['b']} | {m['games']} | {m['wins']} | {m['draws']} | {m['losses']} | {m['truncated']} | {score} | "
            f"{ci[0]:.3f}-{ci[1]:.3f} | {m['elo'] or 0:+.0f} ({eci[0]:+.0f}..{eci[1]:+.0f}) | {m['seconds'] / 60:.1f} |"
        )
    if final.get("targeted"):
        lines += [
            "",
            "## Targeted positions (exact solver ground truth)",
            "",
            "| category | positions | correct | random-move baseline |",
            "|---|---|---|---|",
        ]
        for cat, r in final["targeted"].items():
            lines.append(f"| {cat} | {r['n']} | {r['correct']} | {r['random_baseline']:.2f} |")
    (run_dir / "eval" / "report.md").write_text("\n".join(lines) + "\n")


def load_preset(name: str) -> Preset:
    if name not in PRESETS:
        raise SystemExit(f"unknown preset {name!r}; choose from {', '.join(PRESETS)}")
    return PRESETS[name]


def main_run(
    settings: Any,
    preset_name: str,
    run: str | None,
    resume: bool,
    katago_spec: dict[str, Any] | None,
    reserve_eval_s: float,
) -> int:
    preset = load_preset(preset_name)
    run_name = run or f"{preset_name}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = settings.variant_dir / "runs" / run_name
    if resume:
        cfg = json.loads((run_dir / "config.json").read_text())
        preset = load_preset(cfg["preset"]["name"])
    return Pipeline(run_dir, preset, settings, katago_spec).run(resume, reserve_eval_s)

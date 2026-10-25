# Bonus-turn Go: the variant network and its training

This page records how the variant network is built and trained, what was actually run on this Mac, and what the
results do and do not show. Rules: [BONUS_GO.md](BONUS_GO.md). Code layout: [VARIANT_ARCHITECTURE.md](VARIANT_ARCHITECTURE.md).

**Summary of provenance.** The network that ships with a local run is a *small residual network trained from
scratch by this code*. It is first **distilled** from KataGo's raw policy on ordinary 11×11 Go positions (policy
targets only, labelled as standard-Go teacher targets), then trained by **variant self-play**: bonus-aware search
produces the move targets and the actual results of bonus games produce the value targets. **No KataGo weights are
reused and nothing is fine-tuned from KataGo.** The network is much weaker at Go than KataGo.

## 1. Route A or Route B

### Route A: adapt a pretrained KataGo network (investigated, not taken)

Checked against KataGo's source (`master`, October 2026) and the network server:

| Question | Finding |
|---|---|
| Are trainable checkpoints available? | Yes. katagotraining.org lists a "Raw Checkpoint" zip (`model.ckpt`) next to each `.bin.gz`, e.g. `kata1-b18c384nbt-s9996604416-d4316597426.zip` (214 MB). `clean_checkpoint.py` strips the optimizer before release, so fine-tuning starts with a fresh optimizer. |
| Can the app's `.bin.gz` be trained? | No. It is an inference export; there is no official converter back to PyTorch. Training needs the separate checkpoint. |
| Input features | Input version 7: 22 spatial + 19 global features (`nninputs.h`, `modelconfigs.py`). Global features 0–4 are "previous 1–5 moves were passes"; the last global slot (komi parity) is read by the score-belief head by index. |
| Adding inputs | `model_pytorch.py` hard-codes `Conv2d(22, c_trunk, …)` and `Linear(19, c_trunk)`. New bonus inputs mean a new input version (V8) in C++ (`nninputs`, `nneval`, `modelversion`), in the training-data writer (`trainingwrite.cpp` accepts versions 3–7 only), in the Python loader (`data_processing_pytorch.py` asserts channel counts) and in the exporter. |
| Search and self-play | Alternation is built in: `BoardHistory::makeBoardMoveAssumeLegal` sets the next player to the opponent, the search flips `thread.pla = getOpp(...)`, selfplay (`play.cpp`) alternates, and the history planes are only filled for alternating moves. The only same-colour runs are handicap placements, which reset history and are not searched. |
| Training data | The npz keys would stay, but the channel counts, the pass-history bookkeeping in `globalTargetsNC`, the value targets (bonus games) and the policy targets (who moves) all change. |
| Licence | Networks: KataGo Neural Network License (MIT-style: use, copy, modify, distribute with the notice); code MIT. Fine-tuned weights would be allowed. |

So adapting KataGo properly is a fork of its C++ engine (rules, search, self-play, data writer) plus a new input
version, not just a change of inputs. The weight surgery itself would be easy, and is described in §8 as the
documented path.

### Route B: a separate small network (taken)

This repository already has the variant's rules, chance model and search in Python, so a network trained by its own
self-play loop needs no changes to KataGo. KataGo still helps twice: as a **teacher** for basic Go shape (policy
distillation on ordinary-Go positions) and as an **approximate evaluator** in the bonus search and as an
evaluation opponent.

## 2. Network

`src/gobot/variant/training/net.py`: residual tower (configurable blocks × channels; BatchNorm; global features
added after the stem; KataGo-style global-pooling bias in every second block), policy head with N²+1 logits (pass
from pooled features), value head (logit of **Black's** win probability), score head (Black's final area lead / N²).
Inputs (feature version 1) are documented in `models/features.py` and include every bonus counter, the moves left
this turn, the public next-turn probabilities, komi, superko-forbidden points and the legal mask. The hidden
schedule is never an input. The legal mask is applied outside the network, identically in training and inference.

Exported to ONNX (opset 17, dynamic batch); the app and self-play workers run it with onnxruntime on CPU.
`tests/variant/test_training.py` checks torch–ONNX parity (< 1e-4).

## 3. The training loop

```
distill (KataGo teacher policy, ordinary Go) ─▶ gen_000
loop:  self-play with the best network ─▶ replay window ─▶ train the candidate lineage
       ─▶ export candidate ─▶ gate against the best ─▶ promote or keep ─▶ next generation
final: evaluation suite ─▶ report ─▶ install best model for the app
```

- **Self-play** (`selfplay.py`, `lockstep.py`): many games per worker in lockstep, leaves batched per network;
  Dirichlet root noise; temperature 1 for the first 12 placements then the most-visited move; playout-cap
  randomisation (a full search of `visits` on 25% of moves, which alone produce policy targets, and a fast search
  of `fast_visits` otherwise). Each recorded position stores its public raw facts, the legal mask, the visit
  distribution and the actor; at the end of the game the outcome from Black's perspective and the final lead are
  attached.
- **Truncated games** (3·N² placements) get no value or score target; they are counted in every report.
- **Privileged data** (seeds, hidden schedules, move lists) go to `debug/` only.
- **Replay** (`replay.py`): compressed npz chunks; the window keeps the newest self-play samples up to a sample and a
  byte limit; teacher rows are kept but capped at a quarter of what is sampled.
- **Optimisation** (`train.py`): policy cross-entropy against visit targets, value binary cross-entropy against
  actual outcomes, Huber loss on the final lead; AdamW with weight decay, gradient clipping, non-finite gradients
  skipped and counted; one random rotation/reflection per minibatch, applied to inputs and policy targets alike.
  Colours are never swapped (that would also require swapping komi, bonus resources and outcomes).
- **Checkpoints** hold the model, optimizer, step and all RNG states; `resume` reproduces an uninterrupted run step
  for step (tested). Ctrl+C stops at the next safe point and saves.
- **Curriculum** (`presets.py`): ordinary Go (0/0) → small allocation (1/1) → default (2/2) → a mixture of equal and
  unequal allocations; each generation also plays a share (20%) of earlier stages' settings. Allocations are stored
  as raw counts.
- **Promotion rule**: the candidate plays the current best in paired games (same seed, so the same hidden schedule
  for each colour slot; colours swapped within each pair). It is promoted only if the lower end of the Wilson 95%
  interval of its score is above 0.5, over at least `gate_min_games` scored games. A few wins, or a lower training
  loss, never promote a network.

## 4. Commands

```bash
uv sync --group train                                        # PyTorch + ONNX export (not needed to run the app)
uv run gobot variant bench --preset local_small              # measure this machine first
uv run gobot variant pipeline run --preset smoke_cpu         # a few minutes; proves the whole loop
uv run gobot variant pipeline run --preset local_small --run local-1   # about one hour here
uv run gobot variant pipeline resume --run local-1           # after Ctrl+C or a crash
uv run gobot variant pipeline status [--run local-1]
uv run gobot variant evaluate net:data/variant/models/<id>/model.onnx greedy --pairs 20
uv run gobot variant targeted                                # rebuild the 11x11 targeted suite
uv run gobot variant models                                  # installed networks (the app lists them too)
uv run pytest -q tests/variant                               # incl. training tests when torch is installed
```

Artifacts go to `data/variant/` (git-ignored; `GOBOT_VARIANT_DIR` overrides): `runs/<run>/` and the installed
`models/<id>/model.onnx` + `model.json`. The pipeline refuses to start with less than 2 GB free.

## 5. Presets and budgets

| | `smoke_cpu` | `local_small` (run here) | `gpu_long` (configuration only) |
|---|---|---|---|
| Board | 7×7 | **11×11** | 11×11 |
| Network | 2 blocks × 24 ch | 4 blocks × 48 ch (190k parameters) | 10 blocks × 128 ch |
| Wall-clock cap | 10 min | **60 min**, incl. 15 min kept for evaluation | 48 h |
| Self-play workers / parallel games each | 2 / 8 | 8 / 16 | 32 / 64 |
| Visits: full search / fast search / share of full | 24 / 8 / 50% | 64 / 16 / 25% | 400 / 80 / 25% |
| Games per generation / at most | 24 / 60 | 640 / 6,000 | 4,000 / 240,000 |
| Generations (curriculum 0/0 → 1/1 → 2/2 → mixed) | 1 + 1 | 1 + 2 + 2 + 4 | 4 + 6 + 10 + 40 |
| Placement cap per game (truncation) | 3·N² = 147 | 3·N² = 363 | 363 |
| Replay window | 20k samples / 32 MB | 300k samples / 400 MB | 3M samples / 6 GB |
| Training batch / optimizer steps per generation (at most) | 64 / 60 | 256 / 1,500 (≈4 uses per sample) | 512 / 20,000 |
| Checkpoint every | 20 steps | 250 steps | 2,000 steps |
| Distillation (teacher positions / steps) | 600 / 40 | 24,000 / 800 | 500,000 / 20,000 |
| Gating (pairs / minimum scored games / visits) | 6 / 10 / 12 | 50 / 80 / 32 | 200 / 400 / 200 |
| Final evaluation (pairs per opponent / visits) | 4 / 16 | 30 / 48 (KataGo opponent: 15 pairs) | 200 / 400 |

The pipeline stops starting new generations when the next one would not fit in the time cap (using the slowest
generation so far) or when the game cap is reached, then runs the final evaluation.

### Hardware measured before choosing the budget

`uv run gobot variant bench --preset local_small` on this Mac (Apple M3 Pro, 5 performance + 6 efficiency cores,
18 GB, macOS 26.6; PyTorch 2.14.1 on MPS; onnxruntime 1.31 on CPU):

| Measurement | Value |
|---|---|
| Rules engine, random 11×11 games | 21,700 moves/s (one core) |
| Network evaluations, onnxruntime, 1 thread | 1,510/s (batch 1), 1,660/s (batch 16) |
| Lockstep self-play, one process, 16 games | 3.4 games/s with the *untrained* network (23 moves per game: it passes early) |
| Self-play, 8 worker processes | 9.6 games/s (same caveat) |
| Training on MPS, batch 256 | 17.8 optimizer steps/s |
| Replay storage | 59 bytes per sample (compressed) |

The benchmark's game length comes from an untrained network; distilled and trained networks play several times
longer games, so the real self-play throughput is lower. The run's own per-generation measurements are below.

RESULTS_PLACEHOLDER

## 7. Known approximations and unsupported settings

- **Strength.** A 190k-parameter network trained for an hour is weak at Go. It is far below KataGo on ordinary Go;
  results against the baselines below are the only claims made.
- **Board sizes.** Trained and evaluated on 11×11 only; the app offers it only there. Nothing is claimed for other
  sizes or for allocations, horizons or moves-per-bonus outside those it was trained and evaluated on (allocations
  0–3 per player, H = 30, 3 moves per bonus, komi 7.5).
- **Approximate evaluators.** The KataGo-network evaluator and the heuristic judge positions as ordinary Go; their
  win estimates for bonus positions are approximations, and the app labels them so.
- **Standard KataGo source** ignores future bonuses and, for the current bonus turn, uses artificial passes (see
  BONUS_GO.md §8).
- **Dead stones** are never removed automatically; the score is that of the board as it stands.
- **Imported positions** (photo, analysis branch) have no history: superko only knows positions from the import on,
  and the bonus state is what the user entered.
- **Komi** is the ordinary Chinese 7.5; it has not been recalibrated for bonus play.
- **Undo** never rerolls a trigger, but can show an already-announced turn start before a move is changed.
- **Bonus-blind baseline** is the same trained network with its bonus inputs hidden, searched as if no further bonus
  could trigger — not a separately trained network.
- **Targeted positions** are small bounded endgames (a placement cap scored exactly); they test tactical handling of
  bonuses, not whole-game strategy.

## 8. Route A as a documented path

If a KataGo fork is ever worth it, the transfer would be:

1. Start from a convolutional checkpoint (b18c384nbt, version 15: no Triton/attention kernels needed), loading
   `model` and, if present, `swa_model` from `model.ckpt`.
2. Define input version 8: append the bonus planes/globals (remaining bonuses per player, moves left this turn,
   bonus active, eligible turns, next-turn probabilities, moves per bonus) **before** the last global slot, so the
   score-belief head still finds komi parity at `input_global[:, -1:]`.
3. Zero-pad `conv_spatial.weight` (`[c, 22, 3, 3]` → `[c, 22+k, 3, 3]`) and `linear_global.weight` (`[c, 19]` →
   `[c, 19+m]`), so the network's outputs are unchanged at step 0; do the same for `swa_model`.
4. Change C++: `BoardHistory` and the search to keep the actor across a bonus turn, chance nodes at turn starts,
   the pass-only termination rule, the new history semantics (no alternation), and the data writer (V8 rows, value
   targets from bonus games).
5. Train with a fresh optimizer at a low learning rate, first with the trunk frozen (only the new input weights and
   the heads), then unfreeze in stages while gating against the unmodified network on ordinary Go and against this
   repository's Route-B network on bonus games. Keep the variant checkpoints under their own names and version.

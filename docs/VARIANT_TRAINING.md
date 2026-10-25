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

## 6. What was run on this Mac (25 October 2026)

### CPU smoke test (`smoke_cpu`, run `smoke-1`)

`uv run gobot variant pipeline run --preset smoke_cpu --run smoke-1`: distillation (600 KataGo-labelled 7×7
positions), 2 generations × 24 self-play games, real optimizer steps, 2 gating matches (12 games each, no promotion
— as intended, 12 games cannot pass the rule), the full evaluation suite, a KataGo-evaluator match. **64.5 s wall
time, peak RSS of the main process 784 MB, exit code 0.** The training tests separately prove that weights change,
that a resumed run reproduces the losses of an uninterrupted one step for step, and that Ctrl+C saves a checkpoint
that resumes.

### Local experiment (`local_small`, run `local-1`, best model installed as `local-1-gen_003`)

`uv run gobot variant pipeline run --preset local_small --run local-1` on the M3 Pro (8 self-play workers on CPU,
training on MPS), commit `a485c91`:

- **Distillation**: 24,000 ordinary-Go 11×11 positions labelled with KataGo `tf2-b10c384`'s raw policy in 203 s;
  800 steps; top-1 agreement with the teacher 44%. This is the only use of KataGo during training.
- **Self-play**: 9 generations × 640 games = **5,760 games**, 146,533 policy samples, **0 truncated games**,
  88–101 placements per game, 155–183 s of self-play per generation (≈3.6 games/s with 8 workers).
- **Training**: 3,085 optimizer steps in total (≈255 per generation, ≈16 s each on MPS), 0 skipped for non-finite
  gradients. Policy cross-entropy on the replay window fell from 2.52 to 2.36 and top-1 agreement with the search
  rose from 0.60 to 0.64.
- **Wall time 68.3 min** — 8 minutes over the 60-minute cap: the cap stops new *generations* (they ended after 39
  min), but the final evaluation is not cut short, and its match against search with the KataGo-network evaluator
  alone took 26 min (KataGo answers about 135 one-visit queries/s on this Mac's Metal build; its own benchmark here
  is 110–165 visits/s). Peak memory of all processes 4.4 GB (median 1.7 GB, 19 processes). Disk: 49 MB for the run
  (replay 12 MB, checkpoints 25 MB), 750 KB for the installed model.

Gating (candidate vs current best, 50 paired-schedule pairs = 100 games, 32 visits, promotion iff the Wilson 95%
lower bound > 0.5):

| generation trained after self-play | candidate vs best | score (95% CI) | promoted |
|---|---|---|---|
| 0 (ordinary Go) | gen_001 vs gen_000 (distilled) | 0.77 (0.68–0.84) | yes |
| 1 (1/1) | gen_002 vs gen_001 | 0.48 (0.38–0.58) | no |
| 2 (1/1) | gen_003 vs gen_001 | 0.67 (0.57–0.75) | yes |
| 3 (2/2) | gen_004 vs gen_003 | 0.59 (0.49–0.68) | no |
| 4 (2/2) | gen_005 vs gen_003 | 0.53 (0.43–0.62) | no |
| 5–8 (mixed) | gen_006 … gen_009 vs gen_003 | 0.53, 0.49, 0.59, 0.53 (lower bounds 0.39–0.49) | no |

So the best network is `gen_003` (after 3 generations, 1,920 games); later candidates were not shown to be better
with 100 games. A longer run, more visits or more gating games would be needed to resolve gains of a few percent.

Final evaluation of `gen_003` (48 visits per move, 30 pairs = 60 games per opponent on held-out seeds, paired
schedules, colours alternated, allocations drawn from 0/0, 1/1, 2/2, 3/3, 2/1, 1/2, 3/1, 1/3):

| opponent | games | W–L | score | 95% CI | Elo (95% CI) |
|---|---|---|---|---|---|
| random legal moves | 60 | 60–0 | 1.00 | 0.94–1.00 | ≥ +477 |
| greedy heuristic (documented baseline) | 60 | 60–0 | 1.00 | 0.94–1.00 | ≥ +477 |
| bonus search + heuristic evaluator | 60 | 60–0 | 1.00 | 0.94–1.00 | ≥ +477 |
| `gen_000` (distilled only, before self-play) | 60 | 50–10 | 0.83 | 0.72–0.91 | +280 (+164..+395) |
| `gen_001` (earlier promoted checkpoint) | 60 | 40–20 | 0.67 | 0.54–0.77 | +120 (+28..+213) |
| same network, bonus inputs hidden and bonus-blind search | 60 | 31–29 | 0.52 | 0.39–0.64 | +12 (−75..+99) |
| bonus search + KataGo `tf2-b10c384` network as evaluator ("original evaluator") | 30 | 4–26 | 0.13 | 0.05–0.30 | −325 (−500..−150) |

No game ended in a draw (half-point komi) and none was truncated.

**Bonus information in whole games** (`gobot variant evaluate`, 60 pairs, 128 visits, heavier and unequal
allocations 3/3, 3/1, 1/3, 2/2, 3/0, 0/3):

| A | B | W–L | score | 95% CI |
|---|---|---|---|---|
| trained net, bonus-aware search | same net, bonus inputs hidden, bonus-blind search | 60–60 | 0.50 | 0.41–0.59 |
| heuristic evaluator, bonus-aware search | heuristic evaluator, bonus-blind search | 56–64 | 0.47 | 0.38–0.56 |

**Targeted positions** (50 positions on 11×11: a 5×5 corner inside a settled frame, exact solver ground truth,
192 visits; `tests/fixtures/variant/targeted_11.json`):

| category | bonus search + trained net | bonus-blind search + net without bonus inputs | bonus search + heuristic | random move |
|---|---|---|---|---|
| three-move tactics (bonus now) | 10/10 | 10/10 | 9/10 | 0.38 |
| survival (opponent's next turn is a certain bonus) | **10/10** | **6/10** | 10/10 | 0.20 |
| same board, no bonus coming | 10/10 | 10/10 | 10/10 | 0.88 |
| no bonuses at all | 8/10 | 8/10 | 10/10 | 0.18 |
| unequal allocations (opponent bonus with P = 1/2) | **9/10** | **6/10** | 10/10 | 0.20 |

### What these results show, and what they do not

- Real training happened: distillation and self-play changed the network, and gated self-play generations beat the
  distilled starting point (0.83 over 60 games, CI 0.72–0.91) and an earlier checkpoint (0.67, CI 0.54–0.77).
- The trained network with search is clearly stronger than random play and the heuristic baselines **on 11×11 with
  these allocations**.
- It is **much weaker** than the same search using KataGo's network as the evaluator (0.13). KataGo's network
  judges positions as ordinary Go, so this mainly measures basic Go strength, where KataGo is far ahead.
- Bonus awareness clearly matters in the targeted positions (survival 10/10 vs 6/10, unequal 9/10 vs 6/10), where
  the right move depends on the coming bonus. In whole games at this strength and budget, however, neither the
  trained network nor the heuristic search won measurably more often with bonus information than without it: the
  intervals are wide and centred near 0.5. Turning tactical correctness into game results probably needs a
  stronger network and more search; this run does not show it.
- Nothing here establishes strong play, generalisation to other board sizes or allocations, or that later
  generations are better than `gen_003`.

### Larger runs (`gpu_long`)

From the measurements above (≈3.6 games/s for 640-game generations with a 190k-parameter network, 64/16 visits, 8
CPU workers): `gpu_long` uses a ~1.9M-parameter network (≈10× more evaluation cost) and 400/80 visits (≈4×), so on
the same 8 CPU workers self-play would run at roughly 0.1 games/s. Its 4,000-game generations need a machine
with many more cores (or a GPU inference server for self-play, which this code does not provide) to finish in its
48 h cap. Measure with `gobot variant bench --preset gpu_long` on the target machine first. Disk: about 60 bytes per
replay sample, so its 3M-sample window is about 180 MB.

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

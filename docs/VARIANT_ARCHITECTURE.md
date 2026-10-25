# Bonus-turn Go: architecture

The rules are in [BONUS_GO.md](BONUS_GO.md); training is in [VARIANT_TRAINING.md](VARIANT_TRAINING.md). This page
explains how the code is split, where hidden information lives, and how the three analysis sources work.

## Layers

```
browser (Preact)                       Mac: FastAPI process                          worker processes
┌─────────────────────────┐  REST   ┌──────────────────────────────────────┐   ┌──────────────────────────┐
│ VariantBoardScreen      │ ──────▶ │ web/variant.py   games, actions, 409  │   │ variant/service.py worker│
│ BonusControls, dialogs  │         │ variant/store.py data/variant/games   │   │  run_search(Observation) │
│ AnalysisPanel (sources) │  ws     │ app.py /api/analyze ─┬─ KataGo (std)  │──▶│  evaluators:             │
└─────────────────────────┘ ──────▶ │                      └─ bonus search ─┼──▶│   heuristic | KataGo net │
                                    │ Environment (hidden schedule) ────────┼─✗ │   | trained ONNX net     │
                                    └──────────────────────────────────────┘   └──────────────────────────┘
```

| Module (`src/gobot/variant/`) | Role | Sees the schedule? |
|---|---|---|
| `config.py` | `VariantConfig`: size, komi, rules, allocations, moves per bonus, horizon H, self-play cap | — |
| `rules.py` | Padded-bytearray board, cached chains, incremental Zobrist, positional superko, legal moves | no |
| `scoring.py` | Exact area scoring of the board as it stands | no |
| `state.py` | `VariantState` (public) and its transitions `start_turn`, `apply_action` | no |
| `randomness.py` | Schedule generators and the public conditional probability r/(H−t) | generators only |
| `env.py` | `Environment` and `VariantGame`: **the only holders of a schedule**; undo/redo by replay | **yes** |
| `observation.py` | `Observation` (public), `search_key` | no |
| `serialization.py`, `store.py` | Versioned JSON records; private server files (mode 0600) and public exports | server files only |
| `search/mcts.py`, `search/exact.py` | Stochastic MCTS; exact expectimax for tiny positions | no |
| `models/` | Evaluator interface, heuristic, KataGo-network adapter, feature encoder, ONNX evaluator, registry | no |
| `service.py`, `sources.py` | Worker process for analysis; the three analysis sources | no (receives a pickled public state) |
| `training/` | Lockstep games, self-play, replay, training, distillation, evaluation, pipeline, presets, bench | `lockstep` creates environments; players get observations only |

`tests/variant/test_isolation.py` parses the source of `search/`, `models/`, `observation.py` and `state.py` and
fails if any of them imports `env`, `serialization`, `store` or the schedule generators. It also checks that no
field of `Observation` or `VariantState` carries hidden data and that public JSON never contains a schedule, seed
or salt.

## Why the game lives on the server

The standard game stays in the browser exactly as before (`frontend/src/game/game.ts`). A bonus game is created
and stored on the Mac: the browser sends *actions* (place, pass, undo, redo) with the revision it last saw, and the
server decides whose turn it is. So:

- the hidden schedule never reaches the browser;
- a click cannot grant moves (the API has no colour field);
- one copy of the rules serves the app, the search and training;
- two devices editing the same game get a 409 with the current game instead of diverging.

## State and transitions

```
           apply_action (placement)          apply_action (last move or pass)
in_turn ───────────────────────────▶ in_turn ───────────────────────────────▶ turn_start
   ▲                                                                              │
   │                    start_turn(bonus)  ◀── Environment: schedule[player][t+1] │
   └──────────────────────────────────────    Search: chance node, p = r/(H−t) ◀─┘
                                   two pass-only turns, or the cap ──▶ terminal
```

- `start_turn` validates feasibility: a bonus needs one left; with as many bonuses left as eligible turns it is
  forced. So an infeasible schedule or chance outcome raises instead of silently changing the rules.
- The environment resolves a turn start immediately after the previous turn ends, so public states shown to
  players are never in `turn_start` and the bonus is announced before the move.
- Undo/redo rebuild the environment from the initial state and the action log. The trigger of a personal turn is
  a fixed function of the schedule, so no reroll is possible; recorded `turn_start` events are compared on load.

## Search

`search/mcts.py` (details in its docstring):

- decision nodes for the player to move (the same player throughout a bonus turn), chance nodes at turn starts;
- values are always Black's win probability; the actor maximises its own side's view, with virtual loss and FPU
  from that side; nothing is negated per move;
- chance outcomes use stratified selection so visit shares follow the public probability; the reported value of
  a move followed by a chance node is the probability-weighted value of its outcomes;
- exact scores at terminal states; priors over every legal move;
- evaluation cache keyed by the evaluator's exact inputs (`features.raw_key` for networks: board, recent
  placements, superko and legal masks, every bonus counter, komi), so the same stones with different bonus
  resources never share an evaluation.

`search/exact.py` solves tiny bounded positions exactly; `tests/variant/test_tactics.py` checks that the search finds
the solver's moves, and the training report measures trained networks on 11×11 versions of those positions.

## Analysis sources

| Source | Engine | Labelled |
|---|---|---|
| `katago` | Existing KataGo process; board snapshot with the actor to play, `same_color_moves` = moves left this turn; candidates that violate the variant's superko are dropped | "Standard KataGo (ignores future bonuses)" |
| `variant` + `katago-net` | Bonus search; leaves evaluated by a second KataGo process (the network `scripts/benchmark.sh` measured fastest on this computer, else the light `tf2-b10c384`; one visit per query, board snapshot, policy masked to our legal moves). On this Mac KataGo answers about 135 such queries per second; repeated positions come from a cache | approximate (standard-Go evaluator) |
| `variant` + `heuristic` | Bonus search with the heuristic evaluator (no KataGo needed) | approximate |
| `variant` + `net:<id>` | Bonus search with a trained network (onnxruntime) | trained; offered only for sizes in the model's metadata |

Every result carries `source`, `evaluator {kind, label, modelVersion, approximate, note}`, `budget {visits, seconds}`
and `rules`. The websocket keeps one analysis per client across both sources and rejects stale revisions.

## Network inputs

`models/features.py`, version 1: 18 spatial planes and 18 global features (listed in its docstring): stones, empty,
the last six placements, liberties 1/2/3+ per colour, superko-forbidden points, legal placements; side to move,
both players' remaining bonuses (fraction of H and raw/10), moves left this turn, bonus active, both players'
remaining eligible turns and next-turn probabilities, moves per bonus, komi, pass-only streak, game phase, H.
The same encoder runs in training and inference; the ONNX graph only maps tensors to logits.

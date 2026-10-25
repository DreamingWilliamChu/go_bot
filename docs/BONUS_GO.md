# Bonus-turn Go: rules

This is the rule set the app's **Bonus Mode** plays, its search reasons about, and its variant network is trained
on. The rules code is `src/gobot/variant/` (see [VARIANT_ARCHITECTURE.md](VARIANT_ARCHITECTURE.md)). Everything
marked *provisional* is a game-design setting chosen for this first version, not a claim about how the game must
be played.

## 1. Two controls: Keep color and Bonus Mode

| | Bonus Mode **off** (standard Go) | Bonus Mode **on** (this variant) |
|---|---|---|
| Who moves | Players alternate | Players alternate turns; a bonus turn has several moves |
| Keep color | A manual lock: tap it and the shown colour plays every move until you tap it again. No move limit | **Judge decides** (default): before the first move of a turn it reads "Grant bonus"; the judge taps it to grant the player a bonus, and Keep color locks the player for the bonus moves. **Random**: a read-only indicator that turns on by itself for a bonus turn the Mac scheduled. Either way it turns off by itself when the turn's moves are used up, and it can never add or extend a bonus mid-turn |
| Game state | In the browser (as before) | On the Mac, which holds the hidden bonus schedule |

Bonus Mode is never switched on or off silently in the middle of a game. Turning it on asks whether to start a new
bonus game or a bonus *analysis branch* from the current position. Turning it off returns to the standard game,
which was kept unchanged.

## 2. Board, moves and legality

- Board sizes 9×9, 11×11, 13×13 and 19×19. A trained variant network exists for 11×11 only; on other sizes the
  bonus-aware search uses an approximate evaluator.
- Black plays the first turn unless a position import says otherwise.
- **No suicide.** A placement that leaves its own chain without liberties is illegal, unless it captures.
- **Positional superko.** A placement may not recreate any board position that occurred earlier in the game (since
  the game's initial position), whoever was to move then. This covers simple ko. It needs no special case for
  consecutive moves by one player: only board positions are compared.
- **Passes** are always legal. A pass never creates a position, so it can never violate superko.
- Captures and legality are applied after **every individual move**, including each move of a bonus turn.

## 3. Turns

A *turn* belongs to one player.

- A **normal turn** has exactly one move: a placement or a pass.
- A **bonus turn** has `moves_per_bonus` moves **in total** (default 3), all by the same player. "Three moves" means
  three moves, not one ordinary move plus three more.
- **Passing during any turn ends that turn immediately**; a bonus turn's unused moves are forfeited.
- A turn ends when its moves are used up or its player passes. Then the opponent's turn starts.

Every turn, normal or bonus, counts as one **personal turn** of its player.

### When a bonus starts

Each game uses one of two **triggers**, fixed when the game is created (the app's default is *judge decides*):

- **Judge decides** (§4a): an outside judge, neither player, decides. At the start of a personal turn, before its
  first move, the judge may grant the player one of their remaining bonuses; the turn then becomes a bonus turn.
- **Random** (§4): the Mac's hidden schedule decides at the start of each personal turn whether it is a bonus turn.

Either way, to the players and the program the timing is random: neither ever chooses a bonus.

From then on both are the same:

1. The bonus is **announced before the player chooses a move**.
2. One of that player's bonus opportunities is consumed (remaining count − 1).
3. Keep color turns on for that player (shown as "Keep Black · bonus").
4. The player makes up to `moves_per_bonus` consecutive moves.
5. Keep color turns off when the moves are used up (or the player passes).
6. The opponent's turn starts.

## 4. Random triggering

The number of bonuses alone does not define a random process. This one does:

- Each player has a **personal-turn horizon H**.
- At the start of the game, that player's allocation *a* of **distinct** personal-turn indices is sampled uniformly
  at random from 1..H (every *a*-subset equally likely), independently for the two players.
- The allocation must satisfy 0 ≤ *a* ≤ H.
- A turn is a bonus turn exactly when its personal-turn index is in that player's schedule.
- If the game ends before all scheduled bonuses happen, the unused opportunities lapse.

**Public:** both remaining counts, both personal-turn counts, H, `moves_per_bonus` and this rule.
**Hidden:** the schedule (future trigger timings) and the random generator state. The schedule stays on the Mac;
the browser, the search and the network never see it.

### Conditional probability

Given only public information, after *t* of a player's personal turns have started, with *r* bonuses remaining, the
remaining *r* scheduled turns are a uniformly random *r*-subset of {t+1, …, H}. So the probability that the
player's **next** personal turn is a bonus turn is

```
P(next turn is a bonus) = r / (H − t)        (0 when t ≥ H, which forces r = 0)
```

It is 1 when r = H − t (every remaining eligible turn must be a bonus), and 0 when r = 0. This is the only chance
model the search uses: it samples future bonuses from these observable probabilities, never from the schedule.

### Default settings

| Board | H (provisional) | Reasoning |
|---|---|---|
| 9×9 | 20 | A 9×9 game lasts about 25–30 turns per player |
| **11×11** | **30** | About 35–45 turns per player; with H = 30 the bonuses fall within roughly the first 60 turns of the game |
| 13×13 | 45 | About 60 turns per player |
| 19×19 | 90 | About 120 turns per player |

Defaults: 3 bonuses each, 3 moves per bonus turn. You can change the allocations, moves per bonus and H; the new
values apply to the **next** game (or an explicit restart), never to the counts of a game in progress.

### Equal allocations are not equal value

Two players with the same number of bonuses can still have bonuses of very different value: a three-move turn that
lands in a fight, or right after the opponent's mistake, is worth far more than one in a settled endgame. Timing is
luck. It also interacts with Black's first-move advantage and with komi, which has **not** been recalibrated for
this variant (7.5 is the standard Chinese value).

### Undo, redo and rerolls

Whether a personal turn is a bonus turn is a fixed function of the schedule and the turn's index, so undo, redo,
or playing a different move after undo can **never reroll** a trigger. Each resolved trigger is also recorded as an
explicit event in the game record, and replaying a record checks those events against the schedule. Undo can,
however, let a player see a turn start that was already announced (for example that the opponent's next turn is a
bonus) and then change their own move. This is an analysis tool, not a tournament server.

At creation the Mac publishes a SHA-256 commitment to the schedule (plus a random salt). When the game ends the
schedule and salt are revealed, so the commitment can be checked.

## 4a. Bonuses granted by a judge (the app's default)

- Each player has an allocation of bonuses (0–50; default 3 each). Remaining counts are public.
- **An outside judge decides when they happen**, not the players and not the program. At the start of a player's
  personal turn, before the turn's first move, the judge may grant that player one of their remaining bonuses: the
  turn becomes a bonus turn of `moves_per_bonus` moves in total. In the app the judge taps Keep color ("Grant
  bonus").
- A bonus can be granted only to the player to move, only before that turn's first move, and only with one left.
  It can never be granted mid-turn, added to a bonus turn, or extended. A bonus granted and then followed at once by
  a pass makes a pass-only turn (the bonus is spent). There is no deadline: unused bonuses lapse at game end.
- **To the players and the program the judge's timing is random.** The analysis therefore never suggests using a
  bonus. It treats every future bonus of both players as a chance event with a public model of the judge: the
  remaining bonuses spread evenly over the player's personal turns up to H, so

  ```
  P(next turn is a bonus) = r / max(H − t, r)      (0 with no bonus left)
  ```

  This is §4's probability while the judge keeps within H; if the judge still holds bonuses near or past H, the
  model expects them on the next turns (P reaches 1). H is a setting of the model, not a rule for the judge. A real
  judge may behave differently, so win estimates in judge games are approximations of that model.
- The current turn is analysed as it stands: a normal turn until the judge grants a bonus (analyse again after a
  grant).
- A grant is an entry in the move history like any move: Undo takes back the last entry, whether a move or a
  grant (an undone grant gives the bonus back and the turn is normal again), Redo replays it, and a new move or
  grant after an undo clears the redo list. The game record stores the grant as an action (`"bonus"`) and a
  `bonus_granted` event. The Mac draws no schedule for judge games.
- The trained network was trained on the Mac's random schedule; in judge games its results are labelled
  approximate.

## 5. End of the game

A turn is **pass-only** if its player placed no stone in it: a normal turn that was a pass, or a bonus turn whose
first move was a pass. A bonus turn that placed stones and then passed is **not** pass-only.

The game ends after **two consecutive pass-only turns** (necessarily one by each player). Any turn that places a
stone resets the count. The internal passes some analysis tools fake (§8) are never part of the game record, so they
cannot end a game.

Self-play and search additionally stop a game after 3·N² placements. Such a game is recorded as **truncated**, not
as a win, loss or draw (its positions get no value target in training). Games played in the app have no limit.

## 6. Scoring

Chinese **area scoring** of the board **as it stands** when the game ends:

| Term | Meaning |
|---|---|
| `stones` | Points occupied by that colour's stones |
| `area` | That colour's stones **plus** the empty points in regions bordered only by that colour |
| Black total | Black `area` |
| White total | White `area` + komi |
| Lead | Black total − White total (positive: Black wins) |

`area` already contains the stones, so stones are never added a second time. Komi is configurable and goes to
White; with a half-point komi there are no draws.

### Dead stones

The score counts every stone on the board as alive and every mixed empty region as neutral; it never removes
"dead" groups. That is exact scoring of the supplied board, which is a different question from deciding which
groups *should* be removed. Players settle this the Tromp-Taylor way: capture dead stones before passing. The app
says "scored as the board stands" and does not use an ownership prediction (from KataGo or the variant network) to
adjudicate, because none of them has been validated for this variant. Manual dead-stone marking is not in this
first version.

## 7. Positions without a history (photos, analysis branches)

A photo shows stones, not the bonus history. To analyse a photographed or edited position as a bonus game you must
enter the bonus state explicitly:

- the remaining bonuses of each player,
- how many personal turns each player has started,
- whether the current turn is a normal turn or a bonus turn with *m* moves left,
- `moves_per_bonus` and H,
- how many consecutive pass-only turns just happened (normally 0).

For random-trigger games the Mac validates these (0 ≤ r ≤ H − t) and draws a fresh hidden schedule for the remaining
bonuses, uniform over the remaining eligible turns, so the future is distributed exactly as in §4. For judge games
(§4a) the remaining counts, the turns started (for the probability model) and the current turn's state matter; no
schedule is drawn. Such games are labelled **"history
unknown"**: superko only compares positions from the imported position onwards, and earlier bonus timing is unknown.

## 8. Analysis sources and their limits

| Source | What it is | Limits |
|---|---|---|
| Standard KataGo | KataGo on the current board as an ordinary Go position | Ignores future bonuses. To show the current turn's remaining moves it inserts artificial opponent passes (see below) |
| Bonus search · approximate evaluator | The variant's own stochastic search (correct turns, chance and rules) with KataGo's raw network (or a simple heuristic if KataGo is not available) as the evaluator | The evaluator judges positions as ordinary Go, so its win estimates are approximate |
| Bonus search · trained network | The same search with the variant network trained by self-play | 11×11 only. A small network: much weaker at Go than KataGo |

### What KataGo's artificial passes do

KataGo assumes alternating moves. To make it analyse "Black plays again", the app inserts a White pass and, for
several moves in a row, allows White only passes inside KataGo's search. That helps show an immediate sequence, but:

- **termination**: inside KataGo's tree, a real pass right after an artificial one ends the game, while in this
  variant a pass only forfeits the rest of the turn;
- **history features**: KataGo's network sees the opponent "passing" every other move, which normally signals the
  endgame;
- **records**: the passes are fabricated, so they are never stored in the variant game record or used as variant
  transitions;
- **ko**: for bonus positions the app sends KataGo the current board as a fresh position, so KataGo knows nothing of
  the earlier positions; the app drops candidate moves that the variant's superko rule forbids;
- **future bonuses**: not represented at all.

The bonus search does not use artificial passes: consecutive moves stay with the same player, and the turn switches
only when the turn ends.

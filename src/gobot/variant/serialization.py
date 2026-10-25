"""Versioned save/load of bonus-turn Go games.

Format ``gobot.bonus-go.game`` version 1::

    {
      "format": "gobot.bonus-go.game", "version": 1,
      "id": "...", "created": 1730000000.0, "provenance": "new" | "photo" | "branch" | "import",
      "config": {...VariantConfig...},
      "initial": {...public initial state...},
      "actions": [[x, y] | "pass", ...], "redo": [...], "revision": 12,
      "events": [...turn_start / move / game_end events, as resolved...],
      "commitment": "<sha256 of schedule + salt>",
      "private": {"seed": ..., "salt": "...", "schedule": {"B": [...], "W": [...]}}   # server files only
      "reveal":  {"salt": "...", "schedule": {...}}                                    # exports, game over
    }

Loading replays the actions from the initial state and checks that the regenerated events equal the
recorded ones, so a record either replays deterministically or is rejected.
"""

from __future__ import annotations

from typing import Any

from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.randomness import HiddenSchedule
from gobot.variant.rules import COLOR_NAMES, COLOR_OF, Board
from gobot.variant.state import VariantState

FORMAT = "gobot.bonus-go.game"
VERSION = 1


class RecordError(ValueError):
    """A game record that cannot be loaded (wrong format/version, or it does not replay)."""


def encode_action(action: int, size: int) -> list[int] | str:
    if action == size * size:
        return "pass"
    if action == size * size + 1:
        return "bonus"  # judge trigger: the judge granted a bonus on this turn
    return [action % size, action // size]


def decode_action(a: Any, size: int) -> int:
    if a == "pass":
        return size * size
    if a == "bonus":
        return size * size + 1
    if isinstance(a, list) and len(a) == 2 and all(isinstance(v, int) for v in a):
        x, y = a
        if 0 <= x < size and 0 <= y < size:
            return y * size + x
    raise RecordError(f"bad action {a!r}")


def state_to_json(s: VariantState) -> dict[str, Any]:
    return {
        "stones": [[x, y, COLOR_NAMES[c]] for x, y, c in s.board.stones()],
        "seen": [f"{h:016x}" for h in sorted(s.seen)],
        "toMove": COLOR_NAMES[s.to_move],
        "phase": s.phase,
        "movesLeft": s.moves_left,
        "turnMoves": s.turn_moves,
        "bonusActive": s.bonus_active,
        "placedThisTurn": s.placed_this_turn,
        "remaining": list(s.remaining),
        "turnsStarted": list(s.turns_started),
        "passStreak": s.pass_streak,
        "placements": s.placements,
        "captures": list(s.captures),
        "recent": [[a, COLOR_NAMES[c]] for a, c in s.recent],
        "historyKnown": s.history_known,
    }


def state_from_json(d: dict[str, Any], config: VariantConfig) -> VariantState:
    board = Board.from_stones(config.size, [(x, y, COLOR_OF[c]) for x, y, c in d["stones"]])
    seen = frozenset(int(h, 16) for h in d["seen"])
    if board.hash not in seen:
        raise RecordError("initial position missing from its superko history")
    return VariantState(
        config=config,
        board=board,
        seen=seen,
        to_move=COLOR_OF[d["toMove"]],
        phase=d["phase"],
        moves_left=int(d["movesLeft"]),
        turn_moves=int(d["turnMoves"]),
        bonus_active=bool(d["bonusActive"]),
        placed_this_turn=int(d["placedThisTurn"]),
        remaining=(int(d["remaining"][0]), int(d["remaining"][1])),
        turns_started=(int(d["turnsStarted"][0]), int(d["turnsStarted"][1])),
        pass_streak=int(d["passStreak"]),
        placements=int(d["placements"]),
        captures=(int(d["captures"][0]), int(d["captures"][1])),
        recent=tuple((int(a), COLOR_OF[c]) for a, c in d["recent"]),
        history_known=bool(d["historyKnown"]),
    )


def game_to_json(game: VariantGame, *, private: bool) -> dict[str, Any]:
    """``private=True``: the server's own file (with the hidden schedule). ``False``: an export for
    the user, which reveals the schedule only once the game is over."""
    n = game.initial.config.size
    out: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "id": game.id,
        "created": game.created,
        "provenance": game.provenance,
        "config": game.initial.config.model_dump(),
        "initial": state_to_json(game.initial),
        "actions": [encode_action(a, n) for a in game.actions],
        "redo": [encode_action(a, n) for a in game.redo_stack],
        "revision": game.revision,
        "events": game.env.events,
        "commitment": game.commitment,
    }
    if private:
        out["private"] = {"seed": game.seed, "salt": game.salt, "schedule": game.schedule.to_json()}
    elif game.state.terminal:
        out["reveal"] = {"salt": game.salt, "schedule": game.schedule.to_json()}
    return out


def game_from_json(d: dict[str, Any]) -> VariantGame:
    if d.get("format") != FORMAT:
        raise RecordError("not a bonus-turn Go game record")
    if d.get("version") != VERSION:
        raise RecordError(
            f"unsupported record version {d.get('version')!r} (this app reads version {VERSION})"
        )
    try:
        config = VariantConfig.model_validate(d["config"])
        initial = state_from_json(d["initial"], config)
        priv = d.get("private")
        if not priv:
            raise RecordError("record has no schedule (an export cannot be resumed as the same game)")
        schedule = HiddenSchedule.from_json(priv["schedule"])
        n = config.size
        game = VariantGame(
            initial=initial,
            schedule=schedule,
            seed=int(priv["seed"]),
            salt=str(priv["salt"]),
            id=str(d["id"]),
            created=float(d["created"]),
            provenance=str(d.get("provenance", "new")),
            actions=[decode_action(a, n) for a in d["actions"]],
            redo_stack=[decode_action(a, n) for a in d.get("redo", [])],
            revision=int(d.get("revision", 0)),
        )
    except RecordError:
        raise
    except Exception as e:  # malformed fields, illegal replay, infeasible schedule
        raise RecordError(f"game record does not load: {e}") from e
    if "events" in d and d["events"] != game.env.events:
        raise RecordError("game record does not replay: recorded events differ from the schedule")
    if d.get("commitment") and d["commitment"] != game.commitment:
        raise RecordError("schedule commitment mismatch")
    return game


def game_from_export(d: dict[str, Any], seed: int | None = None) -> VariantGame:
    """Open an *exported* record (no schedule) as a new game with the same history.

    The recorded turn starts fix the past exactly; the future bonuses are drawn afresh from their
    conditional distribution given the public counts at the end of the record (so the opened game is
    a new game, with a new commitment)."""
    import random
    import secrets

    from gobot.variant.randomness import sample_conditional

    if d.get("format") != FORMAT or d.get("version") != VERSION:
        raise RecordError("not a version-1 bonus-turn Go game record")
    try:
        config = VariantConfig.model_validate(d["config"])
        initial = state_from_json(d["initial"], config)
        n = config.size
        actions = [decode_action(a, n) for a in d["actions"]]
        past: dict[str, set[int]] = {"B": set(), "W": set()}
        last_seen = {"B": 0, "W": 0}
        for e in d.get("events", []):
            if e.get("type") == "turn_start":
                last_seen[e["player"]] = max(last_seen[e["player"]], int(e["turnIndex"]))
                if e["bonus"]:
                    past[e["player"]].add(int(e["turnIndex"]))
        # replay the past with exactly the recorded triggers (nothing after them is bonus yet)
        probe = VariantGame(initial, HiddenSchedule(frozenset(past["B"]), frozenset(past["W"])), 0, "")
        for a in actions:
            probe.play(a)
        if probe.env.events != d.get("events", probe.env.events):
            raise RecordError("recorded events do not match the moves")
        s = probe.state
        seed = secrets.randbits(63) if seed is None else seed
        rng = random.Random(seed)
        if config.trigger == "judge":
            schedule = HiddenSchedule(frozenset(), frozenset())  # the judge decides: nothing to draw
        else:
            future = sample_conditional(s.remaining, s.turns_started, config.horizon, rng)
            schedule = HiddenSchedule(
                frozenset(past["B"]) | future.black, frozenset(past["W"]) | future.white
            )
        game = VariantGame(initial, schedule, seed, f"{rng.getrandbits(128):032x}", provenance="import")
        for a in actions:
            game.play(a)
    except RecordError:
        raise
    except Exception as e:
        raise RecordError(f"game record does not load: {e}") from e
    return game

"""The environment: the only place that holds a game's hidden bonus schedule.

``Environment`` applies actions to the public state and resolves every turn start from the schedule
the moment the previous turn ends, so the bonus is announced before the player chooses a move. Each
resolution is recorded as an explicit ``turn_start`` event.

``VariantGame`` adds undo/redo on top: it keeps the initial public state, the action log and the
schedule, and rebuilds the environment by replay. Because a turn's trigger is a fixed function of
(player, personal-turn index) in the schedule, undo, redo and playing a different move after undo
can never reroll a trigger.
"""

from __future__ import annotations

import random
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from gobot.variant.config import VariantConfig
from gobot.variant.observation import Observation, observe
from gobot.variant.randomness import HiddenSchedule, commitment, sample_conditional, sample_schedule
from gobot.variant.rules import COLOR_NAMES, IllegalMove
from gobot.variant.state import TERMINAL, TURN_START, VariantState, apply_action, initial_state, start_turn


def event_turn_start(state_before: VariantState, bonus: bool, probability: float) -> dict[str, Any]:
    p = state_before.to_move
    return {
        "type": "turn_start",
        "player": COLOR_NAMES[p],
        "turnIndex": state_before.turns_started[p - 1] + 1,
        "bonus": bonus,
        "moves": state_before.config.moves_per_bonus if bonus else 1,
        "probability": probability,  # the public probability it had (for display and audits)
    }


class Environment:
    def __init__(self, initial: VariantState, schedule: HiddenSchedule):
        self.initial = initial
        self.schedule = schedule
        self.state = initial
        self.actions: list[int] = []
        self.events: list[dict[str, Any]] = []
        self._resolve()

    def _resolve(self) -> None:
        s = self.state
        if s.phase != TURN_START:
            return
        p = s.to_move
        idx = s.turns_started[p - 1] + 1
        bonus = self.schedule.is_bonus(p, idx)
        prob = s.trigger_probability(p)
        self.state = start_turn(s, bonus)  # raises if the schedule disagrees with the public counts
        self.events.append(event_turn_start(s, bonus, prob))

    def step(self, action: int) -> list[dict[str, Any]]:
        """Apply ``action`` for the player to move; returns the new events."""
        n0 = len(self.events)
        before = self.state
        after = apply_action(before, action)
        n = before.config.size
        if action == before.config.bonus_action:
            # choice trigger: the player decided to spend a bonus on this turn (announced before a move)
            self.state = after
            self.actions.append(action)
            self.events.append(
                {
                    "type": "bonus_used",
                    "player": COLOR_NAMES[before.to_move],
                    "turnIndex": before.turns_started[before.to_move - 1],
                    "moves": after.moves_left,
                }
            )
            return self.events[n0:]
        ev: dict[str, Any] = {"type": "move", "player": COLOR_NAMES[before.to_move]}
        if action == before.config.pass_action:
            ev["pass"] = True
        else:
            ev.update(x=action % n, y=action // n)
            ev["captured"] = sum(after.captures) - sum(before.captures)
        ev["turnEnded"] = after.phase != "in_turn" or after.to_move != before.to_move
        self.state = after
        self.actions.append(action)
        self.events.append(ev)
        if after.phase == TERMINAL:
            assert after.result is not None
            self.events.append({"type": "game_end", **after.result.public()})
        self._resolve()
        return self.events[n0:]

    def observe(self) -> Observation:
        return observe(self.state)


@dataclass
class VariantGame:
    """A server-side game: public initial state, action log, redo stack and the hidden schedule."""

    initial: VariantState
    schedule: HiddenSchedule
    seed: int
    salt: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    created: float = field(default_factory=time.time)
    provenance: str = "new"  # new | photo | branch | import
    actions: list[int] = field(default_factory=list)
    redo_stack: list[int] = field(default_factory=list)
    revision: int = 0
    env: Environment = field(init=False)

    def __post_init__(self) -> None:
        self._rebuild()

    # ------------------------------------------------------------------ construction
    @classmethod
    def new(cls, config: VariantConfig, seed: int | None = None) -> VariantGame:
        seed = secrets.randbits(63) if seed is None else seed
        rng = random.Random(seed)
        if config.trigger == "choice":
            schedule = HiddenSchedule(frozenset(), frozenset())  # nothing random: players choose
        else:
            schedule = sample_schedule(config.black_bonuses, config.white_bonuses, config.horizon, rng)
        return cls(initial_state(config), schedule, seed, salt=f"{rng.getrandbits(128):032x}")

    @classmethod
    def from_imported(cls, state: VariantState, provenance: str, seed: int | None = None) -> VariantGame:
        """A position entered with an explicit public bonus state; the future schedule is drawn from
        its conditional distribution."""
        seed = secrets.randbits(63) if seed is None else seed
        rng = random.Random(seed)
        if state.config.trigger == "choice":
            schedule = HiddenSchedule(frozenset(), frozenset())
        else:
            schedule = sample_conditional(state.remaining, state.turns_started, state.config.horizon, rng)
        return cls(state, schedule, seed, salt=f"{rng.getrandbits(128):032x}", provenance=provenance)

    def _rebuild(self) -> None:
        env = Environment(self.initial, self.schedule)
        for a in self.actions:
            env.step(a)
        self.env = env

    # ------------------------------------------------------------------ play
    @property
    def state(self) -> VariantState:
        return self.env.state

    @property
    def commitment(self) -> str:
        return commitment(self.schedule, self.salt)

    def play(self, action: int) -> list[dict[str, Any]]:
        events = self.env.step(action)  # raises IllegalMove without changing anything
        self.actions.append(action)
        self.redo_stack.clear()
        self.revision += 1
        return events

    def undo(self) -> bool:
        if not self.actions:
            return False
        self.redo_stack.append(self.actions.pop())
        self._rebuild()
        self.revision += 1
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        a = self.redo_stack.pop()
        try:
            self.env.step(a)
        except IllegalMove:  # pragma: no cover - a redo replays a move that was legal before
            self.redo_stack.clear()
            return False
        self.actions.append(a)
        self.revision += 1
        return True

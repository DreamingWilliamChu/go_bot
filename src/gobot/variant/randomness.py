"""Bonus scheduling: the random process of docs/BONUS_GO.md §4.

Each player's allocation of distinct personal-turn indices is drawn uniformly from 1..H. Given only
public information (``r`` bonuses left after ``t`` personal turns have started), the remaining
scheduled turns are a uniform ``r``-subset of ``t+1..H``, so the next turn triggers with probability
``r / (H - t)``.

This module holds the *math* and the *generators*. The only object holding a drawn schedule is
``gobot.variant.env.Environment``; search and models never import this module (they get the
public probabilities from ``gobot.variant.observation``).
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass


def trigger_probability(remaining: int, turns_started: int, horizon: int) -> float:
    """P(the next personal turn is a bonus turn | public information)."""
    eligible = horizon - turns_started
    if remaining < 0:
        raise ValueError("remaining bonuses cannot be negative")
    if eligible <= 0:
        if remaining:
            raise ValueError(
                f"{remaining} bonus(es) left but no eligible personal turns (t={turns_started}, H={horizon})"
            )
        return 0.0
    if remaining > eligible:
        raise ValueError(
            f"{remaining} bonus(es) left but only {eligible} eligible personal turns remain (t={turns_started}, H={horizon})"
        )
    return remaining / eligible


def check_feasible(remaining: int, turns_started: int, horizon: int) -> None:
    trigger_probability(remaining, turns_started, horizon)


@dataclass(frozen=True, slots=True)
class HiddenSchedule:
    """Personal-turn indices (1-based) that are bonus turns, per player. Environment-only data."""

    black: frozenset[int]
    white: frozenset[int]

    def is_bonus(self, player: int, turn_index: int) -> bool:
        return turn_index in (self.black if player == 1 else self.white)

    def to_json(self) -> dict[str, list[int]]:
        return {"B": sorted(self.black), "W": sorted(self.white)}

    @classmethod
    def from_json(cls, d: dict[str, list[int]]) -> HiddenSchedule:
        return cls(frozenset(int(i) for i in d["B"]), frozenset(int(i) for i in d["W"]))


def _sample(rng: random.Random, remaining: int, turns_started: int, horizon: int) -> frozenset[int]:
    check_feasible(remaining, turns_started, horizon)
    return frozenset(rng.sample(range(turns_started + 1, horizon + 1), remaining))


def sample_schedule(black: int, white: int, horizon: int, rng: random.Random) -> HiddenSchedule:
    """A new game's schedule: each allocation uniform over distinct turns 1..H, independently."""
    return HiddenSchedule(_sample(rng, black, 0, horizon), _sample(rng, white, 0, horizon))


def sample_conditional(
    remaining: tuple[int, int], turns_started: tuple[int, int], horizon: int, rng: random.Random
) -> HiddenSchedule:
    """Schedule for a position imported mid-game: the remaining bonuses uniform over the remaining
    eligible turns (t+1..H), which is exactly their conditional distribution given the public state."""
    return HiddenSchedule(
        _sample(rng, remaining[0], turns_started[0], horizon),
        _sample(rng, remaining[1], turns_started[1], horizon),
    )


def commitment(schedule: HiddenSchedule, salt: str) -> str:
    """SHA-256 commitment published at creation; the schedule and salt are revealed at the end."""
    payload = json.dumps(
        {"schedule": schedule.to_json(), "salt": salt}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()

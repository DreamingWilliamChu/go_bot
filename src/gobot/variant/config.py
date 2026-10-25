"""Validated settings of one bonus-turn Go game (docs/BONUS_GO.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Provisional personal-turn horizons H (docs/BONUS_GO.md §4): roughly each player's number of turns in
# the meaningful part of a game. A game-design setting, not a universal rule.
DEFAULT_HORIZON = {9: 20, 11: 30, 13: 45, 19: 90}
DEFAULT_BONUSES = 3  # per player
DEFAULT_MOVES_PER_BONUS = 3
MAX_SIZE = 25  # GTP letters; the web API restricts sizes further (go.coords.SUPPORTED_SIZES)
MAX_JUDGE_BONUSES = 50
Trigger = Literal["random", "judge"]


def default_horizon(size: int) -> int:
    return DEFAULT_HORIZON.get(size, max(1, round(size * size / 4)))


class VariantConfig(BaseModel):
    """Rules of one game. Frozen: a game's settings never change while it is played."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    size: int = Field(default=11, ge=2, le=MAX_SIZE)
    komi: float = 7.5
    rules: Literal["chinese"] = "chinese"  # area scoring, no suicide, positional superko
    black_bonuses: int = Field(default=DEFAULT_BONUSES, ge=0)
    white_bonuses: int = Field(default=DEFAULT_BONUSES, ge=0)
    moves_per_bonus: int = Field(default=DEFAULT_MOVES_PER_BONUS, ge=2, le=9)
    # random: the Mac's hidden schedule over personal turns 1..H (docs/BONUS_GO.md §4)
    # judge: an outside judge grants bonuses at turn starts; players and the program treat the timing as
    #        random, modelled over personal turns 1..H (§4a)
    trigger: Trigger = "random"
    horizon: int = Field(default=0, ge=0, le=1000)  # 0 / missing: default for the board size
    # Self-play / search only: stop after this many placements and mark the game truncated.
    max_placements: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _default_horizon(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("trigger") == "choice":
            data = {**data, "trigger": "judge"}  # records from before the judge trigger was named
        if isinstance(data, dict) and not data.get("horizon"):
            size = data.get("size", 11)
            if isinstance(size, int):
                data = {**data, "horizon": default_horizon(size)}
        return data

    @model_validator(mode="after")
    def _check(self) -> VariantConfig:
        if self.horizon < 1:
            raise ValueError("horizon must be at least 1")
        if abs(self.komi) > 150 or abs(self.komi * 2 - round(self.komi * 2)) > 1e-9:
            raise ValueError("komi must be a multiple of 0.5 between -150 and 150")
        for name, n in (("black_bonuses", self.black_bonuses), ("white_bonuses", self.white_bonuses)):
            if self.trigger == "random" and n > self.horizon:
                raise ValueError(f"{name} ({n}) must be between 0 and the horizon H ({self.horizon})")
            if self.trigger == "judge" and n > MAX_JUDGE_BONUSES:
                raise ValueError(f"{name} ({n}) must be between 0 and {MAX_JUDGE_BONUSES}")
        return self

    def allocation(self, player: int) -> int:
        """Initial bonus allocation of ``player`` (1 Black, 2 White)."""
        return self.black_bonuses if player == 1 else self.white_bonuses

    @property
    def area(self) -> int:
        return self.size * self.size

    @property
    def pass_action(self) -> int:
        return self.size * self.size

    @property
    def bonus_action(self) -> int:
        """Judge trigger only: the judge grants a bonus on this turn (before its first move). Not a
        player's move: the search never chooses it, and it is outside the network's policy."""
        return self.size * self.size + 1

    def public(self) -> dict[str, object]:
        return self.model_dump()

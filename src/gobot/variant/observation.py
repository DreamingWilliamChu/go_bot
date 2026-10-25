"""What players, the search and networks may see of a bonus-turn Go game.

``Observation`` is built from the public ``VariantState`` only. It has no schedule, seed or random
generator fields, and this module does not import ``gobot.variant.env`` or ``randomness`` generators,
so nothing that receives an Observation can reach the hidden schedule (checked by
``tests/variant/test_isolation.py``). Future bonuses are known only through the public conditional
probabilities ``trigger_probability``.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from gobot.variant.rules import BLACK, WHITE
from gobot.variant.state import VariantState


@dataclass(frozen=True, slots=True)
class Observation:
    state: VariantState  # public by construction
    trigger_probability: tuple[float, float]  # P(next personal turn is a bonus) for (Black, White)

    @property
    def to_move(self) -> int:
        return self.state.to_move


def observe(state: VariantState) -> Observation:
    return Observation(state, (state.trigger_probability(BLACK), state.trigger_probability(WHITE)))


def search_key(state: VariantState) -> bytes:
    """Key of everything observable that matters for play from this state: the board, the turn and
    bonus state of both players, the rules, and the repetition context (a digest of the superko
    history). Identical stones with different bonus resources never share a key."""
    cfg = state.config
    header = struct.pack(
        "<BBBBBBHHHHHHhIdBB",
        cfg.size,
        state.to_move,
        {"in_turn": 0, "turn_start": 1, "terminal": 2}[state.phase],
        state.moves_left,
        state.turn_moves,
        int(state.bonus_active),
        state.remaining[0],
        state.remaining[1],
        state.turns_started[0],
        state.turns_started[1],
        cfg.horizon,
        cfg.moves_per_bonus,
        state.pass_streak,
        cfg.max_placements or 0,
        cfg.komi,
        0,  # rules id: chinese (the only supported rule set)
        state.placed_this_turn,
    )
    history = hashlib.blake2b(digest_size=16)
    for h in sorted(state.seen):
        history.update(h.to_bytes(8, "little"))
    return header + bytes(state.board.cells) + history.digest() + struct.pack("<I", state.placements)


__all__ = ["Observation", "observe", "search_key", "BLACK", "WHITE"]

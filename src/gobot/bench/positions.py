"""Representative benchmark positions, taken from games KataGo played against itself
(src/gobot/bench/games/*.sgf, kata1-b18c384nbt at 2 visits per move)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from gobot.engine.query import Move, Position
from gobot.go.coords import sgf_to_xy

GAMES = Path(__file__).parent / "games"


@dataclass
class BenchPosition:
    name: str
    position: Position


def _moves(size: int) -> list[Move]:
    text = (GAMES / f"selfplay_{size}.sgf").read_text()
    out = []
    for color, val in re.findall(r";([BW])\[([a-z]{0,2})\]", text):
        xy = sgf_to_xy(val, size)
        out.append(Move(color=color, x=xy[0], y=xy[1]) if xy else Move(color=color))
    return out


def positions() -> list[BenchPosition]:
    m19, m13, m9 = _moves(19), _moves(13), _moves(9)
    return [
        BenchPosition("19x19 opening (move 30)", Position(size=19, moves=m19[:30], komi=7.5)),
        BenchPosition("19x19 middle game (move 120)", Position(size=19, moves=m19[:120], komi=7.5)),
        BenchPosition("19x19 endgame (move 220)", Position(size=19, moves=m19[:220], komi=7.5)),
        BenchPosition("13x13 middle game (move 60)", Position(size=13, moves=m13[:60], komi=7.5)),
        BenchPosition("9x9 (move 20)", Position(size=9, moves=m9[:20], komi=7.0)),
    ]

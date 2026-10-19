"""Build KataGo analysis queries and normalise its responses.

Every number shown to the user comes from KataGo. This module only re-labels perspective
(KataGo is configured with ``reportAnalysisWinratesAs = BLACK``) and converts coordinates.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from gobot.go.coords import SUPPORTED_SIZES, gtp_to_xy, xy_to_gtp

Color = Literal["B", "W"]
RULES = (
    "chinese",
    "japanese",
    "korean",
    "aga",
    "tromp-taylor",
    "new-zealand",
    "stone-scoring",
    "chinese-ogs",
    "chinese-kgs",
)
Rules = Literal[
    "chinese",
    "japanese",
    "korean",
    "aga",
    "tromp-taylor",
    "new-zealand",
    "stone-scoring",
    "chinese-ogs",
    "chinese-kgs",
]

MAX_THINK_SECONDS = 600.0
UNLIMITED_VISITS = 10_000_000


class Stone(BaseModel):
    color: Color
    x: int
    y: int


class Move(BaseModel):
    """A move; ``x``/``y`` both ``None`` means pass."""

    color: Color
    x: int | None = None
    y: int | None = None

    @model_validator(mode="after")
    def _both_or_neither(self) -> Move:
        if (self.x is None) != (self.y is None):
            raise ValueError("move needs both x and y, or neither for a pass")
        return self

    @property
    def is_pass(self) -> bool:
        return self.x is None


class Position(BaseModel):
    size: int
    initial_stones: list[Stone] = Field(default_factory=list)
    initial_player: Color = "B"
    moves: list[Move] = Field(default_factory=list)
    rules: Rules = "chinese"
    komi: float = 7.5

    @field_validator("size")
    @classmethod
    def _size(cls, v: int) -> int:
        if v not in SUPPORTED_SIZES:
            raise ValueError(f"board size must be one of {SUPPORTED_SIZES}")
        return v

    @field_validator("komi")
    @classmethod
    def _komi(cls, v: float) -> float:
        if abs(v) > 150:
            raise ValueError("komi out of range")
        if abs(v * 2 - round(v * 2)) > 1e-9:
            raise ValueError("komi must be a multiple of 0.5")
        return float(v)

    @model_validator(mode="after")
    def _check_points(self) -> Position:
        seen: set[tuple[int, int]] = set()
        for s in self.initial_stones:
            if not (0 <= s.x < self.size and 0 <= s.y < self.size):
                raise ValueError(f"stone ({s.x}, {s.y}) is off the board")
            if (s.x, s.y) in seen:
                raise ValueError(f"two initial stones on ({s.x}, {s.y})")
            seen.add((s.x, s.y))
        for m in self.moves:
            if not m.is_pass:
                assert m.x is not None and m.y is not None
                if not (0 <= m.x < self.size and 0 <= m.y < self.size):
                    raise ValueError(f"move ({m.x}, {m.y}) is off the board")
        return self

    def next_player(self) -> Color:
        if not self.moves:
            return self.initial_player
        return "W" if self.moves[-1].color == "B" else "B"


class AnalyzeParams(BaseModel):
    max_time: float = Field(default=6.0, gt=0, le=MAX_THINK_SECONDS)
    max_visits: int | None = Field(default=None, ge=1, le=UNLIMITED_VISITS)
    include_ownership: bool = True
    pv_len: int = Field(default=15, ge=1, le=40)
    report_every: float = Field(default=0.5, ge=0.1, le=10)


def build_query(qid: str, pos: Position, params: AnalyzeParams) -> dict[str, Any]:
    """Build a KataGo analysis query for the final position of ``pos``.

    The photographed/setup position is sent as ``initialStones`` with ``initialPlayer``; no move
    history is invented. Moves actually played afterwards are sent as ``moves`` so KataGo can apply
    captures, ko and superko from that point on.
    """
    n = pos.size
    q: dict[str, Any] = {
        "id": qid,
        "initialStones": [[s.color, xy_to_gtp(s.x, s.y, n)] for s in pos.initial_stones],
        "initialPlayer": pos.initial_player,
        "moves": [
            [m.color, "pass" if m.is_pass else xy_to_gtp(m.x, m.y, n)]  # type: ignore[arg-type]
            for m in pos.moves
        ],
        "rules": pos.rules,
        "komi": pos.komi,
        "boardXSize": n,
        "boardYSize": n,
        "includeOwnership": params.include_ownership,
        "includePVVisits": True,
        "analysisPVLen": params.pv_len,
        "maxVisits": params.max_visits or UNLIMITED_VISITS,
        "overrideSettings": {"maxTime": params.max_time},
        "reportDuringSearchEvery": params.report_every,
    }
    return q


def _vertex(move: str, size: int) -> dict[str, Any]:
    xy = gtp_to_xy(move, size)
    if xy is None:
        return {"gtp": "pass", "x": None, "y": None, "pass": True}
    return {"gtp": xy_to_gtp(*xy, size), "x": xy[0], "y": xy[1], "pass": False}


def _other(c: str) -> str:
    return "W" if c == "B" else "B"


def normalize_response(raw: dict[str, Any], size: int, *, top_n: int | None = None) -> dict[str, Any]:
    """Convert a KataGo analysis response into the app's JSON shape.

    All winrates/score leads are from BLACK's perspective (``reportAnalysisWinratesAs = BLACK``).
    Field names say so explicitly (``blackWinrate``, ``blackScoreLead``).
    """
    out: dict[str, Any] = {
        "id": raw.get("id"),
        "isDuringSearch": bool(raw.get("isDuringSearch", False)),
        "turnNumber": raw.get("turnNumber"),
        "noResults": bool(raw.get("noResults", False)),
        "perspective": "black",
    }
    if out["noResults"] or "rootInfo" not in raw:
        out["candidates"] = []
        return out
    root = raw["rootInfo"]
    player = root.get("currentPlayer", "B")
    out.update(
        {
            "currentPlayer": player,
            "rootVisits": int(root.get("visits", 0)),
            "blackWinrate": float(root["winrate"]),
            "blackScoreLead": float(root["scoreLead"]),
            "scoreStdev": float(root.get("scoreStdev", 0.0)) if "scoreStdev" in root else None,
        }
    )
    cands: list[dict[str, Any]] = []
    for mi in sorted(raw.get("moveInfos", []), key=lambda m: m.get("order", 0)):
        if "isSymmetryOf" in mi:
            # Not separately searched; stats copied from a symmetric move. Skip duplicates.
            continue
        v = _vertex(mi["move"], size)
        pv = []
        color = player
        pv_visits = mi.get("pvVisits") or []
        for i, mv in enumerate(mi.get("pv", [])):
            step = _vertex(mv, size)
            step["color"] = color
            if i < len(pv_visits):
                step["visits"] = int(pv_visits[i])
            pv.append(step)
            color = _other(color)
        cands.append(
            {
                **v,
                "order": int(mi.get("order", len(cands))),
                "visits": int(mi.get("visits", 0)),
                "blackWinrate": float(mi["winrate"]),
                "blackScoreLead": float(mi["scoreLead"]),
                "prior": float(mi.get("prior", 0.0)),
                "lcb": float(mi["lcb"]) if "lcb" in mi else None,
                "pv": pv,
            }
        )
    if top_n is not None:
        cands = cands[:top_n]
    out["candidates"] = cands
    own = raw.get("ownership")
    if own is not None:
        if len(own) != size * size:
            raise ValueError(f"ownership has {len(own)} values, expected {size * size}")
        # Row-major from the top-left point; positive = Black (perspective BLACK).
        out["blackOwnership"] = [round(float(v), 4) for v in own]
    return out

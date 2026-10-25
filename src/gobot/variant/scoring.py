"""Exact Chinese area scoring of a board as it stands (docs/BONUS_GO.md §6).

``area`` already contains the stones; stones are never added to it a second time. No group is ever
removed as dead: this scores the supplied board, which is a different question from estimating which
groups should be removed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from gobot.variant.rules import BLACK, EMPTY, WHITE, Board


@dataclass(frozen=True, slots=True)
class ScoreResult:
    black_stones: int
    white_stones: int
    black_area: int  # Black stones + empty points bordered only by Black
    white_area: int
    komi: float
    black_total: float  # = black_area
    white_total: float  # = white_area + komi
    lead: float  # black_total - white_total; > 0: Black wins
    owner: tuple[int, ...]  # row-major: 1 Black, -1 White, 0 neither (stones carry their colour)

    @property
    def winner(self) -> str | None:
        return "B" if self.lead > 0 else "W" if self.lead < 0 else None

    @property
    def black_value(self) -> float:
        """Outcome from Black's perspective: 1 win, 0 loss, 0.5 draw."""
        return 1.0 if self.lead > 0 else 0.0 if self.lead < 0 else 0.5

    def public(self) -> dict[str, object]:
        d = asdict(self)
        d["owner"] = list(self.owner)
        d["winner"] = self.winner
        return d


def area_score(board: Board, komi: float) -> ScoreResult:
    geo = board.geo
    cells = board.cells
    neigh = geo.neigh
    owner = [0] * geo.area
    region_seen = bytearray(geo.cells)
    black_empty = white_empty = 0
    for a, p in enumerate(geo.points):
        v = cells[p]
        if v == BLACK:
            owner[a] = 1
            continue
        if v == WHITE:
            owner[a] = -1
            continue
        if region_seen[p]:
            continue
        # flood the empty region and note which colours border it
        region = [p]
        region_seen[p] = 1
        touches_b = touches_w = False
        i = 0
        while i < len(region):
            q = region[i]
            i += 1
            for r in neigh[q]:
                c = cells[r]
                if c == EMPTY:
                    if not region_seen[r]:
                        region_seen[r] = 1
                        region.append(r)
                elif c == BLACK:
                    touches_b = True
                elif c == WHITE:
                    touches_w = True
        val = 1 if touches_b and not touches_w else -1 if touches_w and not touches_b else 0
        if val:
            for q in region:
                owner[geo.action[q]] = val
            if val == 1:
                black_empty += len(region)
            else:
                white_empty += len(region)
    bs = board.count(BLACK)
    ws = board.count(WHITE)
    black_area = bs + black_empty
    white_area = ws + white_empty
    black_total = float(black_area)
    white_total = white_area + komi
    return ScoreResult(
        black_stones=bs,
        white_stones=ws,
        black_area=black_area,
        white_area=white_area,
        komi=komi,
        black_total=black_total,
        white_total=white_total,
        lead=black_total - white_total,
        owner=tuple(owner),
    )

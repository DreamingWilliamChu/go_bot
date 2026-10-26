"""Board, legality, captures and positional superko for bonus-turn Go.

Plain Python, tuned for search and self-play throughput:

* the board is a padded 1-D ``bytearray`` of ``(N+2)**2`` cells with a ``BORDER`` ring, so neighbour
  lookups need no bounds checks;
* chain labels, liberty counts and chain hashes come from one lazy flood fill, cached per board;
* Zobrist hashing is incremental; positional superko compares the hash of the resulting position with
  the set of earlier positions (``seen``), whoever was to move.

Colours are ``BLACK = 1`` and ``WHITE = 2``. An *action* is ``y * N + x`` for a placement (``y`` from
the top, as everywhere in the app) and ``N * N`` for a pass (``VariantConfig`` adds the judge's grant
and erases). Who is allowed to move is decided by
``gobot.variant.state``, not here: this module accepts any colour for any placement, which is what
consecutive moves by one player need.
"""

from __future__ import annotations

import functools
import random
from collections.abc import Iterable
from dataclasses import dataclass

EMPTY, BLACK, WHITE, BORDER = 0, 1, 2, 3
COLOR_NAMES = {BLACK: "B", WHITE: "W"}
COLOR_OF = {"B": BLACK, "W": WHITE}


def opponent(color: int) -> int:
    return 3 - color


class IllegalMove(ValueError):
    """An action the rules forbid; ``reason`` is one of occupied, suicide, superko, off-board, turn,
    bonus, empty (an erase where there is no stone)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class Geometry:
    """Static tables for one board size."""

    def __init__(self, n: int):
        if not 2 <= n <= 25:
            raise ValueError(f"unsupported board size {n}")
        self.n = n
        self.stride = s = n + 2
        self.cells = s * s
        self.area = n * n
        self.pass_action = n * n
        # action index (row-major from the top-left) -> padded cell, and back
        self.points: tuple[int, ...] = tuple((y + 1) * s + (x + 1) for y in range(n) for x in range(n))
        action = [-1] * self.cells
        for a, p in enumerate(self.points):
            action[p] = a
        self.action: tuple[int, ...] = tuple(action)
        neigh: list[tuple[int, ...]] = [()] * self.cells
        for p in self.points:
            neigh[p] = (p - 1, p + 1, p - s, p + s)
        self.neigh: tuple[tuple[int, ...], ...] = tuple(neigh)
        rng = random.Random(0x5EED0000 + n)  # fixed: hashes are reproducible across runs
        self.zobrist: tuple[tuple[int, ...], ...] = (
            (0,) * self.cells,
            tuple(rng.getrandbits(64) for _ in range(self.cells)),
            tuple(rng.getrandbits(64) for _ in range(self.cells)),
        )
        empty = bytearray([BORDER]) * self.cells
        for p in self.points:
            empty[p] = EMPTY
        self.empty_cells = bytes(empty)

    def xy(self, action: int) -> tuple[int, int]:
        return action % self.n, action // self.n

    def to_action(self, x: int, y: int) -> int:
        if not (0 <= x < self.n and 0 <= y < self.n):
            raise IllegalMove("off-board", f"point ({x}, {y}) is off the {self.n}x{self.n} board")
        return y * self.n + x


@functools.lru_cache(maxsize=32)
def geometry(n: int) -> Geometry:
    return Geometry(n)


@dataclass(slots=True)
class Chains:
    """Flood-fill result for one board: per-cell chain label and per-chain data."""

    label: list[int]  # cell -> chain index, -1 for empty/border
    color: list[int]
    stones: list[list[int]]
    libs: list[int]  # number of distinct liberties
    hashes: list[int]  # XOR of the chain's Zobrist values


class Board:
    """An immutable-by-convention board position. Use ``play`` to get the next position."""

    __slots__ = ("geo", "cells", "hash", "_chains")

    def __init__(self, geo: Geometry, cells: bytearray | bytes, h: int):
        self.geo = geo
        self.cells = cells
        self.hash = h
        self._chains: Chains | None = None

    # ------------------------------------------------------------------ construction
    @classmethod
    def empty(cls, n: int) -> Board:
        geo = geometry(n)
        return cls(geo, bytearray(geo.empty_cells), 0)

    @classmethod
    def from_stones(cls, n: int, stones: Iterable[tuple[int, int, int]]) -> Board:
        """Board with ``(x, y, color)`` stones. Raises if a point repeats; groups without liberties are
        allowed here (``dead_chains`` reports them; importers reject such positions)."""
        geo = geometry(n)
        cells = bytearray(geo.empty_cells)
        h = 0
        for x, y, c in stones:
            if c not in (BLACK, WHITE):
                raise ValueError(f"bad colour {c!r}")
            p = geo.points[geo.to_action(x, y)]
            if cells[p] != EMPTY:
                raise ValueError(f"two stones on ({x}, {y})")
            cells[p] = c
            h ^= geo.zobrist[c][p]
        return cls(geo, cells, h)

    @classmethod
    def from_rows(cls, rows: list[str]) -> Board:
        """Test helper: rows of ``.``, ``X`` (Black) and ``O`` (White), top row first."""
        n = len(rows)
        stones = []
        for y, row in enumerate(rows):
            row = row.replace(" ", "")
            if len(row) != n:
                raise ValueError("rows must form a square")
            for x, ch in enumerate(row):
                if ch in "Xx":
                    stones.append((x, y, BLACK))
                elif ch in "Oo":
                    stones.append((x, y, WHITE))
                elif ch not in ".+":
                    raise ValueError(f"bad board character {ch!r}")
        return cls.from_stones(n, stones)

    # ------------------------------------------------------------------ queries
    @property
    def n(self) -> int:
        return self.geo.n

    def at(self, x: int, y: int) -> int:
        return self.cells[self.geo.points[self.geo.to_action(x, y)]]

    def at_action(self, action: int) -> int:
        return self.cells[self.geo.points[action]]

    def grid(self) -> list[int]:
        """Row-major colours (0 empty, 1 Black, 2 White) without the border."""
        c = self.cells
        return [c[p] for p in self.geo.points]

    def stones(self) -> list[tuple[int, int, int]]:
        geo = self.geo
        return [(a % geo.n, a // geo.n, self.cells[p]) for a, p in enumerate(geo.points) if self.cells[p]]

    def count(self, color: int) -> int:
        return self.cells.count(color)

    def rows(self) -> list[str]:
        ch = {EMPTY: ".", BLACK: "X", WHITE: "O"}
        g = self.grid()
        n = self.n
        return ["".join(ch[v] for v in g[y * n : (y + 1) * n]) for y in range(n)]

    def chains(self) -> Chains:
        ch = self._chains
        if ch is not None:
            return ch
        geo = self.geo
        cells = self.cells
        neigh = geo.neigh
        zob = geo.zobrist
        label = [-1] * geo.cells
        colors: list[int] = []
        stones_l: list[list[int]] = []
        libs_l: list[int] = []
        hashes: list[int] = []
        for p in geo.points:
            c = cells[p]
            if c == EMPTY or label[p] >= 0:
                continue
            k = len(colors)
            label[p] = k
            members = [p]
            libset: set[int] = set()
            h = 0
            zc = zob[c]
            i = 0
            while i < len(members):
                q = members[i]
                i += 1
                h ^= zc[q]
                for r in neigh[q]:
                    v = cells[r]
                    if v == EMPTY:
                        libset.add(r)
                    elif v == c and label[r] < 0:
                        label[r] = k
                        members.append(r)
            colors.append(c)
            stones_l.append(members)
            libs_l.append(len(libset))
            hashes.append(h)
        ch = Chains(label, colors, stones_l, libs_l, hashes)
        self._chains = ch
        return ch

    def dead_chains(self) -> list[list[tuple[int, int]]]:
        """Chains without liberties (only possible in an imported/edited position)."""
        ch = self.chains()
        geo = self.geo
        return [[geo.xy(geo.action[p]) for p in ch.stones[k]] for k in range(len(ch.libs)) if ch.libs[k] == 0]

    # ------------------------------------------------------------------ moves
    def _captures(self, p: int, color: int) -> tuple[list[int], bool] | None:
        """Chains captured by ``color`` at empty cell ``p`` and whether the stone has a liberty without
        them; None for an occupied cell."""
        cells = self.cells
        if cells[p] != EMPTY:
            return None
        ch = self.chains()
        label = ch.label
        libs = ch.libs
        opp = 3 - color
        caught: list[int] = []
        free = False
        for q in self.geo.neigh[p]:
            v = cells[q]
            if v == EMPTY:
                free = True
            elif v == opp:
                k = label[q]
                if libs[k] == 1 and k not in caught:
                    caught.append(k)
            elif v == color and libs[label[q]] > 1:
                free = True
        return caught, free

    def result_hash(self, action: int, color: int) -> int | None:
        """Hash of the position after ``color`` plays ``action``; None if occupied or suicide."""
        p = self.geo.points[action]
        cap = self._captures(p, color)
        if cap is None:
            return None
        caught, free = cap
        if not caught and not free:
            return None
        h = self.hash ^ self.geo.zobrist[color][p]
        if caught:
            hashes = self.chains().hashes
            for k in caught:
                h ^= hashes[k]
        return h

    def play(
        self, action: int, color: int, seen: frozenset[int] | set[int] | None = None
    ) -> tuple[Board, int]:
        """The next position and the number of stones captured. Raises IllegalMove for an occupied
        point, suicide, or (when ``seen`` is given) a position that repeats an earlier one."""
        geo = self.geo
        if not 0 <= action < geo.area:
            raise IllegalMove("off-board", f"action {action} is not a board point")
        p = geo.points[action]
        cap = self._captures(p, color)
        if cap is None:
            raise IllegalMove("occupied", "That point is occupied.")
        caught, free = cap
        if not caught and not free:
            raise IllegalMove("suicide", "Suicide is not allowed.")
        cells = bytearray(self.cells)
        cells[p] = color
        h = self.hash ^ geo.zobrist[color][p]
        n_cap = 0
        if caught:
            ch = self.chains()
            for k in caught:
                for s in ch.stones[k]:
                    cells[s] = EMPTY
                n_cap += len(ch.stones[k])
                h ^= ch.hashes[k]
        if seen is not None and h in seen:
            raise IllegalMove("superko", "Superko: that would repeat an earlier position.")
        return Board(geo, cells, h), n_cap

    def remove(self, action: int) -> Board:
        """The position without the stone on ``action``: an edit, not a move. Raises IllegalMove for an
        empty point. Taking a stone off only adds liberties, so the result never has dead chains."""
        geo = self.geo
        if not 0 <= action < geo.area:
            raise IllegalMove("off-board", f"action {action} is not a board point")
        p = geo.points[action]
        c = self.cells[p]
        if c == EMPTY:
            raise IllegalMove("empty", "There is no stone there to erase.")
        cells = bytearray(self.cells)
        cells[p] = EMPTY
        return Board(geo, cells, self.hash ^ geo.zobrist[c][p])

    def legal_actions(
        self, color: int, seen: frozenset[int] | set[int], superko_out: list[int] | None = None
    ) -> list[int]:
        """Legal placements for ``color`` (passes are always legal and not included). Points that are
        only forbidden by superko are appended to ``superko_out`` when it is given."""
        geo = self.geo
        cells = self.cells
        neigh = geo.neigh
        zc = geo.zobrist[color]
        h0 = self.hash
        out: list[int] = []
        ch: Chains | None = None
        for a, p in enumerate(geo.points):
            if cells[p] != EMPTY:
                continue
            n1, n2, n3, n4 = neigh[p]
            if cells[n1] == EMPTY or cells[n2] == EMPTY or cells[n3] == EMPTY or cells[n4] == EMPTY:
                # A free neighbour: never suicide. Captures are still possible, so the resulting hash
                # needs them only when an adjacent enemy chain is in atari.
                if ch is None:
                    ch = self.chains()
                h = h0 ^ zc[p]
                opp = 3 - color
                done: list[int] = []
                for q in (n1, n2, n3, n4):
                    if cells[q] == opp:
                        k = ch.label[q]
                        if ch.libs[k] == 1 and k not in done:
                            done.append(k)
                            h ^= ch.hashes[k]
                if h not in seen:
                    out.append(a)
                elif superko_out is not None:
                    superko_out.append(a)
                continue
            rh = self.result_hash(a, color)
            if rh is not None:
                if rh not in seen:
                    out.append(a)
                elif superko_out is not None:
                    superko_out.append(a)
        return out

    def superko_forbidden(self, color: int, seen: frozenset[int] | set[int]) -> list[int]:
        """Empty points that are otherwise legal for ``color`` but would repeat a position."""
        out = []
        for a, p in enumerate(self.geo.points):
            if self.cells[p] != EMPTY:
                continue
            rh = self.result_hash(a, color)
            if rh is not None and rh in seen:
                out.append(a)
        return out

    def liberties_grid(self) -> list[int]:
        """Per point (row-major): liberties of the chain on it, 0 for empty points."""
        ch = self.chains()
        return [ch.libs[ch.label[p]] if ch.label[p] >= 0 else 0 for p in self.geo.points]

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Board) and self.geo.n == other.geo.n and self.cells == other.cells

    def __hash__(self) -> int:
        return self.hash

    def __repr__(self) -> str:
        return "Board(\n  " + "\n  ".join(self.rows()) + "\n)"


def recompute_hash(board: Board) -> int:
    """Zobrist hash from scratch (tests compare it with the incremental one)."""
    geo = board.geo
    h = 0
    for p in geo.points:
        c = board.cells[p]
        if c:
            h ^= geo.zobrist[c][p]
    return h

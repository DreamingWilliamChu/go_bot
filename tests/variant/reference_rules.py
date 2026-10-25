"""Deliberately naive Go rules (2-D lists, recomputed from scratch) to fuzz the fast engine against.

Same conventions as gobot.variant.rules: colours 1/2, (x, y) with y from the top, positional superko
over whole-board positions, no suicide.
"""

from __future__ import annotations

Grid = list[list[int]]


def neighbours(n: int, x: int, y: int):
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        if 0 <= x + dx < n and 0 <= y + dy < n:
            yield x + dx, y + dy


def group(g: Grid, x: int, y: int) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    n = len(g)
    c = g[y][x]
    stones, libs, todo = {(x, y)}, set(), [(x, y)]
    while todo:
        px, py = todo.pop()
        for qx, qy in neighbours(n, px, py):
            v = g[qy][qx]
            if v == 0:
                libs.add((qx, qy))
            elif v == c and (qx, qy) not in stones:
                stones.add((qx, qy))
                todo.append((qx, qy))
    return stones, libs


def key(g: Grid) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(r) for r in g)


def play(g: Grid, x: int, y: int, c: int, seen: set) -> tuple[Grid, int] | None:
    """Next grid and number captured, or None if illegal (occupied, suicide, superko)."""
    n = len(g)
    if g[y][x] != 0:
        return None
    new = [r[:] for r in g]
    new[y][x] = c
    captured = 0
    for qx, qy in neighbours(n, x, y):
        if new[qy][qx] == 3 - c:
            stones, libs = group(new, qx, qy)
            if not libs:
                for sx, sy in stones:
                    new[sy][sx] = 0
                captured += len(stones)
    _, libs = group(new, x, y)
    if not libs:
        return None
    if key(new) in seen:
        return None
    return new, captured


def legal(g: Grid, c: int, seen: set) -> list[tuple[int, int]]:
    n = len(g)
    return [(x, y) for y in range(n) for x in range(n) if play(g, x, y, c, seen) is not None]

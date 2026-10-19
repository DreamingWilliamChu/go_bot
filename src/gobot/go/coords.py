"""Board coordinate conversions.

Canonical internal coordinates are ``(x, y)`` with ``x`` = column counted from the left
(0-based) and ``y`` = row counted from the TOP (0-based). This matches SGF ("aa" is the
top-left point), Sabaki's vertex convention, KataGo's ownership array order (row-major,
starting at A19 on 19x19) and rectified photo grids (image top = board top).

GTP / KataGo move strings use a column letter that skips "I" and a row number counted from
the BOTTOM, so the top-left point of a 19x19 board is "A19" and the bottom-right is "T1".
"""

from __future__ import annotations

GTP_COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # no "I"
SUPPORTED_SIZES = (9, 13, 19)
PASS = "pass"


def check_size(size: int) -> int:
    if not isinstance(size, int) or size < 2 or size > len(GTP_COLUMNS):
        raise ValueError(f"unsupported board size {size!r}")
    return size


def in_bounds(x: int, y: int, size: int) -> bool:
    return 0 <= x < size and 0 <= y < size


def xy_to_gtp(x: int, y: int, size: int) -> str:
    check_size(size)
    if not in_bounds(x, y, size):
        raise ValueError(f"point ({x}, {y}) is off a {size}x{size} board")
    return f"{GTP_COLUMNS[x]}{size - y}"


def gtp_to_xy(move: str, size: int) -> tuple[int, int] | None:
    """Parse a GTP vertex. Returns ``None`` for a pass."""
    check_size(size)
    s = move.strip().upper()
    if s == "PASS":
        return None
    if len(s) < 2:
        raise ValueError(f"bad GTP vertex {move!r}")
    col, row = s[0], s[1:]
    if col not in GTP_COLUMNS or not row.isdigit():
        raise ValueError(f"bad GTP vertex {move!r}")
    x = GTP_COLUMNS.index(col)
    y = size - int(row)
    if not in_bounds(x, y, size):
        raise ValueError(f"GTP vertex {move!r} is off a {size}x{size} board")
    return x, y


def xy_to_sgf(x: int, y: int, size: int) -> str:
    check_size(size)
    if not in_bounds(x, y, size):
        raise ValueError(f"point ({x}, {y}) is off a {size}x{size} board")
    return chr(ord("a") + x) + chr(ord("a") + y)


def sgf_to_xy(value: str, size: int) -> tuple[int, int] | None:
    """Parse an SGF point. Returns ``None`` for a pass ("" or "tt" on boards <= 19)."""
    check_size(size)
    if value == "" or (value == "tt" and size <= 19):
        return None
    if len(value) != 2 or not value.isalpha() or not value.islower():
        raise ValueError(f"bad SGF point {value!r}")
    x = ord(value[0]) - ord("a")
    y = ord(value[1]) - ord("a")
    if not in_bounds(x, y, size):
        raise ValueError(f"SGF point {value!r} is off a {size}x{size} board")
    return x, y


def ownership_index(x: int, y: int, size: int) -> int:
    """Index into KataGo's row-major ownership array (top-left first)."""
    return y * size + x

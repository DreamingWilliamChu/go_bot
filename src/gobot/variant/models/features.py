"""Network inputs for bonus-turn Go, version 1 - the same code for training and inference.

A position is first reduced to a ``Raw`` record of public facts (what the replay buffer stores, with
raw counts), then ``encode`` turns a batch of raws into tensors. Nothing here can see the hidden
schedule: future bonuses enter only as the public counters and conditional probabilities.

Spatial planes (``SPATIAL`` = 18, each N x N, row-major from the top-left):

====  ===========================================================
0     Black stones
1     White stones
2     empty points
3     on the board (all ones; lets one network serve several sizes)
4-9   the last 6 placements, most recent first (either colour)
10-12 Black stones whose chain has 1 / 2 / 3+ liberties
13-15 White stones whose chain has 1 / 2 / 3+ liberties
16    points the player to move may not play because of superko
17    legal placements of the player to move
====  ===========================================================

Global features (``GLOBAL`` = 18): to move is Black, to move is White, remaining bonuses (Black, White)
as fraction of H and raw / 10, moves left in this turn / moves-per-bonus, bonus turn active, remaining
eligible personal turns (Black, White) / H, public next-turn bonus probability (Black, White),
moves-per-bonus / 10, komi / 15 (komi goes to White), pass-only streak, placements / N^2, H / 100.

Outputs: N*N+1 policy logits (pass last) masked to the legal moves from the rules engine, a logit of
Black's win probability, and optionally Black's final area lead / N^2.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from gobot.variant.rules import BLACK, WHITE
from gobot.variant.state import RECENT, VariantState

FEATURE_VERSION = 1
SPATIAL = 18
GLOBAL = 18
# Raw scalar columns (int16) stored per sample; komi is stored separately as float32.
SCALARS = (
    "to_move",
    "rem_b",
    "rem_w",
    "moves_left",
    "turn_moves",
    "bonus_active",
    "started_b",
    "started_w",
    "horizon",
    "k",
    "pass_streak",
    "placements",
)
S = {name: i for i, name in enumerate(SCALARS)}


@dataclass
class Raw:
    """Public facts of one position, as stored in replay. Arrays have a leading batch axis."""

    size: int
    board: np.ndarray  # int8[B, A]: 0 empty, 1 Black, 2 White
    libs: np.ndarray  # int8[B, A]: liberties of the chain on each stone (0 for empty points)
    recent: np.ndarray  # int16[B, RECENT]: last placements (action), most recent last, -1 padding
    superko: np.ndarray  # bool[B, A]
    legal: np.ndarray  # bool[B, A+1] (pass last)
    scalars: np.ndarray  # int16[B, len(SCALARS)]
    komi: np.ndarray  # float32[B]

    def __len__(self) -> int:
        return int(self.board.shape[0])

    def take(self, idx: np.ndarray | slice) -> Raw:
        return Raw(
            self.size,
            self.board[idx],
            self.libs[idx],
            self.recent[idx],
            self.superko[idx],
            self.legal[idx],
            self.scalars[idx],
            self.komi[idx],
        )

    @staticmethod
    def concat(raws: Sequence[Raw]) -> Raw:
        size = raws[0].size
        if any(r.size != size for r in raws):
            raise ValueError("cannot mix board sizes in one batch")
        return Raw(
            size,
            *(
                np.concatenate([getattr(r, f) for r in raws])
                for f in ("board", "libs", "recent", "superko", "legal", "scalars", "komi")
            ),  # type: ignore[arg-type]
        )


def raw_from_state(state: VariantState) -> Raw:
    """The public facts of ``state`` (memoised on the state: search asks for keys, then evaluations)."""
    got = state.memo.get("raw")
    if got is not None:
        return got  # type: ignore[no-any-return]
    raw = _raw_from_state(state)
    state.memo["raw"] = raw
    return raw


def _raw_from_state(state: VariantState) -> Raw:
    cfg = state.config
    a = cfg.area
    board = state.board
    grid = np.frombuffer(bytes(board.grid()), dtype=np.int8)
    libs = np.minimum(np.asarray(board.liberties_grid(), dtype=np.int16), 127).astype(np.int8)
    recent = np.full(RECENT, -1, dtype=np.int16)
    acts = [act for act, _ in state.recent]
    if acts:
        recent[RECENT - len(acts) :] = acts
    sk = np.zeros(a, dtype=bool)
    legal = np.zeros(a + 1, dtype=bool)
    if state.phase == "in_turn":
        legal[state.legal_actions()] = True
        sk[state.superko_forbidden()] = True
    sc = np.array(
        [
            state.to_move,
            state.remaining[0],
            state.remaining[1],
            state.moves_left,
            state.turn_moves,
            int(state.bonus_active),
            state.turns_started[0],
            state.turns_started[1],
            cfg.horizon,
            cfg.moves_per_bonus,
            state.pass_streak,
            state.placements,
        ],
        dtype=np.int16,
    )
    return Raw(
        cfg.size,
        grid[None, :].copy(),
        libs[None, :],
        recent[None, :],
        sk[None, :],
        legal[None, :],
        sc[None, :],
        np.array([cfg.komi], dtype=np.float32),
    )


def _probability(remaining: np.ndarray, started: np.ndarray, horizon: np.ndarray) -> np.ndarray:
    eligible = horizon - started
    return np.where(eligible > 0, remaining / np.maximum(eligible, 1), 0.0)


def encode(raw: Raw, *, blind: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(spatial float32[B, SPATIAL, N, N], global float32[B, GLOBAL], legal bool[B, A+1])``.

    ``blind=True`` hides every bonus input (remaining counts, active flag, probabilities, eligible
    turns): the "no bonus information" evaluation baseline. Moves left in the turn stay visible,
    because the rules force them."""
    b = len(raw)
    n = raw.size
    a = n * n
    board = raw.board.astype(np.int16)
    sp = np.zeros((b, SPATIAL, a), dtype=np.float32)
    sp[:, 0] = board == BLACK
    sp[:, 1] = board == WHITE
    sp[:, 2] = board == 0
    sp[:, 3] = 1.0
    rows = np.arange(b)
    for j in range(RECENT):
        act = raw.recent[:, RECENT - 1 - j]  # most recent first
        ok = act >= 0
        sp[rows[ok], 4 + j, act[ok]] = 1.0
    libs = raw.libs.astype(np.int16)
    for base, color in ((10, BLACK), (13, WHITE)):
        mine = board == color
        sp[:, base] = mine & (libs == 1)
        sp[:, base + 1] = mine & (libs == 2)
        sp[:, base + 2] = mine & (libs >= 3)
    sp[:, 16] = raw.superko
    sp[:, 17] = raw.legal[:, :a]

    sc = raw.scalars.astype(np.float32)
    h = np.maximum(sc[:, S["horizon"]], 1.0)
    k = np.maximum(sc[:, S["k"]], 1.0)
    rem_b, rem_w = sc[:, S["rem_b"]], sc[:, S["rem_w"]]
    st_b, st_w = sc[:, S["started_b"]], sc[:, S["started_w"]]
    gl = np.zeros((b, GLOBAL), dtype=np.float32)
    gl[:, 0] = sc[:, S["to_move"]] == BLACK
    gl[:, 1] = sc[:, S["to_move"]] == WHITE
    gl[:, 2] = rem_b / h
    gl[:, 3] = rem_w / h
    gl[:, 4] = rem_b / 10.0
    gl[:, 5] = rem_w / 10.0
    gl[:, 6] = sc[:, S["moves_left"]] / k
    gl[:, 7] = sc[:, S["bonus_active"]]
    gl[:, 8] = np.maximum(h - st_b, 0) / h
    gl[:, 9] = np.maximum(h - st_w, 0) / h
    gl[:, 10] = _probability(rem_b, st_b, h)
    gl[:, 11] = _probability(rem_w, st_w, h)
    gl[:, 12] = sc[:, S["k"]] / 10.0
    gl[:, 13] = raw.komi / 15.0
    gl[:, 14] = sc[:, S["pass_streak"]]
    gl[:, 15] = sc[:, S["placements"]] / a
    gl[:, 16] = sc[:, S["horizon"]] / 100.0
    gl[:, 17] = 0.0  # reserved (rules id: Chinese is the only supported rule set)
    if blind:
        gl[:, [2, 3, 4, 5, 7, 8, 9, 10, 11]] = 0.0
    return sp.reshape(b, SPATIAL, n, n), gl, raw.legal.copy()


def raw_key(raw: Raw) -> bytes:
    """Cache key of one raw position: every fact the encoded inputs are computed from (liberties
    follow from the board)."""
    h = hashlib.blake2b(digest_size=20)
    for arr in (raw.board, raw.recent, raw.superko, raw.legal, raw.scalars, raw.komi):
        h.update(np.ascontiguousarray(arr).tobytes())
    h.update(bytes([raw.size]))
    return h.digest()


def input_key(spatial: np.ndarray, glob: np.ndarray, legal: np.ndarray) -> bytes:
    """Cache key of one encoded position: a digest of exactly what the network sees."""
    h = hashlib.blake2b(digest_size=20)
    h.update(np.ascontiguousarray(spatial).tobytes())
    h.update(np.ascontiguousarray(glob).tobytes())
    h.update(np.packbits(legal).tobytes())
    return h.digest()


# ---------------------------------------------------------------------- symmetries
def symmetry_perms(n: int) -> np.ndarray:
    """int64[8, N*N]: for symmetry s, ``new[:, i] = old[:, perm[s, i]]`` maps board-indexed arrays."""
    ys, xs = np.divmod(np.arange(n * n), n)
    out = []
    for s in range(8):
        x, y = xs, ys
        if s & 4:
            x, y = y, x  # transpose
        if s & 1:
            x = n - 1 - x  # mirror
        if s & 2:
            y = n - 1 - y  # flip
        out.append(y * n + x)
    return np.stack(out)


def apply_symmetry(raw: Raw, policy: np.ndarray | None, s: int) -> tuple[Raw, np.ndarray | None]:
    """The same position under symmetry ``s`` (with its policy target transformed alike)."""
    if s == 0:
        return raw, policy
    n = raw.size
    a = n * n
    perm = symmetry_perms(n)[s]
    inv = np.argsort(perm)
    legal = raw.legal.copy()
    legal[:, :a] = raw.legal[:, :a][:, perm]
    recent = np.where(raw.recent >= 0, inv[np.maximum(raw.recent, 0)], -1).astype(np.int16)
    out = Raw(
        n,
        raw.board[:, perm],
        raw.libs[:, perm],
        recent,
        raw.superko[:, perm],
        legal,
        raw.scalars,
        raw.komi,
    )
    if policy is None:
        return out, None
    pol = policy.copy()
    pol[:, :a] = policy[:, :a][:, perm]
    return out, pol

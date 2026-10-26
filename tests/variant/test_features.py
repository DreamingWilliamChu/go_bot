import random

import numpy as np
import pytest

from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.models.features import (
    FEATURE_VERSION,
    GLOBAL,
    SPATIAL,
    Raw,
    apply_symmetry,
    encode,
    raw_from_state,
    raw_key,
    symmetry_perms,
)
from gobot.variant.rules import Board
from gobot.variant.state import imported_state


def played(seed: int = 1, n: int = 60) -> list:
    g = VariantGame.new(VariantConfig(size=9, black_bonuses=2, white_bonuses=2, horizon=8), seed=seed)
    rng = random.Random(seed)
    states = [g.state]
    while not g.state.terminal and len(states) < n:
        g.play(rng.choice(g.state.legal_actions()[:-1] or g.state.legal_actions()))
        states.append(g.state)
    return states


def test_shapes_version_and_basic_planes() -> None:
    st = played()[-1]
    sp, gl, legal = encode(raw_from_state(st))
    assert FEATURE_VERSION == 1
    assert sp.shape == (1, SPATIAL, 9, 9) and gl.shape == (1, GLOBAL) and legal.shape == (1, 82)
    grid = np.array(st.board.grid()).reshape(9, 9)
    assert np.array_equal(sp[0, 0], grid == 1) and np.array_equal(sp[0, 1], grid == 2)
    assert np.array_equal(legal[0, :-1].reshape(9, 9), sp[0, 17]) and legal[0, -1]
    last = st.recent[-1][0]
    assert sp[0, 4].reshape(-1)[last] == 1.0


def test_identical_stones_with_different_bonus_resources_differ() -> None:
    cfg = VariantConfig(size=9, black_bonuses=2, white_bonuses=2)
    b = Board.from_stones(9, [(2, 2, 1), (6, 6, 2)])
    a = imported_state(cfg, b, 1, (2, 2), (3, 3))
    c = imported_state(cfg, b, 1, (0, 2), (3, 3))
    ra, rc = raw_from_state(a), raw_from_state(c)
    assert raw_key(ra) != raw_key(rc)
    ga, gc = encode(ra)[1], encode(rc)[1]
    assert not np.array_equal(ga, gc)
    # the bonus-blind encoding hides exactly that difference
    assert np.array_equal(encode(ra, blind=True)[1], encode(rc, blind=True)[1])


def test_batch_encoding_matches_single_encoding() -> None:
    states = played(n=30)
    raws = [raw_from_state(s) for s in states]
    batch = encode(Raw.concat(raws))
    for i, r in enumerate(raws):
        one = encode(r)
        for x, y in zip(batch, one, strict=True):
            assert np.array_equal(x[i], y[0])


@pytest.mark.parametrize("s", range(8))
def test_symmetries_agree_with_the_rules_engine(s: int) -> None:
    """Transforming a position's features equals computing the features of the transformed position."""
    n = 9
    perm = symmetry_perms(n)[s]
    inv = np.argsort(perm)
    for st in played(seed=3 + s, n=40)[::7]:
        raw = raw_from_state(st)
        moved, _ = apply_symmetry(raw, None, s)
        # rebuild the transformed position with the rules engine and compare legality and liberties
        stones = [(int(inv[y * n + x]) % n, int(inv[y * n + x]) // n, c) for x, y, c in st.board.stones()]
        b2 = Board.from_stones(n, stones)
        assert np.array_equal(moved.board[0], np.array(b2.grid(), dtype=np.int8))
        assert np.array_equal(moved.libs[0], np.array(b2.liberties_grid(), dtype=np.int8))
        # without history every superko-forbidden point is an ordinary legal move
        legal2 = set(b2.legal_actions(st.to_move, {b2.hash}))
        legal_t = set(np.flatnonzero(moved.legal[0, :-1])) | set(np.flatnonzero(moved.superko[0]))
        assert legal_t == legal2


def test_symmetry_maps_policy_targets_with_the_board() -> None:
    raw = raw_from_state(played()[10])
    pol = np.zeros((1, 82), np.float32)
    pol[0, 3 * 9 + 1] = 0.7  # (x=1, y=3)
    pol[0, 81] = 0.3  # pass is never moved
    for s in range(8):
        r2, p2 = apply_symmetry(raw, pol, s)
        assert p2 is not None and p2[0, 81] == pytest.approx(0.3)
        i = int(np.flatnonzero(p2[0, :81])[0])
        # the target moved to the same place as a stone on that point would
        assert symmetry_perms(9)[s][i] == 3 * 9 + 1
        assert r2.legal[0, i] == raw.legal[0, 3 * 9 + 1]

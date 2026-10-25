import random

import pytest

from gobot.variant.rules import BLACK, WHITE, Board, IllegalMove, recompute_hash
from tests.variant import reference_rules as ref


def grid_of(b: Board) -> list[list[int]]:
    g = b.grid()
    n = b.n
    return [g[y * n : (y + 1) * n] for y in range(n)]


@pytest.mark.parametrize("n", [3, 5, 9, 11])
def test_fuzz_against_reference_including_same_colour_runs(n: int) -> None:
    rng = random.Random(1000 + n)
    for _game in range(12 if n <= 9 else 6):
        b = Board.empty(n)
        g = grid_of(b)
        seen = {b.hash}
        seen_ref = {ref.key(g)}
        color = BLACK
        for _ in range(n * n * 3):
            legal_fast = sorted(b.legal_actions(color, seen))
            legal_ref = sorted(y * n + x for x, y in ref.legal(g, color, seen_ref))
            assert legal_fast == legal_ref
            # superko-forbidden points are exactly the otherwise-legal ones that repeat a position
            for a in b.superko_forbidden(color, seen):
                assert a not in legal_fast
            if not legal_fast:
                break
            a = rng.choice(legal_fast)
            b2, cap = b.play(a, color, seen)
            g2, cap_ref = ref.play(g, a % n, a // n, color, seen_ref)
            assert grid_of(b2) == g2 and cap == cap_ref
            assert b2.hash == recompute_hash(b2)
            b, g = b2, g2
            seen.add(b.hash)
            seen_ref.add(ref.key(g))
            # irregular colour sequences: same colour several times in a row, as in bonus turns
            if rng.random() < 0.55:
                color = 3 - color


def test_capture_and_suicide() -> None:
    b = Board.from_rows(
        [
            ".X...",
            "XOX..",
            ".....",
            ".....",
            ".....",
        ]
    )
    nb, cap = b.play(2 * 5 + 1, BLACK, {b.hash})  # X at (1, 2) captures O at (1, 1)
    assert cap == 1 and nb.at(1, 1) == 0
    with pytest.raises(IllegalMove) as e:
        b.play(0, WHITE, {b.hash})  # O into the corner (0, 0): no liberties, captures nothing
    assert e.value.reason == "suicide"
    with pytest.raises(IllegalMove) as e:
        b.play(1, WHITE)
    assert e.value.reason == "occupied"


def test_multi_stone_capture_and_capture_beats_suicide() -> None:
    b = Board.from_rows(
        [
            "OOX..",
            "XXX..",
            ".....",
            ".....",
            ".....",
        ]
    )
    # A position entered like this has a chain without liberties; importers use dead_chains to refuse it.
    assert b.dead_chains() == [[(0, 0), (1, 0)]]
    b = Board.from_rows(
        [
            ".OX..",
            "OXX..",
            "XX...",
            ".....",
            ".....",
        ]
    )
    # Black at (0, 0) would have no liberties, but it captures the O stones at (1, 0) and (0, 1)
    nb, cap = b.play(0, BLACK, {b.hash})
    assert cap == 2 and nb.at(1, 0) == 0 and nb.at(0, 1) == 0


def test_superko_forbids_ko_recapture_but_not_after_board_changes() -> None:
    # A ko: White can capture at (2, 1); Black recapturing at (1, 1) at once repeats the position.
    b = Board.from_rows(
        [
            ".XO..",
            "X.XO.",
            ".XO..",
            ".....",
            ".....",
        ]
    )
    seen = {b.hash}
    b1, cap = b.play(1 * 5 + 1, WHITE, seen)  # O at (1, 1) captures X at (2, 1)
    assert cap == 1
    seen.add(b1.hash)
    assert (1 * 5 + 2) not in b1.legal_actions(BLACK, seen)  # immediate recapture: superko
    assert (1 * 5 + 2) in b1.superko_forbidden(BLACK, seen)
    with pytest.raises(IllegalMove) as e:
        b1.play(1 * 5 + 2, BLACK, seen)
    assert e.value.reason == "superko"
    # Black plays elsewhere first (e.g. the first move of a bonus turn): the board differs, so the
    # recapture is legal again as the second move of the same turn.
    b2, _ = b1.play(4 * 5 + 4, BLACK, seen)
    seen.add(b2.hash)
    assert (1 * 5 + 2) in b2.legal_actions(BLACK, seen)


def test_hashes_are_positional_and_reproducible() -> None:
    a = Board.from_stones(9, [(2, 2, BLACK), (3, 3, WHITE)])
    b, _ = Board.empty(9).play(3 * 9 + 3, WHITE)
    b, _ = b.play(2 * 9 + 2, BLACK)
    assert a.hash == b.hash == recompute_hash(a)
    assert a == b

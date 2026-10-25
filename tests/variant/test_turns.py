from dataclasses import replace

import pytest

from gobot.variant.rules import BLACK, WHITE, IllegalMove
from gobot.variant.state import IN_TURN, TERMINAL, TURN_START, start_turn
from tests.variant.helpers import act, env_with, pass_


def test_normal_turns_alternate_and_count_personal_turns() -> None:
    env = env_with(size=5)
    assert env.state.to_move == BLACK and env.state.moves_left == 1 and not env.state.bonus_active
    act(env, 0, 0)
    assert env.state.to_move == WHITE
    act(env, 4, 4)
    assert env.state.to_move == BLACK
    assert env.state.turns_started == (2, 1)


def test_bonus_is_announced_before_the_move_and_gives_k_moves_total() -> None:
    env = env_with(size=5, black={1}, k=3)
    # the very first turn: announced at creation, before any move
    assert env.events == [
        {"type": "turn_start", "player": "B", "turnIndex": 1, "bonus": True, "moves": 3, "probability": 0.1}
    ]
    s = env.state
    assert s.bonus_active and s.moves_left == 3 and s.turn_moves == 3 and s.remaining == (0, 0)
    act(env, 0, 0)
    act(env, 1, 0)
    assert env.state.to_move == BLACK and env.state.moves_left == 1  # Keep color on, bounded
    ev = act(env, 2, 0)
    # three moves total, then the lock is released and White's turn starts
    assert ev[0]["turnEnded"] is True
    assert env.state.to_move == WHITE and not env.state.bonus_active and env.state.moves_left == 1
    assert sum(1 for c in env.state.board.grid() if c == BLACK) == 3


def test_captures_and_legality_apply_after_each_individual_move() -> None:
    # White stone at (1, 1) with liberties (1, 2) and (0, 1) left after the first move.
    env = env_with(
        [
            ".X...",
            ".OX..",
            ".....",
            ".....",
            ".....",
        ],
        black={1},
    )
    act(env, 0, 1)  # move 1: atari
    assert env.state.board.at(1, 1) == WHITE
    ev = act(env, 1, 2)  # move 2: captures
    assert ev[0]["captured"] == 1 and env.state.board.at(1, 1) == 0
    act(env, 1, 1)  # move 3: on the point just captured, legal only because of that capture
    assert env.state.board.at(1, 1) == BLACK
    assert env.state.to_move == WHITE


def test_illegal_bonus_move_changes_nothing() -> None:
    env = env_with(size=5, black={1})
    act(env, 2, 2)
    before = (env.state.board.grid(), env.state.moves_left, len(env.events))
    with pytest.raises(IllegalMove):
        act(env, 2, 2)
    assert (env.state.board.grid(), env.state.moves_left, len(env.events)) == before


def test_pass_ends_a_bonus_turn_and_forfeits_the_rest() -> None:
    env = env_with(size=5, black={1})
    act(env, 2, 2)
    pass_(env)
    assert env.state.to_move == WHITE and env.state.pass_streak == 0  # placed a stone: not pass-only
    pass_(env)  # White passes: one pass-only turn, the game goes on
    assert env.state.phase == IN_TURN and env.state.pass_streak == 1
    act(env, 0, 0)
    assert env.state.pass_streak == 0


def test_two_consecutive_pass_only_turns_end_the_game_with_area_score() -> None:
    env = env_with(size=5, komi=0.5)
    act(env, 2, 2)
    pass_(env)  # W
    ev = pass_(env)  # B
    assert env.state.phase == TERMINAL
    r = env.state.result
    assert r is not None and r.reason == "passes" and not r.truncated
    assert r.score.black_area == 25 and r.score.lead == 24.5 and r.black_value == 1.0
    assert ev[-1]["type"] == "game_end"
    with pytest.raises(IllegalMove):
        pass_(env)


def test_bonus_turn_that_opens_with_a_pass_is_pass_only_and_uses_the_bonus() -> None:
    env = env_with(size=5, white={1})
    pass_(env)  # Black: pass-only
    assert env.state.bonus_active and env.state.remaining == (0, 0)  # White's bonus announced/consumed
    pass_(env)  # White opens the bonus turn with a pass: pass-only too
    assert env.state.phase == TERMINAL


def test_placed_then_passed_bonus_does_not_count_as_pass_only() -> None:
    env = env_with(size=5, white={1})
    pass_(env)  # Black pass-only (streak 1)
    act(env, 0, 0)  # White bonus: one stone
    pass_(env)  # ... then pass: ends the turn, not pass-only
    assert env.state.phase == IN_TURN and env.state.pass_streak == 0
    pass_(env)  # Black pass-only: streak 1 only
    assert env.state.phase == IN_TURN and env.state.pass_streak == 1


def test_zero_allocation_never_triggers() -> None:
    env = env_with(size=5, horizon=3)
    for _ in range(8):
        assert not env.state.bonus_active
        assert env.state.trigger_probability(BLACK) == 0.0 and env.state.trigger_probability(WHITE) == 0.0
        act(env, *divmod(len(env.actions), 5)[::-1])


def test_forced_bonus_when_remaining_equals_eligible_turns() -> None:
    env = env_with(size=5, black={1, 2}, horizon=2, k=2)
    assert env.state.bonus_active
    assert env.state.trigger_probability(BLACK) == 1.0  # 1 left, 1 eligible turn left
    act(env, 0, 0)
    act(env, 1, 0)
    act(env, 4, 4)  # White
    assert env.state.bonus_active and env.state.to_move == BLACK and env.state.turns_started == (2, 1)
    act(env, 0, 4)
    act(env, 1, 4)
    act(env, 4, 0)  # White
    # past the horizon nothing can trigger
    assert env.state.trigger_probability(BLACK) == 0.0 and not env.state.bonus_active


def test_start_turn_rejects_infeasible_outcomes() -> None:
    env = env_with(size=5, black={1, 2}, horizon=2)
    # Black's turn 2 pending with 1 bonus left and 1 eligible turn: the bonus is forced
    pending = replace(env.state, phase=TURN_START, turns_started=(1, 1), remaining=(1, 0), bonus_active=False)
    with pytest.raises(ValueError, match="forced"):
        start_turn(pending, bonus=False)
    with pytest.raises(ValueError, match="no bonus"):
        start_turn(replace(pending, remaining=(0, 0)), bonus=True)


def test_truncation_is_not_an_ordinary_result() -> None:
    env = env_with(size=5, max_placements=3)
    act(env, 0, 0)
    act(env, 1, 1)
    act(env, 2, 2)
    r = env.state.result
    assert env.state.phase == TERMINAL and r is not None
    assert r.truncated and r.reason == "truncated" and r.black_value is None

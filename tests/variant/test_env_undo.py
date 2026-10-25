import random

from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.randomness import HiddenSchedule
from gobot.variant.state import initial_state, public_view


def game(black: set[int], white: set[int], size: int = 5, horizon: int = 6) -> VariantGame:
    cfg = VariantConfig(
        size=size, black_bonuses=len(black), white_bonuses=len(white), horizon=horizon, komi=0.5
    )
    return VariantGame(
        initial_state(cfg), HiddenSchedule(frozenset(black), frozenset(white)), seed=1, salt="s"
    )


def snapshot(g: VariantGame) -> tuple:
    return (public_view(g.state), list(g.env.events))


def test_undo_restores_counters_turn_state_and_events_and_redo_is_identical() -> None:
    g = game({1}, {1, 2})
    marks = [snapshot(g)]
    for a in [0, 1, 2, 24, 23, 22, 10]:  # B bonus (3), W bonus (3), B normal
        g.play(a)
        marks.append(snapshot(g))
    for i in range(len(marks) - 1, 0, -1):
        assert snapshot(g) == marks[i]
        assert g.undo()
    assert snapshot(g) == marks[0]
    assert not g.undo()
    for i in range(1, len(marks)):
        assert g.redo()
        assert snapshot(g) == marks[i]
    assert not g.redo()


def test_mid_bonus_undo_restores_moves_left_and_remaining() -> None:
    g = game({1}, set())
    g.play(0)
    g.play(1)
    assert g.state.moves_left == 1 and g.state.remaining == (0, 0)
    g.undo()
    assert g.state.moves_left == 2 and g.state.bonus_active and g.state.remaining == (0, 0)
    g.undo()
    assert g.state.moves_left == 3


def test_undo_never_rerolls_a_trigger() -> None:
    # White's personal turn 1 is a bonus turn in one schedule and not in the other. Whatever Black
    # plays instead after undo, White's announced turn start stays the same.
    for white, expect in (({1}, True), ({3}, False)):
        g = game(set(), white)
        g.play(12)
        first = g.env.events[-1]
        assert first["type"] == "turn_start" and first["player"] == "W" and first["bonus"] is expect
        rng = random.Random(0)
        for _ in range(15):
            g.undo()
            g.play(rng.choice([a for a in g.state.legal_actions() if a != 25]))
            assert g.env.events[-1]["bonus"] is expect


def test_new_move_after_undo_clears_redo() -> None:
    g = game(set(), set())
    g.play(0)
    g.play(1)
    g.undo()
    assert g.redo_stack == [1]
    g.play(2)
    assert g.redo_stack == [] and not g.redo()


def test_random_games_replay_deterministically_from_the_seed() -> None:
    cfg = VariantConfig(size=7, black_bonuses=3, white_bonuses=2, horizon=8)
    a, b = VariantGame.new(cfg, seed=42), VariantGame.new(cfg, seed=42)
    assert a.schedule == b.schedule and a.salt == b.salt
    rng = random.Random(4)
    while not a.state.terminal and len(a.actions) < 200:
        mv = rng.choice(a.state.legal_actions())
        a.play(mv)
        b.play(mv)
    assert snapshot(a) == snapshot(b)


def test_judge_bonus_undo_redo_and_records() -> None:
    import json

    from gobot.variant.serialization import game_from_json, game_to_json

    g = VariantGame.new(VariantConfig(size=5, black_bonuses=1, white_bonuses=1, trigger="judge"), seed=1)
    bonus = g.state.config.bonus_action
    g.play(bonus)
    g.play(0)
    g.play(1)
    assert g.state.moves_left == 1 and g.state.remaining == (0, 1)
    g.undo()
    g.undo()
    g.undo()
    assert not g.state.bonus_active and g.state.remaining == (1, 1)
    g.redo()
    assert g.state.bonus_active and g.state.remaining == (0, 1)
    d = json.loads(json.dumps(game_to_json(g, private=True)))
    assert d["actions"] == ["bonus"] and d["redo"] == [[1, 0], [0, 0]]
    g2 = game_from_json(d)
    assert public_view(g2.state) == public_view(g.state)

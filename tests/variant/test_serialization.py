import copy
import json
import random

import pytest

from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.rules import BLACK, Board
from gobot.variant.serialization import RecordError, game_from_json, game_to_json
from gobot.variant.state import imported_state, public_view


def played_game(seed: int = 3, moves: int = 60) -> VariantGame:
    g = VariantGame.new(VariantConfig(size=7, black_bonuses=2, white_bonuses=3, horizon=8), seed=seed)
    rng = random.Random(seed)
    while not g.state.terminal and len(g.actions) < moves:
        g.play(rng.choice(g.state.legal_actions()))
    g.undo()
    return g


def roundtrip(d: dict) -> dict:
    return json.loads(json.dumps(d))


def test_save_load_replays_identically() -> None:
    g = played_game()
    d = roundtrip(game_to_json(g, private=True))
    assert d["format"] == "gobot.bonus-go.game" and d["version"] == 1
    g2 = game_from_json(d)
    assert public_view(g2.state) == public_view(g.state)
    assert g2.env.events == g.env.events and g2.redo_stack == g.redo_stack and g2.revision == g.revision
    # and play continues identically (same schedule)
    g.redo()
    g2.redo()
    assert public_view(g2.state) == public_view(g.state)


def test_imported_state_round_trip() -> None:
    cfg = VariantConfig(size=9, black_bonuses=2, white_bonuses=2)
    board = Board.from_stones(9, [(2, 2, 1), (6, 6, 2)])
    st = imported_state(
        cfg, board, BLACK, remaining=(1, 2), turns_started=(5, 4), bonus_active=True, moves_left=2
    )
    g = VariantGame.from_imported(st, "photo", seed=5)
    assert all(t > 5 for t in g.schedule.black) and all(t > 4 for t in g.schedule.white)
    g.play(0)
    g2 = game_from_json(roundtrip(game_to_json(g, private=True)))
    assert public_view(g2.state) == public_view(g.state) and g2.provenance == "photo"
    assert not g2.state.history_known


def test_tampered_records_are_rejected() -> None:
    g = played_game()
    d = roundtrip(game_to_json(g, private=True))
    bad = copy.deepcopy(d)
    turn_starts = [e for e in bad["events"] if e["type"] == "turn_start"]
    turn_starts[3]["bonus"] = not turn_starts[3]["bonus"]
    with pytest.raises(RecordError, match="replay"):
        game_from_json(bad)
    bad = copy.deepcopy(d)
    bad["private"]["salt"] = "x"
    with pytest.raises(RecordError, match="commitment"):
        game_from_json(bad)
    with pytest.raises(RecordError, match="version"):
        game_from_json({**d, "version": 99})
    with pytest.raises(RecordError):
        game_from_json({**d, "actions": d["actions"] + [[99, 99]]})


def test_export_hides_the_schedule_until_the_game_ends() -> None:
    g = played_game(moves=10)
    ex = roundtrip(game_to_json(g, private=False))
    assert "private" not in ex and "reveal" not in ex
    text = json.dumps(ex)
    assert "schedule" not in text and "seed" not in text
    with pytest.raises(RecordError, match="no schedule"):
        game_from_json(ex)
    while not g.state.terminal:
        g.play(g.state.config.pass_action)
    ex = game_to_json(g, private=False)
    assert ex["reveal"]["schedule"] == g.schedule.to_json()

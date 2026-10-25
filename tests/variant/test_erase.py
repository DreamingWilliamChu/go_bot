import json

import pytest
from fastapi.testclient import TestClient

from gobot.app import create_app
from gobot.variant.config import VariantConfig
from gobot.variant.env import VariantGame
from gobot.variant.rules import IllegalMove
from gobot.variant.serialization import decode_action, encode_action, game_from_json, game_to_json
from gobot.variant.state import public_view


def judge_game(size: int = 5, bonuses: int = 2) -> VariantGame:
    cfg = VariantConfig(size=size, trigger="judge", black_bonuses=bonuses, white_bonuses=bonuses, komi=0.5)
    return VariantGame.new(cfg, seed=1)


def at(g: VariantGame, x: int, y: int) -> int:
    return y * g.state.config.size + x


def erase(g: VariantGame, x: int, y: int) -> list[dict]:
    return g.play(g.state.config.erase_action(at(g, x, y)))


def counters(g: VariantGame) -> tuple:
    s = g.state
    return (
        s.to_move,
        s.phase,
        s.moves_left,
        s.turn_moves,
        s.bonus_active,
        s.placed_this_turn,
        s.remaining,
        s.turns_started,
        s.pass_streak,
        s.placements,
        s.captures,
        s.can_grant_bonus,
    )


def test_an_erase_takes_the_stone_off_and_changes_nothing_else() -> None:
    g = judge_game()
    g.play(at(g, 1, 1))  # Black
    g.play(at(g, 3, 3))  # White
    before = counters(g)
    events = erase(g, 1, 1)
    assert events == [{"type": "erase", "x": 1, "y": 1, "color": "B"}]
    assert g.state.board.at(1, 1) == 0 and g.state.board.at(3, 3) == 2
    assert counters(g) == before  # Black to move, a normal turn, the judge may still grant a bonus
    assert g.state.board.hash in g.state.seen  # the new position joins the superko history


def test_an_erase_mid_bonus_turn_keeps_the_moves_left() -> None:
    g = judge_game()
    g.play(g.state.config.bonus_action)
    g.play(at(g, 0, 0))
    assert g.state.moves_left == 2 and g.state.remaining == (1, 2)
    erase(g, 0, 0)
    assert g.state.moves_left == 2 and g.state.bonus_active and g.state.to_move == 1
    assert g.state.remaining == (1, 2)


def test_erasing_an_empty_point_or_after_the_game_is_refused_without_changes() -> None:
    g = judge_game()
    with pytest.raises(IllegalMove) as e:
        erase(g, 2, 2)
    assert e.value.reason == "empty"
    assert g.actions == [] and g.revision == 0
    g.play(at(g, 2, 2))
    g.play(g.state.config.pass_action)
    g.play(g.state.config.pass_action)  # two pass-only turns in a row: game over
    assert g.state.terminal
    with pytest.raises(IllegalMove) as e:
        erase(g, 2, 2)
    assert e.value.reason == "turn"


def test_dead_stones_erased_before_the_final_passes_are_not_counted() -> None:
    g = judge_game()
    g.play(at(g, 0, 0))
    g.play(at(g, 4, 4))
    erase(g, 4, 4)  # White's stone is dead: take it off before passing
    g.play(g.state.config.pass_action)
    g.play(g.state.config.pass_action)
    assert g.state.result is not None
    assert g.state.result.score.black_area == 25 and g.state.result.score.white_area == 0


def test_undo_and_redo_an_erase() -> None:
    g = judge_game()
    g.play(at(g, 1, 1))
    played = public_view(g.state)
    erase(g, 1, 1)
    erased = public_view(g.state)
    assert g.undo()
    assert public_view(g.state) == played and g.env.events[-1]["type"] == "turn_start"
    assert g.redo()
    assert public_view(g.state) == erased and g.env.events[-1]["type"] == "erase"


def test_records_store_erases_and_replay_them() -> None:
    g = judge_game()
    g.play(at(g, 1, 1))
    g.play(at(g, 3, 2))
    erase(g, 3, 2)
    g.play(at(g, 2, 2))
    erase(g, 1, 1)
    g.undo()  # an erase on the redo list too
    d = json.loads(json.dumps(game_to_json(g, private=True)))
    assert d["actions"][2] == {"erase": [3, 2]} and d["redo"] == [{"erase": [1, 1]}]
    g2 = game_from_json(d)
    assert public_view(g2.state) == public_view(g.state) and g2.env.events == g.env.events
    assert g2.redo() and g2.state.board.at(1, 1) == 0


def test_erase_actions_encode_and_decode() -> None:
    n = 9
    cfg = VariantConfig(size=n)
    for point in (0, 40, 80):
        a = cfg.erase_action(point)
        assert cfg.erased_point(a) == point
        assert decode_action(encode_action(a, n), n) == a
    assert cfg.erased_point(cfg.pass_action) is None and cfg.erased_point(cfg.bonus_action) is None
    assert cfg.erased_point(5) is None


def test_erase_through_the_api(tmp_settings) -> None:
    with TestClient(
        create_app(tmp_settings, start_engine=False), client=("127.0.0.1", 50000), base_url="http://localhost"
    ) as c:
        v = c.post("/api/variant/games", json={"config": {"size": 9}}).json()

        def act(view: dict, **action) -> dict:
            r = c.post(
                f"/api/variant/games/{view['gameId']}/actions",
                json={"expectedRevision": view["revision"], "action": action},
            )
            assert r.status_code == 200, r.text
            return r.json()

        v = act(v, type="place", x=4, y=4)
        assert v["state"]["toMove"] == "W"
        r = c.post(
            f"/api/variant/games/{v['gameId']}/actions",
            json={"expectedRevision": v["revision"], "action": {"type": "erase", "x": 0, "y": 0}},
        )
        assert r.status_code == 400 and r.json()["detail"]["reason"] == "empty"
        v = act(v, type="erase", x=4, y=4)
        assert v["state"]["grid"][4 * 9 + 4] == 0 and v["state"]["toMove"] == "W"
        assert v["newEvents"] == [{"type": "erase", "x": 4, "y": 4, "color": "B"}]
        assert v["actions"][-1] == {"erase": [4, 4]} and v["canUndo"]
        v = act(v, type="undo")
        assert v["state"]["grid"][4 * 9 + 4] == 1 and v["canRedo"]

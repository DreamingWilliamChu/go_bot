import pytest
from pydantic import ValidationError

from gobot.engine.query import AnalyzeParams, Move, Position, Stone, build_query, normalize_response


def photo_position(**kw):
    base = dict(
        size=19,
        initial_stones=[Stone(color="B", x=15, y=3), Stone(color="W", x=3, y=15)],
        initial_player="W",
        rules="chinese",
        komi=7.5,
    )
    base.update(kw)
    return Position(**base)


def test_photo_position_uses_initial_stones_and_player():
    q = build_query("c1-1", photo_position(), AnalyzeParams(max_time=3))
    assert q["initialStones"] == [["B", "Q16"], ["W", "D4"]]
    assert q["initialPlayer"] == "W"
    assert q["moves"] == []  # no invented history
    assert q["rules"] == "chinese" and q["komi"] == 7.5
    assert q["boardXSize"] == q["boardYSize"] == 19
    assert q["overrideSettings"] == {"maxTime": 3}
    assert q["includeOwnership"] is True


def test_moves_after_photo_and_pass():
    pos = photo_position(moves=[Move(color="W", x=16, y=16), Move(color="B"), Move(color="W", x=2, y=2)])
    q = build_query("x", pos, AnalyzeParams())
    assert q["moves"] == [["W", "R3"], ["B", "pass"], ["W", "C17"]]
    assert pos.next_player() == "B"


def test_next_player_without_moves():
    assert photo_position(initial_player="B").next_player() == "B"
    assert photo_position(initial_player="W").next_player() == "W"


@pytest.mark.parametrize("komi", [7.3, 1000])
def test_bad_komi(komi):
    with pytest.raises(ValidationError):
        photo_position(komi=komi)


def test_ok_komi_values():
    for k in (0, 0.5, 6.5, 7, 7.5, -3.5):
        assert photo_position(komi=k).komi == k


def test_bad_size_and_points():
    with pytest.raises(ValidationError):
        Position(size=15)
    assert Position(size=11, initial_stones=[Stone(color="B", x=10, y=10)]).size == 11
    with pytest.raises(ValidationError):
        Position(size=11, initial_stones=[Stone(color="B", x=11, y=0)])
    with pytest.raises(ValidationError):
        Position(size=9, initial_stones=[Stone(color="B", x=9, y=0)])
    with pytest.raises(ValidationError):
        Position(size=9, initial_stones=[Stone(color="B", x=1, y=1), Stone(color="W", x=1, y=1)])
    with pytest.raises(ValidationError):
        Position(size=9, moves=[Move(color="B", x=1)])
    with pytest.raises(ValidationError):
        Position(size=9, rules="bogus")


def test_visits_cap():
    q = build_query("x", photo_position(), AnalyzeParams(max_visits=50))
    assert q["maxVisits"] == 50


RAW = {
    "id": "c-1",
    "isDuringSearch": False,
    "turnNumber": 0,
    "rootInfo": {"currentPlayer": "W", "visits": 120, "winrate": 0.62, "scoreLead": 3.5, "scoreStdev": 12.0},
    "moveInfos": [
        {
            "move": "D16",
            "order": 0,
            "visits": 80,
            "winrate": 0.6,
            "scoreLead": 3.1,
            "prior": 0.4,
            "pv": ["D16", "Q4", "pass"],
            "pvVisits": [80, 40, 3],
        },
        {
            "move": "Q4",
            "order": 1,
            "visits": 30,
            "winrate": 0.64,
            "scoreLead": 3.9,
            "prior": 0.3,
            "pv": ["Q4"],
        },
        {
            "move": "D4",
            "order": 2,
            "visits": 30,
            "winrate": 0.64,
            "scoreLead": 3.9,
            "prior": 0.3,
            "pv": ["D4"],
            "isSymmetryOf": "Q4",
        },
        {
            "move": "pass",
            "order": 3,
            "visits": 1,
            "winrate": 0.9,
            "scoreLead": 9,
            "prior": 0.0,
            "pv": ["pass"],
        },
    ],
    "ownership": [0.5] * 361,
}


def test_normalize_labels_black_perspective_and_pv_colors():
    r = normalize_response(RAW, 19)
    assert r["perspective"] == "black"
    assert r["currentPlayer"] == "W"
    assert r["blackWinrate"] == pytest.approx(0.62)
    assert r["blackScoreLead"] == pytest.approx(3.5)
    moves = [c["gtp"] for c in r["candidates"]]
    assert moves == ["D16", "Q4", "pass"]  # symmetric duplicate dropped
    first = r["candidates"][0]
    assert (first["x"], first["y"]) == (3, 3)
    assert [s["color"] for s in first["pv"]] == ["W", "B", "W"]
    assert first["pv"][2]["pass"] is True
    assert first["pv"][1]["visits"] == 40
    assert r["candidates"][2]["pass"] is True
    assert len(r["blackOwnership"]) == 361


def test_normalize_top_n_and_bad_ownership():
    assert len(normalize_response(RAW, 19, top_n=1)["candidates"]) == 1
    bad = dict(RAW, ownership=[0.0] * 80)
    with pytest.raises(ValueError):
        normalize_response(bad, 19)


def test_normalize_terminated_without_results():
    r = normalize_response({"id": "x", "isDuringSearch": False, "noResults": True, "turnNumber": 0}, 9)
    assert r["noResults"] and r["candidates"] == []


def test_keep_color_query_inserts_pass_and_restricts_opponent():
    pos = photo_position(initial_player="B", moves=[Move(color="B", x=3, y=3)], to_play="B")
    q = build_query("x", pos, AnalyzeParams(same_color_moves=3, pv_len=2))
    assert q["moves"] == [["B", "D16"], ["W", "pass"]]
    assert q["allowMoves"] == [{"player": "W", "moves": ["pass"], "untilDepth": 4}]
    assert q["analysisPVLen"] == 5
    # Already Black's turn: nothing inserted; one move per line means no restriction.
    q = build_query("x", photo_position(initial_player="B", to_play="B"), AnalyzeParams())
    assert q["moves"] == [] and "allowMoves" not in q


def test_normalize_keep_color_drops_forced_passes():
    raw = {
        "id": "x",
        "isDuringSearch": False,
        "rootInfo": {"currentPlayer": "B", "visits": 10, "winrate": 0.6, "scoreLead": 2.0},
        "moveInfos": [
            {
                "move": "D4",
                "order": 0,
                "visits": 9,
                "winrate": 0.6,
                "scoreLead": 2.0,
                "pv": ["D4", "pass", "Q16", "pass", "Q4", "D16"],
            },
            {
                "move": "C3",
                "order": 1,
                "visits": 1,
                "winrate": 0.5,
                "scoreLead": 1.0,
                "pv": ["C3", "Q16", "Q4"],
            },  # not a same-colour line: cut at the first real reply
        ],
    }
    r = normalize_response(raw, 19, same_color_moves=3)
    assert [s["gtp"] for s in r["candidates"][0]["pv"]] == ["D4", "Q16", "Q4"]
    assert {s["color"] for s in r["candidates"][0]["pv"]} == {"B"}
    assert [s["gtp"] for s in r["candidates"][1]["pv"]] == ["C3"]

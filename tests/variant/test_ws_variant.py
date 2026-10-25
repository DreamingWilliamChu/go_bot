"""Analysis of bonus positions over the websocket: sources, supersession, staleness, labels."""

from fastapi.testclient import TestClient

from gobot.app import create_app
from tests.conftest import needs_katago


def client(settings) -> TestClient:
    return TestClient(
        create_app(settings, start_engine=False), client=("127.0.0.1", 50000), base_url="http://localhost"
    )


def new_game(c: TestClient, **cfg) -> dict:
    return c.post("/api/variant/games", json={"config": {"trigger": "random", "size": 9, **cfg}}).json()


def collect(ws, rid: str) -> list[dict]:
    out = []
    while True:
        m = ws.receive_json()
        if m.get("requestId") != rid:
            continue
        out.append(m)
        if m["type"] in ("final", "error"):
            return out


def test_bonus_search_with_the_heuristic_evaluator(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = new_game(c, black_bonuses=2, white_bonuses=0, horizon=2)  # Black: certain 3-move bonus
        with c.websocket_connect("ws://localhost/api/analyze") as ws:
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "a1",
                    "source": "variant",
                    "evaluator": "heuristic",
                    "variant": {"gameId": v["gameId"], "revision": v["revision"]},
                    "params": {"max_time": 2.0, "max_visits": 300},
                }
            )
            msgs = collect(ws, "a1")
        final = msgs[-1]
        assert final["type"] == "final" and final["source"] == "variant"
        assert final["evaluator"]["kind"] == "heuristic" and final["evaluator"]["approximate"] is True
        assert final["budget"]["visits"] >= 300 and final["budget"]["maxVisits"] == 300
        assert final["rules"]["movesPerBonus"] == 3 and final["currentPlayer"] == "B"
        top = final["candidates"][0]
        # the line keeps Black for all three moves of the bonus turn, then White
        colors = [s["color"] for s in top["pv"][:4]]
        assert colors[:3] == ["B", "B", "B"]
        assert len(top["pv"]) < 4 or colors[3] == "W"


def test_stale_revision_and_unknown_evaluator_are_reported(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = new_game(c)
        with c.websocket_connect("ws://localhost/api/analyze") as ws:
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "s",
                    "variant": {"gameId": v["gameId"], "revision": 99},
                    "params": {},
                }
            )
            assert collect(ws, "s")[-1]["kind"] == "stale"
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "u",
                    "source": "variant",
                    "evaluator": "net:nope",
                    "variant": {"gameId": v["gameId"], "revision": v["revision"]},
                    "params": {},
                }
            )
            last = collect(ws, "u")[-1]
            assert last["type"] == "error" and "not installed" in last["message"]


def test_a_new_request_supersedes_the_running_search(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = new_game(c, black_bonuses=1, white_bonuses=1)
        target = {"gameId": v["gameId"], "revision": v["revision"]}
        with c.websocket_connect("ws://localhost/api/analyze") as ws:
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "old",
                    "source": "variant",
                    "evaluator": "heuristic",
                    "variant": target,
                    "params": {"max_time": 30},
                }
            )
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "new",
                    "source": "variant",
                    "evaluator": "heuristic",
                    "variant": target,
                    "params": {"max_time": 1.0},
                }
            )
            msgs = collect(ws, "new")
            assert msgs[-1]["type"] == "final"


def test_evaluators_endpoint_lists_sources(tmp_settings) -> None:
    with client(tmp_settings) as c:
        d = c.get("/api/variant/evaluators").json()
        assert {s["id"] for s in d["sources"]} == {"katago", "katago-net", "heuristic"}
        assert d["models"] == []


@needs_katago("tf2-b10c384")
def test_standard_katago_and_katago_evaluator_on_a_bonus_position(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = new_game(c, black_bonuses=2, white_bonuses=0, horizon=2)
        target = {"gameId": v["gameId"], "revision": v["revision"]}
        with c.websocket_connect("ws://localhost/api/analyze") as ws:
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "k",
                    "source": "katago",
                    "variant": target,
                    "params": {"max_time": 10, "max_visits": 300},
                }
            )
            final = collect(ws, "k")[-1]
            assert final["type"] == "final" and final["source"] == "katago"
            assert "ignores future bonuses" in final["evaluator"]["label"]
            pv = final["candidates"][0]["pv"]
            assert pv and {s["color"] for s in pv} == {"B"} and len(pv) <= 3  # this turn's three moves
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "n",
                    "source": "variant",
                    "evaluator": "katago-net",
                    "variant": target,
                    "params": {"max_time": 60, "max_visits": 64},
                }
            )
            final = collect(ws, "n")[-1]
            assert final["type"] == "final", final
            assert final["evaluator"]["kind"] == "katago-net" and final["evaluator"]["approximate"]
            assert final["budget"]["visits"] >= 64


def test_choice_games_offer_using_a_bonus_and_say_so(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = c.post(
            "/api/variant/games", json={"config": {"size": 9, "black_bonuses": 1, "white_bonuses": 1}}
        ).json()
        assert v["state"]["config"]["trigger"] == "choice"
        with c.websocket_connect("ws://localhost/api/analyze") as ws:
            ws.send_json(
                {
                    "type": "analyze",
                    "requestId": "c1",
                    "source": "variant",
                    "evaluator": "heuristic",
                    "variant": {"gameId": v["gameId"], "revision": v["revision"]},
                    "params": {"max_time": 3.0, "max_visits": 400},
                }
            )
            final = collect(ws, "c1")[-1]
        assert final["type"] == "final"
        assert "choose when to use their bonuses" in final["evaluator"]["note"]
        bonus = [x for x in final["candidates"] if x["bonus"]]
        assert bonus and bonus[0]["gtp"] == "bonus" and bonus[0]["x"] is None and not bonus[0]["pass"]
        assert bonus[0]["pv"][0]["bonus"] is True and all(s["color"] == "B" for s in bonus[0]["pv"][1:4])


def test_trained_net_is_labelled_approximate_in_choice_games() -> None:
    from gobot.variant.config import VariantConfig
    from gobot.variant.service import label_for_rules
    from gobot.variant.state import initial_state

    trained = {"kind": "variant-net", "label": "net", "modelVersion": "m", "approximate": False, "note": ""}
    random_state = initial_state(VariantConfig(size=9, trigger="random"))
    choice_state = initial_state(VariantConfig(size=9, trigger="choice"))
    assert label_for_rules(trained, random_state)["approximate"] is False
    out = label_for_rules(trained, choice_state)
    assert out["approximate"] is True and "random bonuses" in out["note"]

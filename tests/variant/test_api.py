import json

from fastapi.testclient import TestClient

from gobot.app import create_app


def client(settings) -> TestClient:
    return TestClient(
        create_app(settings, start_engine=False), client=("127.0.0.1", 50000), base_url="http://localhost"
    )


def act(c: TestClient, view: dict, **action) -> dict:
    r = c.post(
        f"/api/variant/games/{view['gameId']}/actions",
        json={"expectedRevision": view["revision"], "action": action},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_create_play_bonus_turn_and_switch(tmp_settings) -> None:
    with client(tmp_settings) as c:
        # allocation = H makes every Black turn a bonus turn: deterministic without a seed API
        v = c.post(
            "/api/variant/games",
            json={"config": {"size": 9, "black_bonuses": 2, "white_bonuses": 0, "horizon": 2}},
        ).json()
        s = v["state"]
        assert (
            s["toMove"] == "B"
            and s["bonusActive"]
            and s["movesLeft"] == 3
            and s["remaining"] == {"B": 1, "W": 0}
        )
        assert v["newEvents"][0] == {
            "type": "turn_start",
            "player": "B",
            "turnIndex": 1,
            "bonus": True,
            "moves": 3,
            "probability": 1.0,
        }
        for x in (0, 1, 2):
            v = act(c, v, type="place", x=x, y=0)
        s = v["state"]
        assert s["toMove"] == "W" and not s["bonusActive"] and s["movesLeft"] == 1
        assert v["state"]["nextTurnBonusProbability"] == {"B": 1.0, "W": 0.0}
        v = act(c, v, type="place", x=8, y=8)
        assert v["state"]["toMove"] == "B" and v["state"]["bonusActive"]
        assert v["newEvents"][-1]["type"] == "turn_start" and v["newEvents"][-1]["bonus"]


def test_illegal_moves_stale_revisions_and_undo(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = c.post(
            "/api/variant/games", json={"config": {"size": 9, "black_bonuses": 0, "white_bonuses": 0}}
        ).json()
        v = act(c, v, type="place", x=4, y=4)
        r = c.post(
            f"/api/variant/games/{v['gameId']}/actions",
            json={"expectedRevision": v["revision"], "action": {"type": "place", "x": 4, "y": 4}},
        )
        assert r.status_code == 400 and r.json()["detail"]["reason"] == "occupied"
        r = c.post(
            f"/api/variant/games/{v['gameId']}/actions",
            json={"expectedRevision": 0, "action": {"type": "pass"}},
        )
        assert r.status_code == 409 and r.json()["detail"]["game"]["revision"] == v["revision"]
        v = act(c, v, type="undo")
        assert v["state"]["placements"] == 0 and v["canRedo"]
        v = act(c, v, type="redo")
        assert v["state"]["placements"] == 1
        # a colour is never accepted: the server decides who moves
        r = c.post(
            f"/api/variant/games/{v['gameId']}/actions",
            json={
                "expectedRevision": v["revision"],
                "action": {"type": "place", "x": 0, "y": 0, "color": "W"},
            },
        )
        assert r.status_code == 200 and r.json()["state"]["grid"][0] == 2  # White was to move anyway


def test_games_survive_a_restart_and_never_leak_the_schedule(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = c.post(
            "/api/variant/games", json={"config": {"size": 11, "black_bonuses": 3, "white_bonuses": 3}}
        ).json()
        v = act(c, v, type="place", x=5, y=5)
        text = json.dumps(v) + c.get(f"/api/variant/games/{v['gameId']}/export").text
        for word in ("schedule", "seed", "salt"):
            assert word not in text
        path = tmp_settings.variant_dir / "games" / f"{v['gameId']}.json"
        assert path.stat().st_mode & 0o777 == 0o600
    with client(tmp_settings) as c:  # a new server process: loads from disk
        v2 = c.get(f"/api/variant/games/{v['gameId']}").json()
        assert v2["state"] == v["state"] and v2["revision"] == v["revision"]
        assert c.get("/api/variant/games/0123456789abcdef").status_code == 404
        assert c.get("/api/variant/games/..%2f..%2fx").status_code == 404


def test_allocations_are_fixed_per_game(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = c.post(
            "/api/variant/games", json={"config": {"size": 9, "black_bonuses": 1, "white_bonuses": 1}}
        ).json()
        other = c.post(
            "/api/variant/games", json={"config": {"size": 9, "black_bonuses": 5, "white_bonuses": 0}}
        ).json()
        assert c.get(f"/api/variant/games/{v['gameId']}").json()["state"]["config"]["black_bonuses"] == 1
        assert other["state"]["config"]["black_bonuses"] == 5
        assert (
            c.post("/api/variant/games", json={"config": {"size": 9, "black_bonuses": 21}}).status_code == 422
        )
        assert c.post("/api/variant/games", json={"config": {"size": 10}}).status_code == 422


def test_import_requires_a_feasible_explicit_bonus_state(tmp_settings) -> None:
    with client(tmp_settings) as c:
        body = {
            "config": {"size": 9, "black_bonuses": 2, "white_bonuses": 2},
            "stones": [{"color": "B", "x": 2, "y": 2}, {"color": "W", "x": 6, "y": 6}],
            "toMove": "W",
            "remaining": {"B": 1, "W": 2},
            "turnsStarted": {"B": 4, "W": 4},
            "bonusActive": True,
            "movesLeft": 2,
            "provenance": "photo",
        }
        v = c.post("/api/variant/games/import", json=body).json()
        s = v["state"]
        assert s["toMove"] == "W" and s["bonusActive"] and s["movesLeft"] == 2 and not s["historyKnown"]
        assert v["provenance"] == "photo" and v["newEvents"] == []
        bad = {**body, "remaining": {"B": 17, "W": 0}}  # more than the 16 eligible turns left
        r = c.post("/api/variant/games/import", json=bad)
        assert r.status_code == 422 and "eligible" in r.json()["detail"]
        dead = {
            **body,
            "stones": [
                {"color": "W", "x": 0, "y": 0},
                {"color": "B", "x": 1, "y": 0},
                {"color": "B", "x": 0, "y": 1},
            ],
        }
        assert c.post("/api/variant/games/import", json=dead).status_code == 422


def test_open_an_export_keeps_the_history(tmp_settings) -> None:
    with client(tmp_settings) as c:
        v = c.post(
            "/api/variant/games",
            json={"config": {"size": 9, "black_bonuses": 1, "white_bonuses": 2, "horizon": 3}},
        ).json()
        for x in range(6):
            v = act(c, v, type="place", x=x, y=x % 3)
        ex = c.get(f"/api/variant/games/{v['gameId']}/export").json()
        r = c.post("/api/variant/games/open", json=ex)
        assert r.status_code == 200, r.text
        w = r.json()
        assert w["gameId"] != v["gameId"] and w["provenance"] == "import"
        assert w["state"]["grid"] == v["state"]["grid"] and w["state"]["remaining"] == v["state"]["remaining"]
        assert w["events"] == v["events"]

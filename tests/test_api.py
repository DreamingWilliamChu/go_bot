from __future__ import annotations

from fastapi.testclient import TestClient

from gobot.app import create_app
from gobot.auth import COOKIE_NAME, cookie_value

from .conftest import needs_katago


def client_for(settings, token=None, host="127.0.0.1"):
    app = create_app(settings, token=token, start_engine=False)
    return TestClient(app, client=(host, 50000))


def test_status_and_frontend_fallback(tmp_settings, tmp_path):
    tmp_settings.frontend_dist = tmp_path / "nodist"
    with client_for(tmp_settings) as c:
        r = c.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert body["engine"]["state"] in ("stopped", "error")
        assert [p["id"] for p in body["presets"]] == ["quick", "standard", "deep"]
        assert "chinese" in body["rules"]
        assert "Frontend not built" in c.get("/").text
        assert c.get("/api/nope").status_code == 404


def test_frontend_served_with_spa_fallback(tmp_settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a.js").write_text("x")
    tmp_settings.frontend_dist = dist
    with client_for(tmp_settings) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/some/route").text == "<html>app</html>"
        r = c.get("/assets/a.js")
        assert r.text == "x" and "immutable" in r.headers["cache-control"]
        assert c.get("/../pyproject.toml").text == "<html>app</html>"


def test_lan_auth_requires_pairing(tmp_settings):
    tmp_settings.lan = True
    tmp_settings.require_token = True
    tok = "s3cret-token-abcdefgh"
    # phone on the LAN
    with client_for(tmp_settings, token=tok, host="192.168.1.50") as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/status").status_code == 401
        assert c.post("/api/pair", json={"token": "wrong"}).status_code == 403
        r = c.post("/api/pair", json={"token": tok})
        assert r.status_code == 200
        assert COOKIE_NAME in r.cookies
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/pair/info").status_code == 403  # pairing details only on the Mac
    with client_for(tmp_settings, token=tok, host="192.168.1.50") as c2:
        assert c2.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
        c2.cookies.set(COOKIE_NAME, cookie_value("other"))
        assert c2.get("/api/status").status_code == 401
    # the Mac itself
    with client_for(tmp_settings, token=tok, host="127.0.0.1") as c3:
        assert c3.get("/api/status").status_code == 200


def test_ws_rejects_unpaired_lan_client(tmp_settings):
    tmp_settings.require_token = True
    with client_for(tmp_settings, token="abcdefghijklmnopq", host="10.0.0.7") as c:
        import pytest
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as ei, c.websocket_connect("/api/analyze") as ws:
            ws.receive_json()
        assert ei.value.code == 4401


def test_ws_invalid_position(tmp_settings):
    with client_for(tmp_settings) as c, c.websocket_connect("/api/analyze") as ws:
        ws.send_json({"type": "analyze", "requestId": "r1", "position": {"size": 15}})
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["kind"] == "invalid" and msg["requestId"] == "r1"


def test_ws_engine_unavailable(tmp_settings, tmp_path):
    tmp_settings.katago_bin = None
    tmp_settings.models_dir = tmp_path / "empty-models"
    tmp_settings.models_dir.mkdir()
    import shutil

    from gobot.settings import repo_root

    shutil.copy(repo_root() / "models" / "manifest.json", tmp_settings.models_dir / "manifest.json")
    import gobot.settings as st

    orig = st.Settings.find_katago
    st.Settings.find_katago = lambda self: None  # type: ignore[method-assign]
    try:
        with client_for(tmp_settings) as c, c.websocket_connect("/api/analyze") as ws:
            ws.send_json({"type": "analyze", "requestId": "r1", "position": {"size": 9}})
            msgs = [ws.receive_json() for _ in range(2)]
            err = [m for m in msgs if m["type"] == "error"][0]
            assert err["kind"] == "unavailable" and "brew install katago" in err["message"]
    finally:
        st.Settings.find_katago = orig  # type: ignore[method-assign]


@needs_katago("b18c384-conv")
def test_ws_real_analysis_roundtrip(tmp_settings):
    tmp_settings.model = "b18c384-conv"
    with client_for(tmp_settings) as c, c.websocket_connect("/api/analyze") as ws:
        ws.send_json(
            {
                "type": "analyze",
                "requestId": "r7",
                "position": {
                    "size": 9,
                    "initial_stones": [{"color": "B", "x": 4, "y": 4}],
                    "initial_player": "W",
                    "rules": "chinese",
                    "komi": 7.5,
                },
                "params": {"max_time": 5, "max_visits": 10},
            }
        )
        final = None
        for _ in range(200):
            m = ws.receive_json()
            assert m["type"] != "error", m
            if m["type"] == "final":
                final = m
                break
        assert final is not None
        assert final["requestId"] == "r7"
        assert final["currentPlayer"] == "W"
        assert final["engine"]["modelId"] == "b18c384-conv"
        assert final["engine"]["version"]
        assert final["candidates"]

from __future__ import annotations

import json
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from gobot.accounts import SESSION_COOKIE, AccountError, AccountStore, hash_password, verify_password
from gobot.app import create_app
from gobot.auth import COOKIE_NAME, TunnelMarker, cookie_value
from gobot.tunnel import Tunnel

PW = "correct horse battery"


def test_password_hash_roundtrip():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert verify_password(PW, h)
    assert not verify_password("wrong", h)
    assert not verify_password(PW, None) and not verify_password(PW, "garbage")
    assert hash_password(PW) != h  # salted


def test_store_sessions_and_password_change(tmp_path):
    path = tmp_path / "account.json"
    s = AccountStore(path)
    assert s.username == "admin" and not s.password_set
    sid = s.create_session()
    assert not s.valid_session(sid)  # no password yet: nobody can be signed in
    with pytest.raises(AccountError):
        s.set_credentials(None, "short")
    with pytest.raises(AccountError):
        s.set_credentials("two words", None)
    s.set_credentials("Go", PW)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    raw = path.read_text()
    assert PW not in raw
    assert s.check_login("go", PW) and not s.check_login("other", PW) and not s.check_login("Go", "x")
    a, b = s.create_session(), s.create_session()
    assert a not in raw and s.valid_session(a) and s.valid_session(b)
    assert a not in path.read_text()  # only hashes are stored
    # reload from disk
    s2 = AccountStore(path)
    assert s2.username == "Go" and s2.valid_session(a)
    # a new password ends every session except the one that changed it
    s2.set_credentials(None, PW + "!", keep_session=a)
    assert s2.valid_session(a) and not s2.valid_session(b)
    assert s2.end_all_sessions() == 1 and not s2.valid_session(a)
    # expired sessions are refused
    c = s2.create_session()
    data = json.loads(path.read_text())
    for v in data["sessions"].values():
        v["expires"] = 1
    path.write_text(json.dumps(data))
    assert not AccountStore(path).valid_session(c)


def test_limiter_locks_after_repeated_failures(tmp_path):
    lim = AccountStore(tmp_path / "a.json").limiter
    for _ in range(4):
        lim.failed("1.2.3.4", 100.0)
    assert lim.wait_seconds("1.2.3.4", 100.0) == 0
    lim.failed("1.2.3.4", 100.0)
    assert lim.wait_seconds("1.2.3.4", 100.0) == pytest.approx(30)
    assert lim.wait_seconds("5.6.7.8", 100.0) == 0  # other clients unaffected
    lim.succeeded("1.2.3.4")
    assert lim.wait_seconds("1.2.3.4", 100.0) == 0


def _client(settings, host="127.0.0.1", token=None, tunnel=False):
    app = create_app(settings, token=token, start_engine=False)
    asgi = TunnelMarker(app) if tunnel else app
    base = "https://gobot-test.trycloudflare.com" if tunnel else "http://localhost"
    return TestClient(asgi, client=(host, 50000), base_url=base)  # type: ignore[arg-type]


def test_tunnel_requires_sign_in_even_though_it_is_loopback(tmp_settings):
    AccountStore(tmp_settings.account_json).set_credentials("admin", PW)
    with _client(tmp_settings, tunnel=True) as c:
        info = c.get("/api/auth/info").json()
        assert info["required"] and not info["signedIn"] and info["tunnel"] and not info["mac"]
        assert info["user"] is None and not info["pairing"]
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/pair/info").status_code == 401
        assert c.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401
        r = c.post("/api/login", json={"username": "admin", "password": PW})
        assert r.status_code == 200
        set_cookie = r.headers["set-cookie"].lower()
        assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=strict" in set_cookie
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/auth/info").json()["signedIn"]
        # signed in remotely, but pairing secrets stay on the Mac
        assert c.get("/api/pair/info").status_code == 403
        assert c.post("/api/logout").status_code == 200
        assert c.get("/api/status").status_code == 401


def test_tunnel_refuses_pairing_token_and_spoofed_loopback(tmp_settings):
    tmp_settings.lan = tmp_settings.require_token = True
    tok = "s3cret-token-abcdefgh"
    with _client(tmp_settings, token=tok, tunnel=True) as c:
        c.cookies.set(COOKIE_NAME, cookie_value(tok))
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        assert c.get("/api/status", headers={"Cf-Connecting-Ip": "127.0.0.1"}).status_code == 401


def test_login_without_password_and_rate_limit(tmp_settings):
    with _client(tmp_settings, host="192.168.1.20") as c:
        assert c.post("/api/login", json={"username": "admin", "password": PW}).status_code == 409
    AccountStore(tmp_settings.account_json).set_credentials(None, PW)
    with _client(tmp_settings, tunnel=True) as c:
        h = {"Cf-Connecting-Ip": "203.0.113.9"}
        codes = [
            c.post("/api/login", json={"username": "admin", "password": "x"}, headers=h).status_code
            for _ in range(6)
        ]
        assert codes[:5] == [401] * 5 and codes[5] == 429
        # even the right password is refused while locked
        r = c.post("/api/login", json={"username": "admin", "password": PW}, headers=h)
        assert r.status_code == 429
        # another visitor is not locked out
        r = c.post(
            "/api/login",
            json={"username": "admin", "password": PW},
            headers={"Cf-Connecting-Ip": "198.51.100.4"},
        )
        assert r.status_code == 200


def test_account_change_rules(tmp_settings):
    # the Mac sets the first password without knowing an old one
    with _client(tmp_settings) as mac:
        assert mac.get("/api/auth/info").json() == {
            "required": False,
            "signedIn": False,
            "user": "admin",
            "mac": True,
            "passwordSet": False,
            "pairing": False,
            "tunnel": False,
        }
        assert mac.post("/api/account", json={"newPassword": "short"}).status_code == 400
        r = mac.post("/api/account", json={"username": "owner", "newPassword": PW})
        assert r.status_code == 200 and r.json() == {"username": "owner", "passwordSet": True}
    with _client(tmp_settings, tunnel=True) as c, _client(tmp_settings, tunnel=True) as other:
        c.post("/api/login", json={"username": "owner", "password": PW})
        other.post("/api/login", json={"username": "owner", "password": PW})
        assert other.get("/api/status").status_code == 200
        # remote changes need the current password
        assert c.post("/api/account", json={"newPassword": "new password 2"}).status_code == 403
        r = c.post("/api/account", json={"currentPassword": PW, "newPassword": "new password 2"})
        assert r.status_code == 200
        assert c.get("/api/status").status_code == 200  # this device stays signed in
        assert other.get("/api/status").status_code == 401  # every other device is signed out
        assert c.post("/api/account/logout-all").json() == {"ended": 0}
    with _client(tmp_settings, tunnel=True) as c:
        assert c.post("/api/login", json={"username": "owner", "password": PW}).status_code == 401
        assert (
            c.post("/api/login", json={"username": "owner", "password": "new password 2"}).status_code == 200
        )


def test_lan_phone_can_sign_in_instead_of_pairing(tmp_settings):
    tmp_settings.lan = tmp_settings.require_token = True
    AccountStore(tmp_settings.account_json).set_credentials(None, PW)
    with _client(tmp_settings, token="abcdefghijklmnopq", host="192.168.1.50") as c:
        info = c.get("/api/auth/info").json()
        assert info["required"] and info["pairing"] and not info["mac"]
        r = c.post("/api/login", json={"username": "admin", "password": PW})
        assert "secure" not in r.headers["set-cookie"].lower()  # plain http on the LAN
        assert SESSION_COOKIE in c.cookies
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/tunnel").json()["state"] == "off"


def test_tunnel_output_parsing(tmp_path):
    seen: list[str] = []
    t = Tunnel("http://127.0.0.1:8001", on_url=seen.append, binary="/bin/false")
    t.state = "starting"
    t.feed("INF Requesting new quick Tunnel on trycloudflare.com...\n")
    t.feed("ERR failed to reach https://api.trycloudflare.com/tunnel\n")
    assert t.url is None
    t.feed("INF |  https://ripe-lemon-tide-forest.trycloudflare.com                                 |\n")
    assert t.url == "https://ripe-lemon-tide-forest.trycloudflare.com" and t.state == "running"
    assert seen == [t.url]

    named = Tunnel("http://127.0.0.1:8001", token="tok", hostname="go.example.com", on_url=seen.append)
    assert named.mode == "named" and named.url == "https://go.example.com"
    named.state = "starting"
    named.feed("INF Registered tunnel connection connIndex=0 location=sjc01\n")
    assert named.state == "running" and seen[-1] == "https://go.example.com"


def test_tunnel_process_lifecycle(tmp_path):
    fake = tmp_path / "cloudflared"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        "print('INF +---------------------------+', flush=True)\n"
        "print('INF |  https://a-b-c.trycloudflare.com  |', flush=True)\n"
        "time.sleep(30)\n"
    )
    fake.chmod(0o755)
    t = Tunnel("http://127.0.0.1:8001", binary=str(fake))
    assert t.start()
    import time

    for _ in range(100):
        if t.url:
            break
        time.sleep(0.05)
    assert t.url == "https://a-b-c.trycloudflare.com" and t.status()["state"] == "running"
    t.stop()
    assert t.status()["state"] == "stopped"
    missing = Tunnel("http://127.0.0.1:8001", binary=None)
    missing.binary = None
    assert not missing.start() and "brew install cloudflared" in (missing.error or "")

from __future__ import annotations

import base64
import json
import os
import stat

import pytest

from gobot import cli
from gobot.settings import Settings
from gobot.tunnel import (
    load_tunnel_config,
    normalize_hostname,
    parse_tunnel_token,
    plan_tunnel,
    save_tunnel_config,
    save_tunnel_token,
)


def fake_token(tunnel_id: str = "6ff42ae2-765d-4adf-8112-31c55c1551ef") -> str:
    payload = {"a": "0123456789abcdef0123456789abcdef", "t": tunnel_id, "s": "c2VjcmV0LXNlY3JldC1zZWNyZXQ="}
    return base64.b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_parse_token_bare_and_from_install_command():
    tok = fake_token()
    assert parse_tunnel_token(tok) == (tok, "6ff42ae2-765d-4adf-8112-31c55c1551ef")
    cmd = f"sudo cloudflared service install {tok}\n"
    assert parse_tunnel_token(cmd)[0] == tok
    with pytest.raises(ValueError, match="No tunnel token"):
        parse_tunnel_token("hello")
    bogus = base64.b64encode(json.dumps({"x": 1, "pad": "y" * 60}).encode()).decode()
    with pytest.raises(ValueError, match="does not look like"):
        parse_tunnel_token(bogus)


def test_hostnames():
    assert normalize_hostname("https://Go.Example.com/") == "go.example.com"
    for bad in ("localhost", "go example.com", "-x.example.com", ""):
        with pytest.raises(ValueError):
            normalize_hostname(bad)


def test_plan(tmp_path):
    root, data = tmp_path / "repo", tmp_path / "data"
    # nothing configured: only --tunnel starts a (quick) tunnel
    assert not plan_tunnel(root, data).enabled
    p = plan_tunnel(root, data, flag=True)
    assert p.enabled and p.token is None and p.hostname is None
    # repo configured, this device has no token yet: stays off, but we know the shared hostname
    save_tunnel_config(root, "go.example.com")
    p = plan_tunnel(root, data)
    assert not p.enabled and p.shared_host == "go.example.com" and p.hostname is None
    # token added: starts by itself as a named tunnel; --no-tunnel wins
    tok = fake_token()
    path = save_tunnel_token(data, tok)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    p = plan_tunnel(root, data)
    assert p.enabled and p.token == tok and p.hostname == "go.example.com"
    assert not plan_tunnel(root, data, no_tunnel=True).enabled
    assert plan_tunnel(root, data, hostname="other.example.com").hostname == "other.example.com"
    # autostart off: only with --tunnel
    save_tunnel_config(root, "go.example.com", autostart=False)
    assert not plan_tunnel(root, data).enabled and plan_tunnel(root, data, flag=True).enabled
    assert load_tunnel_config(root) == {"hostname": "go.example.com", "autostart": False}


def test_cli_setup_and_token(tmp_path, monkeypatch, capsys):
    s = Settings()
    s.root, s.data_dir = tmp_path, tmp_path / "data"
    monkeypatch.setattr(cli, "Settings", lambda: s)
    tok = fake_token()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": f"cloudflared service install {tok}")
    assert cli.main(["tunnel", "setup", "--hostname", "https://go.example.com"]) == 0
    assert json.loads((tmp_path / "config" / "tunnel.json").read_text())["hostname"] == "go.example.com"
    assert (s.data_dir / "tunnel_token").read_text().strip() == tok
    out = capsys.readouterr().out
    assert tok not in out and "6ff42ae2" in out
    assert cli.main(["tunnel", "show"]) == 0
    assert "go.example.com" in capsys.readouterr().out
    assert cli.main(["tunnel", "forget-token"]) == 0
    assert not (s.data_dir / "tunnel_token").exists()


def test_named_tunnel_rejected_token_then_recovers():
    from gobot.tunnel import Tunnel

    t = Tunnel("http://127.0.0.1:8001", token=fake_token(), hostname="go.example.com")
    t.state = "starting"
    t.feed('ERR Register tunnel error from server side error="Failed to get tunnel" connIndex=0')
    assert t.state == "error" and "gobot tunnel token" in (t.error or "")
    t.feed("INF Registered tunnel connection connIndex=0 location=sjc01")
    assert t.state == "running" and t.error is None

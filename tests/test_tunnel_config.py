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


def test_named_tunnel_command_passes_local_url():
    from gobot.tunnel import Tunnel

    tok = fake_token()
    cmd, env = Tunnel("http://127.0.0.1:8001", token=tok, binary="/bin/cloudflared").command()
    assert cmd == ["/bin/cloudflared", "tunnel", "--no-autoupdate", "run", "--url", "http://127.0.0.1:8001"]
    assert env["TUNNEL_TOKEN"] == tok and tok not in cmd
    cmd, env = Tunnel("http://127.0.0.1:8001", binary="/bin/cloudflared").command()
    assert cmd[-2:] == ["--url", "http://127.0.0.1:8001"] and "run" not in cmd


def fake_cloudflared(tmp_path, create_out: str = "Created tunnel gobot", create_code: int = 0):
    """A stand-in cloudflared that logs its arguments and answers create/route/token."""
    log = tmp_path / "calls.log"
    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        'case "$2" in\n'
        f'  create) echo "{create_out}"; exit {create_code} ;;\n'
        '  route) echo "INF Added CNAME"; exit 0 ;;\n'
        f'  token) echo "{fake_token()}"; exit 0 ;;\n'
        "esac\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    return script, log


def test_cli_create_without_dashboard(tmp_path, monkeypatch, capsys):
    s = Settings()
    s.root, s.data_dir = tmp_path, tmp_path / "data"
    monkeypatch.setattr(cli, "Settings", lambda: s)
    script, log = fake_cloudflared(tmp_path)
    cert = tmp_path / "cert.pem"
    cert.write_text("x")
    monkeypatch.setenv("GOBOT_CLOUDFLARED_BIN", str(script))
    monkeypatch.setenv("TUNNEL_ORIGIN_CERT", str(cert))
    assert cli.main(["tunnel", "create", "--hostname", "go.example.com"]) == 0
    assert log.read_text().splitlines() == [
        "tunnel create gobot",
        "tunnel route dns gobot go.example.com",
        "tunnel token gobot",
    ]
    assert (s.data_dir / "tunnel_token").read_text().strip() == fake_token()
    assert json.loads((tmp_path / "config" / "tunnel.json").read_text())["hostname"] == "go.example.com"
    assert fake_token() not in capsys.readouterr().out
    # second run reuses the tunnel and takes the hostname from config/tunnel.json
    script, log = fake_cloudflared(tmp_path, create_out="tunnel with name already exists", create_code=1)
    assert cli.main(["tunnel", "create"]) == 0
    assert "existing tunnel" in capsys.readouterr().out


def test_cli_create_reports_cloudflared_errors(tmp_path, monkeypatch, capsys):
    s = Settings()
    s.root, s.data_dir = tmp_path, tmp_path / "data"
    monkeypatch.setattr(cli, "Settings", lambda: s)
    script, _ = fake_cloudflared(tmp_path, create_out="Unauthorized", create_code=1)
    cert = tmp_path / "cert.pem"
    cert.write_text("x")
    monkeypatch.setenv("GOBOT_CLOUDFLARED_BIN", str(script))
    monkeypatch.setenv("TUNNEL_ORIGIN_CERT", str(cert))
    assert cli.main(["tunnel", "create"]) == 2  # no hostname anywhere
    assert cli.main(["tunnel", "create", "--hostname", "go.example.com"]) == 1
    assert "could not create the tunnel: Unauthorized" in capsys.readouterr().err
    assert not (s.data_dir / "tunnel_token").exists()

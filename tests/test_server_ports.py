from __future__ import annotations

import socket

from gobot import cli
from gobot.server import port_in_use
from gobot.settings import Settings


def listener() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_in_use():
    s = listener()
    port = s.getsockname()[1]
    assert port_in_use("127.0.0.1", port)
    s.close()
    assert not port_in_use("127.0.0.1", port)


def test_busy_tunnel_port_stops_with_one_line(tmp_path, monkeypatch, capsys):
    s = Settings()
    s.root, s.data_dir = tmp_path, tmp_path / "data"
    monkeypatch.setattr(cli, "Settings", lambda: s)
    monkeypatch.setenv("GOBOT_CLOUDFLARED_BIN", str(tmp_path / "no-cloudflared"))
    busy = listener()
    port = busy.getsockname()[1]
    try:
        argv = [
            "serve",
            "--port",
            str(free_port()),
            "--tunnel",
            "--tunnel-port",
            str(port),
            "--no-engine-warmup",
        ]
        assert cli.main(argv) == 1
    finally:
        busy.close()
    out = capsys.readouterr()
    assert out.out == ""  # no banner, no tunnel started
    assert out.err.strip() == (
        f"Port {port} (Cloudflare tunnel) is already in use, probably by another copy of gobot: "
        "stop that one first (Ctrl+C in its terminal), or start with --port N."
    )

"""Remote access through a Cloudflare Tunnel (``cloudflared``), started and stopped with the server.

Two kinds of tunnel:

* **Quick tunnel** (no Cloudflare account): ``cloudflared tunnel --url http://127.0.0.1:<port>``.
  Cloudflare assigns a random ``https://<words>.trycloudflare.com`` address, new on every start.
* **Named tunnel** (your Cloudflare account and domain): create a tunnel in the Cloudflare dashboard
  (Zero Trust > Networks > Tunnels), add a public hostname pointing to ``http://127.0.0.1:<port>``,
  and give gobot its token (``GOBOT_TUNNEL_TOKEN`` or ``data/tunnel_token``). The address stays the same.
  ``gobot tunnel create`` does the same through ``cloudflared tunnel login`` without the dashboard.

For a named tunnel shared by every checkout of the repository, ``config/tunnel.json`` (committed,
not secret) names the public hostname. Each device stores the secret token once in its git-ignored
``data/tunnel_token`` (``gobot tunnel token``); ``start.sh`` then starts the tunnel by itself.

cloudflared connects to a loopback-only port of its own, served through ``auth.TunnelMarker``, so
everything that arrives through the tunnel needs a sign-in. The token is passed in the environment
(``TUNNEL_TOKEN``), not on the command line where other processes could read it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_CANDIDATES = ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared")


def find_cloudflared() -> str | None:
    env = os.environ.get("GOBOT_CLOUDFLARED_BIN")
    if env:
        return env
    found = shutil.which("cloudflared")
    if found:
        return found
    return next((c for c in CLOUDFLARED_CANDIDATES if Path(c).is_file()), None)


def tunnel_config_path(root: Path) -> Path:
    return root / "config" / "tunnel.json"


def load_tunnel_config(root: Path) -> dict[str, Any]:
    """The committed tunnel settings: ``{"hostname": "go.example.com", "autostart": true}``."""
    try:
        data = json.loads(tunnel_config_path(root).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_tunnel_config(root: Path, hostname: str, autostart: bool = True) -> Path:
    host = normalize_hostname(hostname)
    p = tunnel_config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hostname": host, "autostart": autostart}, indent=2) + "\n")
    return p


HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_hostname(value: str) -> str:
    host = value.strip().lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
    if not HOSTNAME_RE.match(host):
        raise ValueError(f"{value!r} is not a host name like go.example.com")
    return host


TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9_=+/-]{40,}")


def parse_tunnel_token(raw: str) -> tuple[str, str]:
    """Return (token, tunnel id). Accepts the bare token or the whole ``cloudflared ... install``
    command the dashboard shows."""
    m = TOKEN_RE.search(raw.strip())
    if not m:
        raise ValueError("No tunnel token found. It is the long text starting with 'eyJ' in the dashboard.")
    token = m.group(0)
    try:
        info = json.loads(base64.b64decode(token + "=" * (-len(token) % 4)))
        tunnel_id = str(info["t"])
        assert info.get("a") and info.get("s")
    except Exception as e:
        raise ValueError("This does not look like a Cloudflare tunnel token.") from e
    return token, tunnel_id


def save_tunnel_token(data_dir: Path, token: str) -> Path:
    p = data_dir / "tunnel_token"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.chmod(p, 0o600)
    return p


def load_tunnel_token(data_dir: Path) -> str | None:
    tok = os.environ.get("GOBOT_TUNNEL_TOKEN", "").strip()
    if tok:
        return tok
    p = data_dir / "tunnel_token"
    if p.is_file():
        return p.read_text().strip() or None
    return None


class TunnelSetupError(RuntimeError):
    pass


def origin_cert_present() -> bool:
    """Whether ``cloudflared tunnel login`` has been done on this device."""
    env = os.environ.get("TUNNEL_ORIGIN_CERT")
    return Path(env).is_file() if env else (Path.home() / ".cloudflared" / "cert.pem").is_file()


def create_named_tunnel(binary: str, name: str, hostname: str, log: Callable[[str], None] = print) -> str:
    """Create (or reuse) a named tunnel with cloudflared's own login instead of the Zero Trust
    dashboard, point ``hostname`` at it and return its token. Needs ``cloudflared tunnel login``."""

    def run(*args: str) -> tuple[int, str]:
        p = subprocess.run(
            [binary, "tunnel", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        return p.returncode, (p.stdout + p.stderr).strip()

    code, out = run("create", name)
    if code == 0:
        log(f"Created tunnel '{name}'.")
    elif "already exists" in out:
        log(f"Using the existing tunnel '{name}'.")
    else:
        raise TunnelSetupError(f"cloudflared could not create the tunnel: {out}")
    code, out = run("route", "dns", name, hostname)
    if code != 0:
        raise TunnelSetupError(f"cloudflared could not point {hostname} at the tunnel: {out}")
    log(f"{hostname} now points to tunnel '{name}'.")
    code, out = run("token", name)
    if code != 0:
        raise TunnelSetupError(f"cloudflared could not fetch the tunnel token: {out}")
    return parse_tunnel_token(out)[0]


@dataclass
class TunnelPlan:
    enabled: bool
    token: str | None  # named tunnel when set, quick tunnel otherwise
    hostname: str | None  # public hostname of the named tunnel
    shared_host: str | None  # hostname from config/tunnel.json (autostart), even without a token


def plan_tunnel(
    root: Path, data_dir: Path, flag: bool = False, no_tunnel: bool = False, hostname: str | None = None
) -> TunnelPlan:
    """Decide whether and how to start the tunnel.

    ``--tunnel`` always starts one (named with a token, quick without). Without the flag it starts
    only when config/tunnel.json has autostart and this device has the token; ``--no-tunnel`` wins.
    """
    cfg = load_tunnel_config(root)
    shared = cfg.get("hostname") if cfg.get("autostart", True) else None
    token = load_tunnel_token(data_dir)
    enabled = not no_tunnel and (flag or bool(shared and token))
    host = (hostname or cfg.get("hostname")) if token else None
    return TunnelPlan(enabled=enabled, token=token, hostname=host, shared_host=shared)


class Tunnel:
    def __init__(
        self,
        local_url: str,
        token: str | None = None,
        hostname: str | None = None,
        binary: str | None = None,
        on_url: Callable[[str], None] | None = None,
    ):
        self.local_url = local_url
        self.token = token
        self.mode = "named" if token else "quick"
        self.binary = binary or find_cloudflared()
        self.on_url = on_url
        self.url: str | None = (
            f"https://{hostname.removeprefix('https://').rstrip('/')}" if hostname else None
        )
        self.state = "stopped"
        self.error: str | None = None
        self.log_tail: list[str] = []
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "state": self.state, "url": self.url, "error": self.error}

    def command(self) -> tuple[list[str], dict[str, str]]:
        """The cloudflared command line and environment. A named tunnel also gets ``--url``: a tunnel
        made with ``gobot tunnel create`` has no routes stored at Cloudflare and uses it, while one made
        in the dashboard replaces it with the routes from the dashboard."""
        assert self.binary
        cmd = [self.binary, "tunnel", "--no-autoupdate"]
        env = dict(os.environ)
        if self.token:
            cmd += ["run", "--url", self.local_url]
            env["TUNNEL_TOKEN"] = self.token
        else:
            cmd += ["--url", self.local_url]
        return cmd, env

    def start(self) -> bool:
        if not self.binary:
            self.state, self.error = "error", "cloudflared is not installed. Run: brew install cloudflared"
            return False
        cmd, env = self.command()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        except OSError as e:
            self.state, self.error = "error", f"could not start cloudflared: {e}"
            return False
        self.state = "starting"
        self._reader = threading.Thread(target=self._read, name="cloudflared-log", daemon=True)
        self._reader.start()
        return True

    def _read(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            self.feed(line)
        code = proc.wait()
        if self.state != "stopped":
            self.state = "error"
            self.error = f"cloudflared exited (code {code}). Last output: " + " | ".join(self.log_tail[-3:])

    def feed(self, line: str) -> None:
        """Handle one line of cloudflared output (public for tests)."""
        line = line.rstrip()
        if not line:
            return
        self.log_tail = [*self.log_tail[-19:], line]
        if self.mode == "quick" and self.url is None:
            m = QUICK_URL_RE.search(line)
            if m and "api.trycloudflare.com" not in m.group(0):
                self.url = m.group(0)
                self.state = "running"
                if self.on_url:
                    self.on_url(self.url)
        elif self.mode == "named" and ("Register tunnel error" in line or "Unauthorized" in line):
            self.state = "error"
            self.error = (
                "Cloudflare rejected this device's tunnel token (the tunnel may have been deleted or the token "
                "mistyped). Copy it again from the tunnel's page and run: uv run gobot tunnel token"
            )
        elif self.state in ("starting", "error") and "Registered tunnel connection" in line:
            self.state, self.error = "running", None
            if self.mode == "named" and self.url and self.on_url:
                self.on_url(self.url)

    def stop(self) -> None:
        self.state = "stopped"
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

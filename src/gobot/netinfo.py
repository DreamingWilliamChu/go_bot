"""Local-network address discovery (never contacts the internet)."""

from __future__ import annotations

import ipaddress
import socket

import psutil

_PRIVATE = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")]
_SKIP_PREFIXES = ("lo", "docker", "br-", "veth", "utun", "awdl", "llw", "bridge", "vmnet", "tailscale", "zt")


def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _PRIVATE)


def candidate_ips() -> list[tuple[str, str]]:
    """(interface, IPv4) pairs on private networks, Wi-Fi/Ethernet first."""
    out: list[tuple[str, str]] = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if name.startswith(_SKIP_PREFIXES):
            continue
        st = stats.get(name)
        if st is not None and not st.isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and _is_private(a.address):
                out.append((name, a.address))
    # macOS Wi-Fi is usually en0; prefer en*/eth*/wl* names, then 192.168.x
    out.sort(key=lambda t: (not t[0].startswith(("en", "eth", "wl")), not t[1].startswith("192.168."), t[0]))
    return out


def lan_ip() -> str | None:
    # Route lookup without sending packets: picks the interface used for the default route.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 9))
            ip = s.getsockname()[0]
            if _is_private(ip):
                return ip
    except OSError:
        pass
    c = candidate_ips()
    return c[0][1] if c else None


def local_hostname() -> str:
    h = socket.gethostname()
    return h if h.endswith(".local") else h.split(".")[0] + ".local"

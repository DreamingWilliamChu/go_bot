"""Model manifest, discovery and verified downloads."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Preference order used when nothing was selected by the benchmark yet.
DEFAULT_PREFERENCE = ("tf3-b10c512", "tf2-b10c384", "b18c384-conv", "tf3-b11c768", "b28c512-conv")
FALLBACK_CHAIN = ("tf2-b10c384", "b18c384-conv")


@dataclass
class NetInfo:
    id: str
    file: str
    url: str
    size: int
    sha256: str
    label: str = ""
    role: str = ""
    architecture: str = ""
    kata1_elo: float | None = None
    license: str = ""
    default_download: bool = False
    homebrew_bundled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> NetInfo:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "extra"}
        extra = {k: v for k, v in d.items() if k not in cls.__dataclass_fields__}
        return cls(**known, extra=extra)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "file": self.file,
            "architecture": self.architecture,
            "kata1Elo": self.kata1_elo,
            "size": self.size,
            "license": self.license,
        }


@dataclass
class Manifest:
    raw: dict[str, Any]
    networks: list[NetInfo]
    recognition: list[NetInfo]

    def net(self, net_id: str) -> NetInfo | None:
        return next((n for n in self.networks if n.id == net_id), None)

    def recog(self, rec_id: str) -> NetInfo | None:
        return next((n for n in self.recognition if n.id == rec_id), None)

    def net_by_file(self, filename: str) -> NetInfo | None:
        return next((n for n in self.networks if n.file == filename), None)


def load_manifest(path: Path) -> Manifest:
    raw = json.loads(path.read_text())
    return Manifest(
        raw=raw,
        networks=[NetInfo.from_json(n) for n in raw.get("networks", [])],
        recognition=[NetInfo.from_json(n) for n in raw.get("recognition", [])],
    )


def find_file(filename: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        p = d / filename
        if p.is_file():
            return p
    return None


def installed_networks(manifest: Manifest, dirs: list[Path]) -> list[tuple[NetInfo, Path]]:
    out = []
    for n in manifest.networks:
        p = find_file(n.file, dirs)
        if p is not None:
            out.append((n, p))
    return out


def resolve_model(
    spec: str | None, manifest: Manifest, dirs: list[Path]
) -> tuple[NetInfo | None, Path | None]:
    """Resolve a model id or path. With ``spec=None`` use the default preference order."""
    if spec:
        net = manifest.net(spec)
        if net is not None:
            return net, find_file(net.file, dirs)
        p = Path(spec).expanduser()
        if p.is_file():
            return manifest.net_by_file(p.name), p
        return None, None
    installed = {n.id: (n, p) for n, p in installed_networks(manifest, dirs)}
    for nid in DEFAULT_PREFERENCE:
        if nid in installed:
            return installed[nid]
    return None, None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()


def download(
    info: NetInfo,
    dest_dir: Path,
    progress: Callable[[int, int], None] | None = None,
    verify_existing: bool = True,
) -> Path:
    """Download ``info`` into ``dest_dir`` and verify size and sha256. Idempotent."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / info.file
    if dest.is_file():
        if not verify_existing or (dest.stat().st_size == info.size and sha256_file(dest) == info.sha256):
            return dest
        dest.unlink()
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(info.url, headers={"User-Agent": "gobot-model-downloader"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as resp, part.open("wb") as f:
        total = int(resp.headers.get("Content-Length") or info.size)
        done = 0
        while True:
            b = resp.read(1 << 20)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress:
                progress(done, total)
    size = part.stat().st_size
    digest = sha256_file(part)
    if size != info.size or digest != info.sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"{info.file}: verification failed (size {size} vs {info.size}, sha256 {digest} vs {info.sha256})"
        )
    part.replace(dest)
    return dest


def print_progress(name: str) -> Callable[[int, int], None]:
    last = [-1]

    def cb(done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        if pct != last[0] and (pct % 5 == 0 or done == total):
            last[0] = pct
            width = shutil.get_terminal_size((80, 20)).columns
            msg = f"  {name}: {pct:3d}% ({done / 1e6:.1f}/{total / 1e6:.1f} MB)"
            sys.stdout.write("\r" + msg[: width - 1])
            sys.stdout.flush()
            if done >= total:
                sys.stdout.write("\n")

    return cb

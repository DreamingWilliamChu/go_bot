"""Paths and runtime settings.

Everything the app writes lives under ``data/`` (git-ignored); downloaded weights live under
``models/`` (git-ignored except the manifest). Environment variables override defaults:

* ``GOBOT_DATA_DIR``, ``GOBOT_MODELS_DIR`` - directories
* ``GOBOT_KATAGO_BIN`` - KataGo executable
* ``GOBOT_MODEL`` - model id from models/manifest.json or a path to a ``.bin.gz``
* ``GOBOT_FRONTEND_DIST`` - built frontend directory
* ``GOBOT_VARIANT_DIR`` - bonus-turn Go games, variant networks and training runs (default data/variant)
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    # src/gobot/settings.py -> repo root
    return Path(__file__).resolve().parents[2]


HOMEBREW_KATAGO_CANDIDATES = (
    "/opt/homebrew/bin/katago",  # Apple Silicon Homebrew
    "/usr/local/bin/katago",  # Intel Homebrew
    "/home/linuxbrew/.linuxbrew/bin/katago",
)
HOMEBREW_SHARE_DIRS = (
    "/opt/homebrew/share/katago",
    "/usr/local/share/katago",
    "/home/linuxbrew/.linuxbrew/share/katago",
)


@dataclass
class Settings:
    root: Path = field(default_factory=repo_root)
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("GOBOT_DATA_DIR", repo_root() / "data"))
    )
    models_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("GOBOT_MODELS_DIR", repo_root() / "models"))
    )
    frontend_dist: Path = field(
        default_factory=lambda: Path(os.environ.get("GOBOT_FRONTEND_DIST", repo_root() / "frontend" / "dist"))
    )
    katago_bin: str | None = field(default_factory=lambda: os.environ.get("GOBOT_KATAGO_BIN"))
    model: str | None = field(default_factory=lambda: os.environ.get("GOBOT_MODEL"))
    host: str = "127.0.0.1"
    port: int = 8000
    lan: bool = False
    https: bool = False
    require_token: bool = False
    tunnel: bool = False
    tunnel_port: int = 0  # loopback port cloudflared connects to (0: port + 1)

    @property
    def manifest_path(self) -> Path:
        return self.models_dir / "manifest.json"

    @property
    def engine_json(self) -> Path:
        return self.data_dir / "engine.json"

    @property
    def hardware_json(self) -> Path:
        return self.data_dir / "hardware.json"

    @property
    def katago_dir(self) -> Path:
        return self.data_dir / "katago"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def account_json(self) -> Path:
        return self.data_dir / "account.json"

    @property
    def certs_dir(self) -> Path:
        return self.data_dir / "certs"

    @property
    def user_testset_dir(self) -> Path:
        return self.data_dir / "user_testset"

    @property
    def variant_dir(self) -> Path:
        """Bonus-turn Go games, variant networks and training runs (``GOBOT_VARIANT_DIR``)."""
        env = os.environ.get("GOBOT_VARIANT_DIR")
        return Path(env) if env else self.data_dir / "variant"

    def extra_model_dirs(self) -> list[Path]:
        return [Path(p) for p in HOMEBREW_SHARE_DIRS if Path(p).is_dir()]

    def find_katago(self) -> str | None:
        if self.katago_bin:
            return self.katago_bin
        found = shutil.which("katago")
        if found:
            return found
        for cand in HOMEBREW_KATAGO_CANDIDATES:
            if Path(cand).is_file():
                return cand
        local = self.data_dir / "katago-linux" / "katago"
        if local.is_file():
            return str(local)
        return None

    def load_engine_json(self) -> dict[str, Any]:
        try:
            return json.loads(self.engine_json.read_text())
        except FileNotFoundError:
            return {}

    def save_engine_json(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.engine_json.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.engine_json)

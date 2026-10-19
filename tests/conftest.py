from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from gobot.engine.models import load_manifest, resolve_model
from gobot.settings import Settings, repo_root

ROOT = repo_root()
FIXTURES = Path(__file__).parent / "fixtures"
FAKE = Path(__file__).parent / "fake_katago.py"


def real_katago() -> str | None:
    s = Settings()
    b = s.find_katago()
    if b and Path(b).exists():
        return b
    return None


def real_model(model_id: str) -> Path | None:
    s = Settings()
    m = load_manifest(s.manifest_path)
    _, p = resolve_model(model_id, m, [s.models_dir, *s.extra_model_dirs()])
    return p


@pytest.fixture
def coords_fixture() -> dict:
    return json.loads((FIXTURES / "coords.json").read_text())


@pytest.fixture
def fake_binary(tmp_path: Path) -> str:
    wrapper = tmp_path / "fake-katago"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    wrapper.chmod(0o755)
    return str(wrapper)


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.katago_bin = real_katago()
    s.data_dir = tmp_path / "data"
    s.data_dir.mkdir()
    (s.data_dir / "hardware.json").write_text(json.dumps({"memory_gb": 8, "logical_cpus": 4}))
    return s


def needs_katago(model_id: str = "b18c384-conv"):
    b = real_katago()
    p = real_model(model_id)
    reason = f"needs KataGo binary and model {model_id} (run scripts/fetch_ci_engine.sh or setup)"
    return pytest.mark.skipif(not (b and p) or os.environ.get("GOBOT_SKIP_KATAGO") == "1", reason=reason)

"""Trained variant networks installed for the app: ``<variant_dir>/models/<id>/{model.onnx, model.json}``.

``model.json`` records what the network is and where it came from (feature version, board sizes it
was trained *and evaluated* on, training provenance, evaluation summary). The app offers a network only
for the sizes listed there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


@dataclass
class VariantModel:
    id: str
    dir: Path
    meta: dict[str, Any]

    @property
    def onnx_path(self) -> Path:
        return self.dir / "model.onnx"

    @property
    def sizes(self) -> list[int]:
        return [int(s) for s in self.meta.get("sizes", [])]

    def public(self) -> dict[str, Any]:
        m = self.meta
        return {
            "id": self.id,
            "label": m.get("label", self.id),
            "sizes": self.sizes,
            "featureVersion": m.get("feature_version"),
            "architecture": m.get("architecture"),
            "provenance": m.get("provenance"),
            "evaluation": m.get("evaluation_summary"),
            "created": m.get("created"),
        }


def models_dir(variant_dir: Path) -> Path:
    return variant_dir / "models"


def list_models(variant_dir: Path) -> list[VariantModel]:
    root = models_dir(variant_dir)
    out: list[VariantModel] = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        meta_path = d / "model.json"
        if not (d.is_dir() and ID_RE.match(d.name) and meta_path.is_file() and (d / "model.onnx").is_file()):
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            continue
        out.append(VariantModel(d.name, d, meta))
    return out


def find_model(variant_dir: Path, model_id: str) -> VariantModel | None:
    if not ID_RE.match(model_id):
        return None
    for m in list_models(variant_dir):
        if m.id == model_id:
            return m
    return None

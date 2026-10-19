"""Wire settings, manifest, hardware and generated config into an EngineManager."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gobot import hardware
from gobot.engine.config import default_engine_settings, write_config
from gobot.engine.katago import EngineManager, EngineSpec, EngineUnavailable, probe_binary
from gobot.engine.models import FALLBACK_CHAIN, Manifest, installed_networks, load_manifest, resolve_model
from gobot.settings import Settings


def load_hardware(settings: Settings) -> dict[str, Any]:
    try:
        return json.loads(settings.hardware_json.read_text())
    except (FileNotFoundError, ValueError):
        info = hardware.detect()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.hardware_json.write_text(json.dumps(info, indent=2) + "\n")
        return info


class EngineService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.manifest: Manifest = load_manifest(settings.manifest_path)
        self.hardware = load_hardware(settings)
        self._probe: dict[str, Any] | None = None
        eng = settings.load_engine_json()
        fallbacks = tuple(eng.get("fallbacks", FALLBACK_CHAIN))
        self.manager = EngineManager(spec_factory=self.spec_for, fallback_chain=fallbacks)

    @property
    def model_dirs(self) -> list[Path]:
        return [self.settings.models_dir, *self.settings.extra_model_dirs()]

    def probe(self) -> dict[str, Any]:
        if self._probe is None:
            binary = self.settings.find_katago()
            if not binary:
                return {}
            try:
                self._probe = probe_binary(binary)
            except EngineUnavailable:
                return {}
        return self._probe

    def spec_for(self, model_id: str | None) -> EngineSpec:
        s = self.settings
        eng = s.load_engine_json()
        requested = model_id or s.model or eng.get("model_id")
        net, path = resolve_model(requested, self.manifest, self.model_dirs)
        binary = s.find_katago()
        backend = self.probe().get("backend_kind", "other")
        cfg = {**default_engine_settings(self.hardware, backend), **eng.get("katago", {})}
        if net is not None:
            cfg.update(eng.get("per_model", {}).get(net.id, {}))
        cfg_path = write_config(s.katago_dir / "analysis.cfg", cfg, backend)
        return EngineSpec(
            binary=binary,
            model_path=path,
            config_path=cfg_path,
            model_id=net.id if net else (requested or (path.name if path else None)),
            model_label=net.label if net else (path.name if path else None),
        )

    def models_status(self) -> list[dict[str, Any]]:
        installed = {n.id: p for n, p in installed_networks(self.manifest, self.model_dirs)}
        out = []
        for n in self.manifest.networks:
            d = n.public()
            d["installed"] = n.id in installed
            d["path"] = str(installed[n.id]) if n.id in installed else None
            d["current"] = n.id == self.manager.model_id
            out.append(d)
        return out

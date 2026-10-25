"""Replay buffer on disk: compressed npz chunks of training samples, with a bounded window.

Every chunk stores the *public* raw facts of each position (``features.Raw``: board, liberties, last
placements, superko and legal masks, raw counters such as remaining bonuses, komi), the policy target,
the outcome from Black's perspective (``value``: 1, 0.5, 0; NaN for truncated games or teacher rows),
the final area lead / N^2 (NaN when unknown), the actor, the curriculum stage, the generation and the
target kind (0 = variant self-play search, 1 = KataGo teacher policy on ordinary Go). Hidden
schedules are never stored here (``debug/`` files hold them for audits).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from gobot.variant.models.features import FEATURE_VERSION, Raw

SELFPLAY, TEACHER = 0, 1
FIELDS = ("board", "libs", "recent", "superko", "legal", "scalars", "komi")


@dataclass
class Batch:
    raw: Raw
    policy: np.ndarray  # float32[B, A+1]
    value: np.ndarray  # float32[B]: Black outcome, NaN = no target
    score: np.ndarray  # float32[B]: lead / A, NaN = no target
    kind: np.ndarray  # int8[B]

    def __len__(self) -> int:
        return len(self.raw)


def make_chunk(
    raws: list[Raw],
    policies: list[np.ndarray],
    values: list[float],
    scores: list[float],
    *,
    actors: list[int],
    kind: int,
    stage: int,
    generation: int,
    meta: dict[str, Any] | None = None,
) -> dict[str, np.ndarray]:
    """``meta``: experiment identifiers (run, model that played, preset, stage, rules) stored with the
    chunk; not a model input."""
    raw = Raw.concat(raws)
    n = len(raw)
    return {
        "meta": np.asarray(json.dumps(meta or {}, sort_keys=True)),
        **{f: getattr(raw, f) for f in FIELDS},
        "policy": np.stack(policies).astype(np.float16),
        "value": np.asarray(values, dtype=np.float32),
        "score": np.asarray(scores, dtype=np.float32),
        "actor": np.asarray(actors, dtype=np.int8),
        "kind": np.full(n, kind, dtype=np.int8),
        "stage": np.full(n, stage, dtype=np.int8),
        "generation": np.full(n, generation, dtype=np.int16),
        "size": np.asarray([raw.size], dtype=np.int16),
        "feature_version": np.asarray([FEATURE_VERSION], dtype=np.int16),
    }


class ReplayBuffer:
    def __init__(self, root: Path, max_samples: int, max_bytes: int):
        self.root = root
        self.max_samples = max_samples
        self.max_bytes = max_bytes
        root.mkdir(parents=True, exist_ok=True)

    def chunks(self) -> list[Path]:
        return sorted(self.root.glob("*.npz"))

    def add(self, chunk: dict[str, np.ndarray], name: str) -> Path:
        path = self.root / f"{name}.npz"
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **chunk)  # type: ignore[arg-type]
        tmp.replace(path)
        meta = {"samples": int(chunk["value"].shape[0]), "added": time.time()}
        path.with_suffix(".json").write_text(json.dumps(meta))
        self._trim()
        return path

    def _meta(self, p: Path) -> dict[str, Any]:
        try:
            return dict(json.loads(p.with_suffix(".json").read_text()))
        except (FileNotFoundError, ValueError):
            with np.load(p) as z:
                return {"samples": int(z["value"].shape[0])}

    def _trim(self) -> None:
        """Drop the oldest self-play chunks beyond the sample/byte window (teacher chunks are kept: they
        are small and protect basic shape knowledge from forgetting)."""
        chunks = self.chunks()
        total = 0
        size = 0
        for p in reversed(chunks):
            total += self._meta(p)["samples"]
            size += p.stat().st_size
            if (total > self.max_samples or size > self.max_bytes) and not p.name.startswith("teacher"):
                p.unlink(missing_ok=True)
                p.with_suffix(".json").unlink(missing_ok=True)

    def stats(self) -> dict[str, Any]:
        cs = self.chunks()
        return {
            "chunks": len(cs),
            "samples": sum(self._meta(p)["samples"] for p in cs),
            "bytes": sum(p.stat().st_size for p in cs),
        }

    def load(self, include_teacher: bool = True, only_teacher: bool = False) -> Batch:
        """Everything in the window as one in-memory batch."""
        parts: dict[str, list[np.ndarray]] = {}
        size = None
        for p in self.chunks():
            teacher = p.name.startswith("teacher")
            if (teacher and not include_teacher) or (only_teacher and not teacher):
                continue
            with np.load(p) as z:
                if int(z["feature_version"][0]) != FEATURE_VERSION:
                    raise ValueError(f"{p} has feature version {int(z['feature_version'][0])}")
                s = int(z["size"][0])
                if size is not None and s != size:
                    raise ValueError("replay mixes board sizes")
                size = s
                for k in (*FIELDS, "policy", "value", "score", "kind"):
                    parts.setdefault(k, []).append(z[k])
        if size is None:
            raise ValueError("replay buffer is empty")
        cat = {k: np.concatenate(v) for k, v in parts.items()}
        raw = Raw(size, *(cat[f] for f in FIELDS))
        return Batch(raw, cat["policy"].astype(np.float32), cat["value"], cat["score"], cat["kind"])


def take(batch: Batch, idx: np.ndarray) -> Batch:
    return Batch(batch.raw.take(idx), batch.policy[idx], batch.value[idx], batch.score[idx], batch.kind[idx])

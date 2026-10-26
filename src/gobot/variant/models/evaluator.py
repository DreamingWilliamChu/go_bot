"""The evaluator interface shared by every analysis source.

An evaluator turns observations into legal-action priors and a value. Values are always Black's win
probability in [0, 1] (draw 0.5), whoever is to move: consecutive moves may belong to the same player,
so nothing in the search negates values per move.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from gobot.variant.observation import Observation, search_key


@dataclass(frozen=True, slots=True)
class EvaluatorInfo:
    kind: str  # heuristic | katago-net | variant-net | uniform | ...
    label: str  # shown in the UI
    model_version: str | None
    approximate: bool  # True: not trained for this variant (its numbers are approximations)
    note: str = ""

    def public(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "modelVersion": self.model_version,
            "approximate": self.approximate,
            "note": self.note,
        }


@dataclass(slots=True)
class Evaluation:
    priors: np.ndarray  # float32[N*N+1], zero on illegal actions, sums to 1 over legal ones
    black_win: float  # in [0, 1]
    black_score: float | None = None  # expected final lead for Black, when the evaluator has one


class Evaluator(Protocol):
    info: EvaluatorInfo

    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]: ...

    def key(self, obs: Observation) -> bytes:
        """Cache key: everything the evaluation depends on."""
        ...


def legal_mask(obs: Observation) -> np.ndarray:
    st = obs.state
    m = np.zeros(st.config.area + 1, dtype=bool)
    m[st.legal_actions()] = True
    return m


def normalise(weights: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Weights restricted to legal actions and scaled to sum to 1 (uniform if they vanish)."""
    w = np.where(mask, np.maximum(weights, 0.0), 0.0).astype(np.float32)
    total = float(w.sum())
    if total <= 0 or not np.isfinite(total):
        w = mask.astype(np.float32)
        total = float(w.sum())
    return w / total


class UniformEvaluator:
    """Uniform priors over legal actions and an even value: a neutral baseline for tests."""

    info = EvaluatorInfo("uniform", "Uniform (no knowledge)", None, approximate=True)

    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]:
        out = []
        for obs in batch:
            m = legal_mask(obs)
            out.append(Evaluation(normalise(np.ones(m.shape, np.float32), m), 0.5))
        return out

    def key(self, obs: Observation) -> bytes:
        return search_key(obs.state)

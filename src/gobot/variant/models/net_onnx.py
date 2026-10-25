"""A trained variant network run with onnxruntime (CPU): what the app and self-play workers use.

The ONNX graph takes the encoded inputs of ``features.encode`` and returns raw policy logits, a Black
win logit and a score; masking, softmax and the sigmoid happen here, exactly as in training.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from gobot.variant.models.evaluator import Evaluation, EvaluatorInfo
from gobot.variant.models.features import FEATURE_VERSION, Raw, encode, raw_from_state, raw_key
from gobot.variant.observation import Observation

TRAINED_NOTE = (
    "A small network trained by bonus-turn self-play (after policy distillation from KataGo on ordinary "
    "Go). It sees the bonus counters but is much weaker at Go than KataGo."
)


def softmax_masked(logits: np.ndarray, legal: np.ndarray) -> np.ndarray:
    z = np.where(legal, logits, -np.inf)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return (e / e.sum(axis=1, keepdims=True)).astype(np.float32)


class OnnxEvaluator:
    def __init__(self, path: Path, threads: int = 1, blind: bool = False, meta: dict[str, Any] | None = None):
        import onnxruntime as ort

        if meta is None:
            meta_path = path.parent / "model.json"
            meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        fv = meta.get("feature_version", FEATURE_VERSION)
        if fv != FEATURE_VERSION:
            raise ValueError(f"{path} uses feature version {fv}; this app encodes version {FEATURE_VERSION}")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.meta = meta
        self.blind = blind
        model_id = str(meta.get("id", path.parent.name))
        self.info = EvaluatorInfo(
            "variant-net" if not blind else "variant-net-blind",
            str(meta.get("label", model_id)) + (" (bonus inputs hidden)" if blind else ""),
            model_id,
            approximate=False,
            note=TRAINED_NOTE,
        )
        self.score_scale = float(meta.get("score_scale", 1.0))

    def run(self, raw: Raw) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        sp, gl, legal = encode(raw, blind=self.blind)
        logits, value, score = self.session.run(None, {"spatial": sp, "global": gl})
        return logits, value, score, legal

    def evaluate(self, batch: Sequence[Observation]) -> list[Evaluation]:
        if not batch:
            return []
        raw = Raw.concat([raw_from_state(o.state) for o in batch])
        logits, value, score, legal = self.run(raw)
        priors = softmax_masked(logits, legal)
        win = 1.0 / (1.0 + np.exp(-value.reshape(-1)))
        area = raw.size * raw.size
        sc = score.reshape(-1) * area
        return [Evaluation(priors[i], float(win[i]), float(sc[i])) for i in range(len(batch))]

    def key(self, obs: Observation) -> bytes:
        return raw_key(raw_from_state(obs.state)) + (b"blind" if self.blind else b"")

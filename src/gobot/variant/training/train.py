"""Optimisation of the variant network (needs torch).

Loss per batch (weights configurable):

* policy: cross-entropy of the masked policy against the target distribution (search visit counts for
  self-play rows, KataGo's raw policy for teacher rows)
* value: binary cross-entropy of the Black-win logit against the *actual* game outcome from Black's
  perspective (1 / 0.5 / 0); rows without a real outcome (truncated games, teacher rows) are masked
* score: Huber loss on Black's final area lead / N^2, completed games only
* regularisation: AdamW weight decay; gradients clipped; a step with a non-finite gradient is skipped
  and counted

Each minibatch gets one random board symmetry (rotation/reflection) applied to inputs *and* policy
targets. Colours are never swapped.

Checkpoints hold the model, optimizer, step, configuration and all RNG states; ``resume`` continues
exactly. ``request_stop()`` (wired to SIGINT by the pipeline) makes ``train_steps`` stop after the
current step and save.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

from gobot.variant.models.features import FEATURE_VERSION, GLOBAL, SPATIAL, apply_symmetry, encode
from gobot.variant.training.net import NetConfig, VariantNet, count_parameters, masked_log_softmax
from gobot.variant.training.replay import Batch, take


@dataclass
class TrainConfig:
    batch_size: int = 256
    lr: float = 2e-3
    weight_decay: float = 1e-4
    grad_clip: float = 5.0
    policy_weight: float = 1.0
    value_weight: float = 1.0
    score_weight: float = 0.25
    symmetries: bool = True
    device: str = "auto"  # auto: MPS on Apple Silicon, else CUDA, else CPU


def pick_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class StopRequested(Exception):
    pass


class Trainer:
    def __init__(self, net_cfg: NetConfig, cfg: TrainConfig, seed: int = 0, device: str | None = None):
        self.net_cfg = net_cfg
        self.cfg = cfg
        self.device = pick_device(device or cfg.device)
        torch.manual_seed(seed)
        self.np_rng = np.random.default_rng(seed)
        self.py_rng = random.Random(seed)
        self.model = VariantNet(net_cfg).to(self.device)
        self.opt = torch.optim.AdamW(self.model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
        self.step = 0
        self.skipped = 0
        self._stop = False

    # ------------------------------------------------------------------ control
    def request_stop(self) -> None:
        self._stop = True

    @property
    def parameters(self) -> int:
        return count_parameters(self.model)

    # ------------------------------------------------------------------ data
    def _tensors(self, b: Batch) -> tuple[torch.Tensor, ...]:
        raw, pol = b.raw, b.policy
        if self.cfg.symmetries:
            raw, pol2 = apply_symmetry(raw, pol, self.py_rng.randrange(8))
            assert pol2 is not None
            pol = pol2
        sp, gl, legal = encode(raw)
        d = self.device
        return (
            torch.from_numpy(sp).to(d),
            torch.from_numpy(gl).to(d),
            torch.from_numpy(legal).to(d),
            torch.from_numpy(pol.astype(np.float32)).to(d),
            torch.from_numpy(b.value.astype(np.float32)).to(d),
            torch.from_numpy(b.score.astype(np.float32)).to(d),
        )

    def losses(self, b: Batch) -> tuple[torch.Tensor, dict[str, float]]:
        sp, gl, legal, pol, val, score = self._tensors(b)
        logits, v_logit, s_pred = self.model(sp, gl)
        logp = masked_log_softmax(logits, legal)
        has_pol = pol.sum(dim=1) > 0
        p_loss = -(pol * logp.clamp(min=-1e4)).sum(dim=1)
        p_loss = p_loss[has_pol].mean() if bool(has_pol.any()) else logits.sum() * 0
        has_v = ~torch.isnan(val)
        v_loss = (
            F.binary_cross_entropy_with_logits(v_logit[has_v], val[has_v])
            if bool(has_v.any())
            else v_logit.sum() * 0
        )
        has_s = ~torch.isnan(score)
        s_loss = (
            F.smooth_l1_loss(s_pred[has_s], score[has_s], beta=0.1) if bool(has_s.any()) else s_pred.sum() * 0
        )
        total = (
            self.cfg.policy_weight * p_loss + self.cfg.value_weight * v_loss + self.cfg.score_weight * s_loss
        )
        with torch.no_grad():
            acc = (
                (logits.masked_fill(~legal, -1e9).argmax(1) == pol.argmax(1))[has_pol].float().mean()
                if bool(has_pol.any())
                else torch.tensor(0.0)
            )
        return total, {
            "loss": float(total.detach()),
            "policy_ce": float(p_loss.detach()),
            "value_bce": float(v_loss.detach()),
            "score_huber": float(s_loss.detach()),
            "policy_top1": float(acc),
            "value_rows": int(has_v.sum()),
        }

    # ------------------------------------------------------------------ training
    def train_steps(
        self, data: Batch, steps: int, log: Any = None, weights: np.ndarray | None = None
    ) -> list[dict[str, Any]]:
        """``steps`` optimizer steps on minibatches sampled from ``data`` (``weights``: sampling weights)."""
        self.model.train()
        n = len(data)
        bs = min(self.cfg.batch_size, n)
        p = None if weights is None else weights / weights.sum()
        out = []
        for _ in range(steps):
            if self._stop:
                raise StopRequested
            idx = self.np_rng.choice(n, size=bs, replace=n < bs, p=p)
            t0 = time.monotonic()
            loss, metrics = self.losses(take(data, idx))
            self.opt.zero_grad(set_to_none=True)
            loss.backward()
            grads = [q.grad for q in self.model.parameters() if q.grad is not None]
            norm = float(torch.norm(torch.stack([g.detach().norm() for g in grads])))
            if not math.isfinite(norm) or not math.isfinite(metrics["loss"]):
                self.skipped += 1
                self.opt.zero_grad(set_to_none=True)
                metrics["skipped"] = True
            else:
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.cfg.grad_clip)
                self.opt.step()
            self.step += 1
            metrics.update(step=self.step, grad_norm=norm, seconds=round(time.monotonic() - t0, 4))
            out.append(metrics)
            if log is not None:
                log(metrics)
        return out

    @torch.no_grad()
    def evaluate_loss(self, data: Batch, max_rows: int = 4096) -> dict[str, float]:
        self.model.eval()
        n = min(len(data), max_rows)
        sym = self.cfg.symmetries
        self.cfg.symmetries = False
        try:
            _, m = self.losses(take(data, np.arange(n)))
        finally:
            self.cfg.symmetries = sym
        return m

    # ------------------------------------------------------------------ checkpoints
    def save(self, path: Path, extra: dict[str, Any] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        torch.save(
            {
                "model": self.model.state_dict(),
                "optimizer": self.opt.state_dict(),
                "step": self.step,
                "skipped": self.skipped,
                "net_config": self.net_cfg.to_dict(),
                "train_config": asdict(self.cfg),
                "feature_version": FEATURE_VERSION,
                "rng": {
                    "torch": torch.get_rng_state(),
                    "numpy": self.np_rng.bit_generator.state,
                    "python": self.py_rng.getstate(),
                },
                "extra": extra or {},
            },
            tmp,
        )
        tmp.replace(path)

    def load(self, path: Path, *, weights_only: bool = False) -> dict[str, Any]:
        ck = torch.load(path, map_location="cpu", weights_only=False)
        if ck.get("feature_version") != FEATURE_VERSION:
            raise ValueError(f"{path}: feature version {ck.get('feature_version')} != {FEATURE_VERSION}")
        self.model.load_state_dict(ck["model"])
        self.model.to(self.device)
        if not weights_only:
            self.opt.load_state_dict(ck["optimizer"])
            self.step = int(ck["step"])
            self.skipped = int(ck.get("skipped", 0))
            torch.set_rng_state(ck["rng"]["torch"])
            self.np_rng.bit_generator.state = ck["rng"]["numpy"]
            self.py_rng.setstate(ck["rng"]["python"])
        return dict(ck.get("extra", {}))

    # ------------------------------------------------------------------ export
    def export_onnx(self, path: Path, size: int, meta: dict[str, Any]) -> Path:
        """ONNX graph for onnxruntime (dynamic batch) plus ``model.json`` beside it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        model = VariantNet(self.net_cfg)
        model.load_state_dict({k: v.cpu() for k, v in self.model.state_dict().items()})
        model.eval()
        sp = torch.zeros(2, SPATIAL, size, size)
        gl = torch.zeros(2, GLOBAL)
        torch.onnx.export(
            model,
            (sp, gl),
            str(path),
            input_names=["spatial", "global"],
            output_names=["policy", "value", "score"],
            dynamic_axes={
                "spatial": {0: "batch"},
                "global": {0: "batch"},
                "policy": {0: "batch"},
                "value": {0: "batch"},
                "score": {0: "batch"},
            },
            opset_version=17,
            dynamo=False,
        )
        info = {
            **meta,
            "feature_version": FEATURE_VERSION,
            "architecture": {**self.net_cfg.to_dict(), "parameters": self.parameters},
            "outputs": {
                "policy": "N*N+1 logits, row-major from the top-left, pass last; mask illegal moves",
                "value": "logit of Black's win probability",
                "score": "Black's final area lead / N^2",
            },
            "train_step": self.step,
        }
        (path.parent / "model.json").write_text(json.dumps(info, indent=2) + "\n")
        return path

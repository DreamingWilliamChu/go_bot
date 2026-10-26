"""The small variant policy/value network (Route B, docs/VARIANT_TRAINING.md). Needs torch.

A residual tower with global features added after the stem and global-pooling bias in every second
block (as in KataGo, so the trunk can carry whole-board information), and three heads:

* policy: one logit per point from a 1x1 conv, plus a pass logit from pooled features (N*N+1)
* value: a logit of Black's win probability (the perspective is fixed: always Black)
* score: Black's final area lead divided by N*N (auxiliary; trained only on completed games)

The legal mask is applied *outside* the network (``masked_log_softmax``), identically in training
and inference.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
import torch.nn.functional as F
from torch import nn

from gobot.variant.models.features import FEATURE_VERSION, GLOBAL, SPATIAL


@dataclass
class NetConfig:
    blocks: int = 6
    channels: int = 64
    head_channels: int = 32
    value_hidden: int = 64
    feature_version: int = FEATURE_VERSION

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class GlobalPoolBias(nn.Module):
    """Adds a per-channel bias computed from the mean and max of ``g`` over the board."""

    def __init__(self, channels: int, pooled: int):
        super().__init__()
        self.fc = nn.Linear(2 * pooled, channels)

    def forward(self, x: torch.Tensor, g: torch.Tensor) -> torch.Tensor:
        pooled = torch.cat([g.mean(dim=(2, 3)), g.amax(dim=(2, 3))], dim=1)
        return x + self.fc(pooled)[:, :, None, None]


class ResBlock(nn.Module):
    def __init__(self, ch: int, pool: bool):
        super().__init__()
        self.c1 = nn.Conv2d(ch, ch, 3, padding=1, bias=False)
        self.b1 = nn.BatchNorm2d(ch)
        self.c2 = nn.Conv2d(ch, ch, 3, padding=1, bias=False)
        self.b2 = nn.BatchNorm2d(ch)
        self.pool = GlobalPoolBias(ch, ch) if pool else None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = F.relu(self.b1(self.c1(x)))
        if self.pool is not None:
            y = self.pool(y, y)
        y = self.b2(self.c2(y))
        return F.relu(x + y)


class VariantNet(nn.Module):
    def __init__(self, cfg: NetConfig | None = None):
        super().__init__()
        self.cfg = cfg = cfg or NetConfig()
        ch = cfg.channels
        self.stem = nn.Conv2d(SPATIAL, ch, 3, padding=1, bias=False)
        self.stem_bn = nn.BatchNorm2d(ch)
        self.glob = nn.Linear(GLOBAL, ch)
        self.blocks = nn.ModuleList([ResBlock(ch, pool=(i % 2 == 1)) for i in range(cfg.blocks)])
        hc = cfg.head_channels
        self.p_conv = nn.Conv2d(ch, hc, 1, bias=False)
        self.p_bn = nn.BatchNorm2d(hc)
        self.p_out = nn.Conv2d(hc, 1, 1)
        self.p_pass = nn.Linear(2 * hc, 1)
        self.v_conv = nn.Conv2d(ch, hc, 1, bias=False)
        self.v_bn = nn.BatchNorm2d(hc)
        self.v_fc = nn.Linear(2 * hc, cfg.value_hidden)
        self.v_out = nn.Linear(cfg.value_hidden, 1)
        self.s_out = nn.Linear(cfg.value_hidden, 1)

    def forward(
        self, spatial: torch.Tensor, glob: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x = self.stem_bn(self.stem(spatial)) + self.glob(glob)[:, :, None, None]
        x = F.relu(x)
        for blk in self.blocks:
            x = blk(x)
        p = F.relu(self.p_bn(self.p_conv(x)))
        pooled_p = torch.cat([p.mean(dim=(2, 3)), p.amax(dim=(2, 3))], dim=1)
        logits = torch.cat([self.p_out(p).flatten(1), self.p_pass(pooled_p)], dim=1)
        v = F.relu(self.v_bn(self.v_conv(x)))
        pooled_v = torch.cat([v.mean(dim=(2, 3)), v.amax(dim=(2, 3))], dim=1)
        h = F.relu(self.v_fc(pooled_v))
        return logits, self.v_out(h).squeeze(1), self.s_out(h).squeeze(1)


def masked_log_softmax(logits: torch.Tensor, legal: torch.Tensor) -> torch.Tensor:
    return torch.log_softmax(logits.masked_fill(~legal, -1e9), dim=1)


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())

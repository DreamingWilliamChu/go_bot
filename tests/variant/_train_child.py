"""Child process for test_training.test_interrupted_training_resumes."""

import signal
import sys
from pathlib import Path

import numpy as np

from gobot.variant.models.features import Raw
from gobot.variant.training.net import NetConfig
from gobot.variant.training.replay import Batch
from gobot.variant.training.train import StopRequested, TrainConfig, Trainer

out = Path(sys.argv[1])
tr = Trainer(
    NetConfig(blocks=2, channels=16, head_channels=8, value_hidden=16),
    TrainConfig(batch_size=16, device="cpu"),
)
signal.signal(signal.SIGINT, lambda *_: tr.request_stop())
n, a = 64, 49
rng = np.random.default_rng(0)
raw = Raw(
    7,
    rng.integers(0, 3, (n, a)).astype(np.int8),
    rng.integers(0, 4, (n, a)).astype(np.int8),
    np.full((n, 6), -1, np.int16),
    np.zeros((n, a), bool),
    np.ones((n, a + 1), bool),
    np.ones((n, 12), np.int16),
    np.full(n, 7.5, np.float32),
)
pol = rng.random((n, a + 1)).astype(np.float32)
data = Batch(
    raw,
    pol / pol.sum(1, keepdims=True),
    rng.integers(0, 2, n).astype(np.float32),
    np.full(n, np.nan, np.float32),
    np.zeros(n, np.int8),
)
print("training", flush=True)
try:
    while True:
        tr.train_steps(data, 5)
except StopRequested:
    tr.save(out / "ck.pt")
    print("saved", tr.step, flush=True)
    sys.exit(130)

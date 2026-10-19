"""Refit the stone classifier's logistic weights on the kaya-go/moku-v3 *validation* split.

    uv run python -m gobot.eval.fit_stone_model

Prints W_STONE / W_BLACK for src/gobot/vision/stones.py. Only the validation split is used, so the
reported test-set numbers (moku-v3 test, Tengen-Go, synthetic) stay out-of-sample.
"""

from __future__ import annotations

import numpy as np

from gobot.eval.datasets import moku_parquet
from gobot.settings import Settings
from gobot.vision.geometry import order_corners
from gobot.vision.stones import extract_features, feature_matrix


def _fit(x: np.ndarray, y: np.ndarray, l2: float = 1e-2, iters: int = 100) -> np.ndarray:
    w = np.zeros(x.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-x @ w))
        reg = l2 * np.r_[w[:-1], 0]
        g = x.T @ (p - y) / len(y) + reg
        h = (x * (p * (1 - p))[:, None]).T @ x / len(y) + l2 * np.eye(len(w))
        w -= np.linalg.solve(h, g)
    return w


def main() -> None:
    path = Settings().data_dir / "testsets" / "moku-v3" / "validation.parquet"
    xs, ys = [], []
    for s in moku_parquet(path, "validation"):
        if s.note or s.board is None or s.grid_corners is None or not (s.board != 0).any():
            continue
        f, _ = extract_features(s.load(), order_corners(s.grid_corners), s.size)
        # labels are orientation-free: pick the rotation where stones line up with hidden crossings
        t = max(
            (np.rot90(s.board, k) for k in range(4)), key=lambda t: int(((t != 0) & (f["line"] < 0.3)).sum())
        )
        xs.append(feature_matrix(f).reshape(-1, 6))
        ys.append(t.ravel())
    x, y = np.concatenate(xs), np.concatenate(ys)
    w_stone = _fit(x, (y != 0).astype(float))
    m = y != 0
    w_black = _fit(x[m], (y[m] == 1).astype(float))
    acc_s = np.mean(((x @ w_stone) > 0) == (y != 0))
    acc_b = np.mean(((x[m] @ w_black) > 0) == (y[m] == 1))
    print(
        f"points {len(y)} (black {(y == 1).sum()}, white {(y == -1).sum()}); train acc stone {acc_s:.4f}, black|stone {acc_b:.4f}"
    )
    print("W_STONE = np.array([" + ", ".join(f"{v:.4f}" for v in w_stone) + "])")
    print("W_BLACK = np.array([" + ", ".join(f"{v:.4f}" for v in w_black) + "])")


if __name__ == "__main__":
    main()

"""Classify each grid intersection as black, white or empty (classical features).

Works on the rectified grid image. Per intersection:
  * dL   - lightness relative to a smooth board-colour model fitted to the cell centres
  * dC   - chroma relative to the board (wood is yellow; stones are neutral)
  * line - visibility of the grid-line crossing at the stone's base (hidden by any stone)
  * ring - circular edge evidence around the stone body (the 30th percentile over angles, so the
           four grid-line crossings of an empty point do not count)
Stones stand above the board, so their visible body is shifted away from the camera; a single
global offset is estimated per photo and used for colour sampling (base point for line evidence).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from gobot.vision.board_detect import line_responses
from gobot.vision.geometry import rectify

CELL = 40


@dataclass
class StoneResult:
    labels: np.ndarray  # N x N int8: 1 black, -1 white, 0 empty
    confidence: np.ndarray  # N x N float 0..1
    features: dict[str, np.ndarray]
    offset: tuple[float, float]  # stone-body offset in cells (dx, dy)


def _fit_surface(xs: np.ndarray, ys: np.ndarray, v: np.ndarray, iters: int = 4) -> np.ndarray:
    """Robust quadratic surface fit; returns coefficients for [1, x, y, x^2, xy, y^2]."""
    A = np.stack([np.ones_like(xs), xs, ys, xs * xs, xs * ys, ys * ys], axis=1)
    keep = np.ones(len(v), bool)
    coef = np.zeros(6)
    for _ in range(iters):
        if keep.sum() < 8:
            break
        coef, *_ = np.linalg.lstsq(A[keep], v[keep], rcond=None)
        r = v - A @ coef
        mad = np.median(np.abs(r[keep] - np.median(r[keep]))) + 1e-6
        keep = np.abs(r) < 2.5 * 1.4826 * mad + 2.0
    return coef


def _eval_surface(coef: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    return coef[0] + coef[1] * xs + coef[2] * ys + coef[3] * xs * xs + coef[4] * xs * ys + coef[5] * ys * ys


def _disc_offsets(r: float) -> np.ndarray:
    ri = int(np.ceil(r))
    yy, xx = np.mgrid[-ri : ri + 1, -ri : ri + 1]
    m = xx * xx + yy * yy <= r * r
    return np.stack([xx[m], yy[m]], axis=1)


def _sample(img: np.ndarray, centers: np.ndarray, offs: np.ndarray) -> np.ndarray:
    """Values of img at centers + offs: (num_centers, num_offsets[, channels])."""
    h, w = img.shape[:2]
    pts = np.round(centers[:, None, :] + offs[None, :, :]).astype(int)
    xs = np.clip(pts[..., 0], 0, w - 1)
    ys = np.clip(pts[..., 1], 0, h - 1)
    return img[ys, xs]


def extract_features(
    rgb: np.ndarray, corners: np.ndarray, n: int, offset: tuple[float, float] | None = None
) -> tuple[dict, tuple[float, float]]:
    rect, _ = rectify(rgb, corners, n, CELL, margin=1.0)
    lab = cv2.cvtColor(rect, cv2.COLOR_RGB2LAB).astype(np.float32)
    L = lab[..., 0]
    chroma = np.sqrt((lab[..., 1] - 128) ** 2 + (lab[..., 2] - 128) ** 2)
    gy, gx = np.mgrid[0:n, 0:n]
    base = np.stack([(gx.ravel() + 1) * CELL, (gy.ravel() + 1) * CELL], axis=1).astype(np.float64)

    # board colour model from cell centres (never the centre of a stone)
    cy, cx = np.mgrid[0 : n - 1, 0 : n - 1]
    cc = np.stack([(cx.ravel() + 1.5) * CELL, (cy.ravel() + 1.5) * CELL], axis=1)
    small = _disc_offsets(0.12 * CELL)
    Lc = np.median(_sample(L, cc, small), axis=1)
    Cc = np.median(_sample(chroma, cc, small), axis=1)
    nx, ny = cc[:, 0] / (n * CELL), cc[:, 1] / (n * CELL)
    kL, kC = _fit_surface(nx, ny, Lc), _fit_surface(nx, ny, Cc)
    bx, by = base[:, 0] / (n * CELL), base[:, 1] / (n * CELL)
    board_L = _eval_surface(kL, bx, by)
    board_C = _eval_surface(kC, bx, by)

    # line evidence at the base point: mean response on the four half-segments leaving the
    # intersection, ignoring half-segments that would point off the grid (edges and corners)
    rv, rh = line_responses(L.astype(np.uint8), CELL)
    half = np.arange(int(0.06 * CELL), int(0.3 * CELL) + 1)
    zeros = np.zeros_like(half)
    dirs = {
        "up": (rv, np.stack([zeros, -half], axis=1)),
        "down": (rv, np.stack([zeros, half], axis=1)),
        "left": (rh, np.stack([-half, zeros], axis=1)),
        "right": (rh, np.stack([half, zeros], axis=1)),
    }
    gxr, gyr = gx.ravel(), gy.ravel()
    valid = {
        "up": gyr > 0,
        "down": gyr < n - 1,
        "left": gxr > 0,
        "right": gxr < n - 1,
    }
    side = np.array([[-1, 0], [0, 0], [1, 0]])
    total = np.zeros(len(base))
    count = np.zeros(len(base))
    for name, (resp, offs) in dirs.items():
        jitter = side if name in ("up", "down") else side[:, ::-1]
        v = np.max([_sample(resp, base + j, offs).mean(axis=1) for j in jitter], axis=0)
        total += np.where(valid[name], v, 0.0)
        count += valid[name]
    line = total / np.maximum(count, 1)
    # reference line strength: grid line midway between intersections (rarely fully covered)
    mids = np.stack([(gx[:, :-1].ravel() + 1.5) * CELL, (gy[:, :-1].ravel() + 1) * CELL], axis=1)
    ref = float(np.percentile(_sample(rh, mids, np.zeros((1, 2))).ravel(), 75)) + 1e-3
    line_n = line / ref

    disc = _disc_offsets(0.3 * CELL)
    stone_like = line_n < 0.5

    def body_stats(dx: float, dy: float) -> tuple[np.ndarray, np.ndarray]:
        c = base + np.array([dx, dy]) * CELL
        return np.median(_sample(L, c, disc), axis=1), np.median(_sample(chroma, c, disc), axis=1)

    if offset is None:
        best, best_sc = (0.0, 0.0), -1.0
        if stone_like.sum() >= 1:
            for dy in np.arange(-0.35, 0.351, 0.07):
                for dx in np.arange(-0.35, 0.351, 0.07):
                    Lm, _ = body_stats(dx, dy)
                    sc = float(np.abs(Lm - board_L)[stone_like].sum())
                    if sc > best_sc * 1.01:
                        best, best_sc = (float(dx), float(dy)), sc
        offset = best
    Lm, Cm = body_stats(*offset)

    # ring: radial gradient around the stone body
    gxm = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gym = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    rings = []
    for rr in (0.4, 0.46):
        o = np.stack([np.cos(ang), np.sin(ang)], axis=1) * rr * CELL
        c = base + np.array(offset) * CELL
        gxs = _sample(gxm, c, o)
        gys = _sample(gym, c, o)
        radial = np.abs(gxs * np.cos(ang)[None] + gys * np.sin(ang)[None])
        rings.append(np.percentile(radial, 30, axis=1))
    ring = np.max(rings, axis=0)
    ring_ref = float(np.percentile(np.hypot(gxm, gym), 90)) + 1e-3

    feats = {
        "dL": (Lm - board_L).reshape(n, n),
        "L": Lm.reshape(n, n),
        "boardL": board_L.reshape(n, n),
        "dC": (Cm - board_C).reshape(n, n),
        "boardC": board_C.reshape(n, n),
        "line": line_n.reshape(n, n),
        "ring": (ring / ring_ref).reshape(n, n),
    }
    return feats, offset


# Logistic models fitted on the kaya-go/moku-v3 *validation* split (53 photos; never on the test
# sets used for reporting). Features: log(line+0.05), dC/10, ring*5, Lrel, |Lrel-1|*3, bias.
# Refit with: uv run python -m gobot.eval.fit_stone_model
W_STONE = np.array([-1.8006, -0.7896, 0.1967, -0.1323, 1.6451, -4.4399])
W_BLACK = np.array([1.1345, 0.5851, 0.0745, -2.1700, 1.9827, 1.1707])
UNCERTAIN_BELOW = 0.8


def feature_matrix(f: dict) -> np.ndarray:
    line = f["line"]
    lrel = f["L"] / np.maximum(f["boardL"], 1)
    x = np.stack(
        [
            np.log(line + 0.05),
            np.clip(f["dC"], -40, 20) / 10,
            np.clip(f["ring"], 0, 1.5) * 5,
            np.clip(lrel, 0, 2.5),
            np.abs(lrel - 1) * 3,
        ],
        axis=-1,
    )
    return np.concatenate([x, np.ones(x.shape[:-1] + (1,))], axis=-1)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def probabilities(f: dict) -> tuple[np.ndarray, np.ndarray]:
    """(P(stone), P(black | stone)) per intersection."""
    x = feature_matrix(f)
    return _sigmoid(x @ W_STONE), _sigmoid(x @ W_BLACK)


def classify(rgb: np.ndarray, corners: np.ndarray, n: int) -> StoneResult:
    f, offset = extract_features(rgb, corners, n)
    p_stone, p_black = probabilities(f)
    labels = np.zeros((n, n), np.int8)
    stone = p_stone >= 0.5
    labels[stone & (p_black >= 0.5)] = 1
    labels[stone & (p_black < 0.5)] = -1
    conf = np.where(stone, p_stone * np.maximum(p_black, 1 - p_black), 1 - p_stone).astype(np.float32)
    f["p_stone"], f["p_black"] = p_stone, p_black
    return StoneResult(labels=labels, confidence=conf, features=f, offset=offset)

"""Deterministic synthetic board photos with exact ground truth (for regression tests and for
9x9/19x19 coverage). Synthetic results are reported separately from real photos and are not a
substitute for them.
"""

from __future__ import annotations

from collections.abc import Iterator

import cv2
import numpy as np

from gobot.eval.datasets import Sample


def _wood(rng: np.random.Generator, size: int) -> np.ndarray:
    base = np.array([rng.uniform(200, 235), rng.uniform(160, 195), rng.uniform(95, 135)])
    y = np.arange(size)[:, None]
    x = np.arange(size)[None, :]
    freq = rng.uniform(0.02, 0.06)
    streak = np.sin(y * freq + 3 * np.sin(x * 0.004 + rng.uniform(0, 6))) * rng.uniform(4, 10)
    noise = cv2.GaussianBlur(rng.normal(0, 6, (size, size)).astype(np.float32), (0, 0), 3)
    img = base[None, None, :] + (streak + noise)[..., None]
    return np.clip(img, 0, 255).astype(np.float32)


def render_board(
    rng: np.random.Generator, n: int, density: float, cell: int = 48
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    margin = rng.uniform(0.55, 0.9)
    side = int(round((n - 1 + 2 * margin) * cell))
    img = _wood(rng, side)
    off = margin * cell
    lw = max(1, int(round(cell * rng.uniform(0.03, 0.05))))
    for i in range(n):
        p = int(round(off + i * cell))
        cv2.line(img, (int(off), p), (int(off + (n - 1) * cell), p), (40, 30, 20), lw, cv2.LINE_AA)
        cv2.line(img, (p, int(off)), (p, int(off + (n - 1) * cell)), (40, 30, 20), lw, cv2.LINE_AA)
    stars = {19: [3, 9, 15], 13: [3, 6, 9], 11: [2, 5, 8], 9: [2, 4, 6]}[n]
    for sx in stars:
        for sy in stars:
            if n != 19 and (sx, sy) not in [
                (stars[0], stars[0]),
                (stars[2], stars[0]),
                (stars[0], stars[2]),
                (stars[2], stars[2]),
                (stars[1], stars[1]),
            ]:
                continue
            cv2.circle(
                img,
                (int(off + sx * cell), int(off + sy * cell)),
                max(2, cell // 12),
                (40, 30, 20),
                -1,
                cv2.LINE_AA,
            )
    board = np.zeros((n, n), np.int8)
    occ = rng.random((n, n)) < density
    colors = np.where(rng.random((n, n)) < 0.5, 1, -1)
    board[occ] = colors[occ]
    tilt_shift = rng.uniform(0.0, 0.25)  # stone-height parallax in cells (towards the image top)
    r = cell * rng.uniform(0.45, 0.49)
    for y in range(n):
        for x in range(n):
            if board[y, x] == 0:
                continue
            cx = off + x * cell
            cy = off + y * cell - tilt_shift * cell
            cv2.ellipse(
                img,
                (int(cx + 2), int(cy + 4)),
                (int(r), int(r * 0.95)),
                0,
                0,
                360,
                (90, 70, 40),
                -1,
                cv2.LINE_AA,
            )
            yy, xx = np.mgrid[-int(r) - 1 : int(r) + 2, -int(r) - 1 : int(r) + 2]
            d = np.sqrt(xx**2 + yy**2) / r
            m = d <= 1.0
            hl = np.exp(-(((xx + 0.35 * r) ** 2 + (yy + 0.35 * r) ** 2) / (0.25 * r * r)))
            if board[y, x] == 1:
                val = 25 + 70 * hl
                col = np.stack([val, val, val], -1)
            else:
                val = 215 + 35 * hl - 25 * d**2
                col = np.stack([val, val, val * 0.98], -1)
            y0, x0 = int(round(cy)) - int(r) - 1, int(round(cx)) - int(r) - 1
            ys, xs = slice(max(0, y0), y0 + m.shape[0]), slice(max(0, x0), x0 + m.shape[1])
            sub = img[ys, xs]
            mm = m[: sub.shape[0], : sub.shape[1]]
            sub[mm] = col[: sub.shape[0], : sub.shape[1]][mm]
    corners = np.array(
        [
            [off, off],
            [off + (n - 1) * cell, off],
            [off + (n - 1) * cell, off + (n - 1) * cell],
            [off, off + (n - 1) * cell],
        ]
    )
    return img, board, corners, side


def synth_photo(seed: int, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    density = rng.uniform(0.0, 0.45)
    board_img, board, gcorners, side = render_board(rng, n, density)
    W, H = (1600, 1200) if rng.random() < 0.5 else (1200, 1600)
    bg = np.full((H, W, 3), rng.uniform(30, 200, 3), np.float32)
    bg += cv2.GaussianBlur(rng.normal(0, 20, (H, W)).astype(np.float32), (0, 0), 8)[..., None]
    # perspective: board rectangle -> random quad (pitch + rotation + scale)
    s = rng.uniform(0.55, 0.85) * min(W, H)
    pitch = rng.uniform(0.0, 0.35)  # top edge shrink factor
    rot = np.deg2rad(rng.uniform(-18, 18))
    cx, cy = W / 2 + rng.uniform(-0.08, 0.08) * W, H / 2 + rng.uniform(-0.08, 0.08) * H
    top = s * (1 - pitch) / 2
    hgt = s * (1 - 0.5 * pitch)
    quad = np.array([[-top, -hgt / 2], [top, -hgt / 2], [s / 2, hgt / 2], [-s / 2, hgt / 2]])
    R = np.array([[np.cos(rot), -np.sin(rot)], [np.sin(rot), np.cos(rot)]])
    quad = quad @ R.T + [cx, cy]
    src = np.array([[0, 0], [side, 0], [side, side], [0, side]], np.float32)
    Hm = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    warped = cv2.warpPerspective(board_img, Hm, (W, H))
    mask = cv2.warpPerspective(np.ones((int(side), int(side)), np.float32), Hm, (W, H))[..., None]
    img = bg * (1 - mask) + warped * mask
    # lighting gradient + soft shadow
    gx = np.linspace(0, 1, W)[None, :]
    gy = np.linspace(0, 1, H)[:, None]
    a, b = rng.uniform(-0.35, 0.35, 2)
    light = 1 + a * (gx - 0.5) + b * (gy - 0.5)
    if rng.random() < 0.5:
        sx, sy = rng.uniform(0, W), rng.uniform(0, H)
        shadow = 1 - rng.uniform(0.15, 0.4) * np.exp(
            -(((gx * W - sx) ** 2 + (gy * H - sy) ** 2) / (2 * (0.2 * W) ** 2))
        )
        light = light * shadow
    img = img * light[..., None]
    img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.3, 1.4))
    img += rng.normal(0, rng.uniform(1, 6), img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(
        ".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, int(rng.uniform(60, 92))]
    )
    dec = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    assert dec is not None
    img = cv2.cvtColor(dec, cv2.COLOR_BGR2RGB)
    gc = cv2.perspectiveTransform(gcorners.reshape(-1, 1, 2).astype(np.float32), Hm).reshape(4, 2)
    return img, board, gc.astype(np.float64)


def synthetic(count_per_size: int = 12, seed: int = 1234) -> Iterator[Sample]:
    for n in (9, 13, 19):
        for i in range(count_per_size):
            sd = seed + n * 1000 + i

            def load(sd: int = sd, n: int = n) -> np.ndarray:
                return synth_photo(sd, n)[0]

            _, board, gc = synth_photo(sd, n)
            yield Sample(
                set="synthetic",
                name=f"synthetic/{n}/{i}",
                group=f"synthetic/{n}/{i}",
                size=n,
                load=load,
                board=board,
                grid_corners=gc,
            )

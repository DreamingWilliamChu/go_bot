"""Homography helpers. Grid coordinates (gx, gy) run 0..N-1 with (0, 0) at the top-left grid
corner as seen in the photo; ``corners`` are image points [TL, TR, BR, BL] of the outermost grid
intersections."""

from __future__ import annotations

import cv2
import numpy as np


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as TL, TR, BR, BL (clockwise from the top-left in image coordinates)."""
    p = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    c = p.mean(axis=0)
    ang = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    p = p[np.argsort(ang)]  # clockwise in image coords (y down), starting near -pi (left)
    # rotate so the first point is the one with the smallest x+y (top-left)
    k = int(np.argmin(p.sum(axis=1)))
    return np.roll(p, -k, axis=0)


def grid_to_image_h(corners: np.ndarray, size: int) -> np.ndarray:
    """Homography mapping grid coords (0..N-1) to image pixels."""
    src = np.array([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32)
    return cv2.getPerspectiveTransform(src, np.asarray(corners, dtype=np.float32))


def apply_h(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    ph = np.hstack([p, np.ones((len(p), 1))]) @ h.T
    return ph[:, :2] / ph[:, 2:3]


def grid_points(size: int) -> np.ndarray:
    gy, gx = np.mgrid[0:size, 0:size]
    return np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float64)


def is_convex_quad(corners: np.ndarray, min_area: float = 1.0) -> bool:
    p = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    cross = []
    for i in range(4):
        a, b, c = p[i], p[(i + 1) % 4], p[(i + 2) % 4]
        cross.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    same = all(x > 0 for x in cross) or all(x < 0 for x in cross)
    return bool(same and abs(cv2.contourArea(p.astype(np.float32))) >= min_area)


def rectify(
    rgb: np.ndarray, corners: np.ndarray, size: int, cell: int, margin: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    """Warp so grid point (gx, gy) lands at pixel ((gx + margin) * cell, (gy + margin) * cell).

    Returns (rectified image, homography image->rectified).
    """
    n = size - 1
    dst = (
        np.array(
            [[margin, margin], [margin + n, margin], [margin + n, margin + n], [margin, margin + n]],
            dtype=np.float32,
        )
        * cell
    )
    h = cv2.getPerspectiveTransform(np.asarray(corners, dtype=np.float32), dst)
    side = int(round((n + 2 * margin) * cell))
    out = cv2.warpPerspective(rgb, h, (side, side), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out, h


def rotate_corners(corners: np.ndarray, k: int) -> np.ndarray:
    """Re-label corners so the board is rotated by k*90 degrees clockwise in grid space."""
    return np.roll(np.asarray(corners), -k, axis=0)

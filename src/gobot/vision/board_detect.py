"""Classical (OpenCV) board and grid detection.

1. Candidate board quadrilaterals from colour segmentation (board colour sampled at the image centre)
   and from edge contours.
2. For each candidate, warp it to a square and fit exactly N equally spaced grid lines in each
   direction to directional line-response profiles (lines just outside the fitted grid are
   penalised, so a 13-line fit inside a 19x19 grid scores badly).
3. Refine the homography from detected grid-line crossings (RANSAC), which also corrects for the
   board's thickness and lens effects that make the outer board edge an imperfect guide.

The result is the four outermost grid intersections in image coordinates plus a quality score.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np

from gobot.vision.geometry import apply_h, grid_points, is_convex_quad, order_corners

WORK_SIDE = 1000


@dataclass
class GridFit:
    corners: np.ndarray  # TL, TR, BR, BL in the input image's pixels
    score: float  # 0..1-ish, higher is better
    method: str
    line_contrast: float = 0.0
    cross_fraction: float = 0.0
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------- line responses


def line_responses(gray: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Black-hat responses for thin dark vertical and horizontal lines."""
    k = max(5, int(round(cell * 0.45)) | 1)
    g = gray.astype(np.float32)
    # closing with a horizontal kernel removes thin dark vertical lines -> black-hat keeps them
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k))
    rv = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, kv)
    rh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, kh)
    return rv, rh


def _quad_from_points(pts: np.ndarray) -> np.ndarray | None:
    hull = cv2.convexHull(pts.astype(np.float32))
    if hull is None or len(hull) < 4:
        return None
    peri = cv2.arcLength(hull, True)
    approx = hull
    for eps in (0.005, 0.01, 0.02, 0.03):
        a = cv2.approxPolyDP(hull, eps * peri, True)
        if len(a) <= 12:
            approx = a
            break
    p = approx.reshape(-1, 2).astype(np.float64)
    if len(p) < 4:
        return None
    if len(p) == 4:
        return order_corners(p)
    best, best_area = None, 0.0
    for combo in itertools.combinations(range(len(p)), 4):
        q = p[list(combo)]
        area = abs(cv2.contourArea(q.astype(np.float32)))
        if area > best_area:
            best_area, best = area, q
    return order_corners(best) if best is not None else None


def color_quads(img: np.ndarray) -> list[np.ndarray]:
    """Board quads from colour similarity to the centre of the image."""
    h, w = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_s = cv2.GaussianBlur(lab, (0, 0), 3)
    out: list[np.ndarray] = []
    seeds = [(0.5, 0.5), (0.5, 0.42), (0.5, 0.58), (0.42, 0.5), (0.58, 0.5)]
    for sx, sy in seeds:
        cx, cy = int(sx * w), int(sy * h)
        r = max(4, int(0.04 * min(h, w)))
        patch = lab_s[cy - r : cy + r, cx - r : cx + r].reshape(-1, 3)
        ref = np.median(patch, axis=0)
        d = np.sqrt(
            ((lab_s[..., 1] - ref[1]) ** 2 + (lab_s[..., 2] - ref[2]) ** 2)
            + 0.25 * (lab_s[..., 0] - ref[0]) ** 2
        )
        for thr in (10.0, 16.0, 24.0, 34.0):
            mask = (d < thr).astype(np.uint8)
            k = max(3, int(0.012 * min(h, w)) | 1)
            mask = cv2.morphologyEx(
                mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            )
            n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            lab_id = labels[cy, cx]
            if lab_id == 0:
                continue
            area = stats[lab_id, cv2.CC_STAT_AREA]
            if area < 0.04 * h * w or area > 0.97 * h * w:
                continue
            comp = (labels == lab_id).astype(np.uint8)
            # fill holes (stones, lines) by flooding the outside
            ff = comp.copy()
            ff = cv2.copyMakeBorder(ff, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
            m2 = np.zeros((ff.shape[0] + 2, ff.shape[1] + 2), np.uint8)
            cv2.floodFill(ff, m2, (0, 0), 2)
            filled = (ff[1:-1, 1:-1] != 2).astype(np.uint8)
            cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            c = max(cnts, key=cv2.contourArea)
            q = _quad_from_points(c.reshape(-1, 2))
            if q is not None and is_convex_quad(q, 0.04 * h * w):
                # reject quads that are mostly not the component (e.g. leaked through background)
                qa = abs(cv2.contourArea(q.astype(np.float32)))
                if cv2.contourArea(c) / qa > 0.8:
                    out.append(q)
    return out


def edge_quads(img: np.ndarray) -> list[np.ndarray]:
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    out = []
    for blur, lo, hi in ((5, 40, 120), (9, 20, 60)):
        g = cv2.GaussianBlur(gray, (blur, blur), 0)
        e = cv2.Canny(g, lo, hi)
        e = cv2.dilate(e, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:12]
        for c in cnts:
            if cv2.contourArea(c) < 0.05 * h * w:
                continue
            q = _quad_from_points(c.reshape(-1, 2))
            if q is None or not is_convex_quad(q, 0.05 * h * w):
                continue
            qa = abs(cv2.contourArea(q.astype(np.float32)))
            hull_a = cv2.contourArea(cv2.convexHull(c))
            if hull_a / qa > 0.85:
                out.append(q)
    return out


def dedupe_quads(quads: list[np.ndarray], tol: float) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for q in quads:
        if all(np.max(np.linalg.norm(q - o, axis=1)) > tol for o in out):
            out.append(q)
    return out


# ---------------------------------------------------------------- 1-D grid fitting


def _pooled(profile: np.ndarray, rad: int) -> np.ndarray:
    k = 2 * rad + 1
    return cv2.dilate(profile.astype(np.float32).reshape(1, -1), np.ones((1, k), np.uint8)).ravel()


def fit_lines_1d(
    profile: np.ndarray, n: int, min_frac: float = 0.55, max_frac: float = 1.02
) -> tuple[float, float, float]:
    """Find offset o and spacing s maximising the mean profile at o + k*s (k = 0..n-1).

    Returns (o, s, score). The profile is normalised (median 0, robust scale 1). Lines just outside
    the fitted set (at o - s and o + n*s) are penalised.
    """
    w = len(profile)
    p = profile - np.median(profile)
    scale = np.percentile(np.abs(p), 90) + 1e-6
    p = np.clip(p / scale, -2, 6)
    pp = _pooled(p, 1)
    best = (0.0, 1.0, -1e9)
    s_lo = min_frac * w / (n - 1)
    s_hi = max_frac * w / (n - 1)
    ks = np.arange(n)
    for s in np.arange(s_lo, s_hi, 0.25):
        span = s * (n - 1)
        offs = np.arange(-0.03 * w, w - span + 0.03 * w, 0.5)
        pos = offs[:, None] + ks[None, :] * s  # (O, n)
        valid = (pos >= 0) & (pos <= w - 1)
        vals = np.interp(pos, np.arange(w), pp, left=0, right=0)
        vals[~valid] = -1.0
        score = vals.mean(axis=1)
        # penalise lines just outside the fitted grid (extra grid lines)
        outside_l = np.interp(offs - s, np.arange(w), pp, left=-1, right=-1)
        outside_r = np.interp(offs + n * s, np.arange(w), pp, left=-1, right=-1)
        score = score - 0.6 * np.clip(np.maximum(outside_l, outside_r), 0, None)
        i = int(np.argmax(score))
        if score[i] > best[2]:
            best = (float(offs[i]), float(s), float(score[i]))
    return best


def fit_grid_in_quad(
    img_gray: np.ndarray, quad: np.ndarray, n: int, side: int = 760
) -> tuple[np.ndarray, float] | None:
    """Fit an n x n grid inside a board quad; returns image-space grid corners and a score."""
    dst = np.array([[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]], dtype=np.float32)
    h = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    warp = cv2.warpPerspective(
        img_gray, h, (side, side), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    cell = side / (n + 0.5)
    rv, rh = line_responses(warp, cell)
    # ignore the outer 2% (board edge artefacts)
    m = int(0.02 * side)
    col = rv[m:-m].mean(axis=0)
    row = rh[:, m:-m].mean(axis=1)
    ox, sx, scx = fit_lines_1d(col, n)
    oy, sy, scy = fit_lines_1d(row, n)
    if sx <= 0 or sy <= 0:
        return None
    # aspect sanity: grid cells should be roughly square after warping the board quad
    if not (0.75 < sx / sy < 1.33):
        return None
    g = np.array(
        [[ox, oy], [ox + (n - 1) * sx, oy], [ox + (n - 1) * sx, oy + (n - 1) * sy], [ox, oy + (n - 1) * sy]]
    )
    corners = apply_h(np.linalg.inv(h), g)
    return order_corners(corners), float(min(scx, scy))


# ---------------------------------------------------------------- 2-D refinement from crossings


def cross_maps(gray_rect: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rv, rh = line_responses(gray_rect, cell)
    L = max(3, int(round(cell * 0.8)) | 1)
    v = cv2.blur(rv, (1, L))
    hh = cv2.blur(rh, (L, 1))
    return v + hh, rv, rh


def refine_grid(
    img_gray: np.ndarray,
    corners: np.ndarray,
    n: int,
    radii: tuple[float, ...] = (0.35, 0.22, 0.12),
    cell: int = 26,
) -> tuple[np.ndarray, dict]:
    """Iteratively re-fit the grid homography to detected line crossings."""
    info: dict = {"iterations": 0}
    cur = np.asarray(corners, dtype=np.float64)
    gp = grid_points(n)
    for r in radii:
        nn = n - 1
        dst = np.array([[1, 1], [1 + nn, 1], [1 + nn, 1 + nn], [1, 1 + nn]], dtype=np.float32) * cell
        h = cv2.getPerspectiveTransform(cur.astype(np.float32), dst)
        side = int((nn + 2) * cell)
        warp = cv2.warpPerspective(
            img_gray, h, (side, side), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        cmap, _, _ = cross_maps(warp, cell)
        rad = max(2, int(round(r * cell)))
        meas, src, vals = [], [], []
        for gx, gy in gp:
            px, py = int(round((gx + 1) * cell)), int(round((gy + 1) * cell))
            win = cmap[py - rad : py + rad + 1, px - rad : px + rad + 1]
            if win.size == 0:
                continue
            iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
            vals.append(float(win[iy, ix]))
            meas.append((px - rad + ix, py - rad + iy))
            src.append(((gx + 1) * cell, (gy + 1) * cell))
        vals_a = np.array(vals)
        if len(vals_a) < 8:
            break
        thr = np.median(vals_a)
        keep = vals_a >= thr  # crossings visible (empty points); stones hide them
        if keep.sum() < 8:
            break
        src_a = np.array(src, dtype=np.float32)[keep]
        meas_a = np.array(meas, dtype=np.float32)[keep]
        hm, inl = cv2.findHomography(src_a, meas_a, cv2.RANSAC, max(1.5, 0.12 * cell))
        if hm is None or inl is None or inl.sum() < max(8, 0.35 * keep.sum()):
            break
        # new grid corners in warp coords, then back to the image
        gc = apply_h(hm, dst.astype(np.float64))
        new = apply_h(np.linalg.inv(h), gc)
        if not is_convex_quad(new):
            break
        shift = np.max(np.linalg.norm(new - cur, axis=1)) / cell
        cur = new
        info["iterations"] += 1
        info["inliers"] = int(inl.sum())
        info["last_shift_px"] = float(shift)
    return order_corners(cur), info


def grid_quality(
    img_gray: np.ndarray, corners: np.ndarray, n: int, cell: int = 26
) -> tuple[float, float, float]:
    """(score, line_contrast, cross_fraction) for a candidate grid.

    * line_contrast: line response on the predicted grid lines vs. half a cell away.
    * cross_fraction: share of visible crossings whose peak is within 0.12 cell of the prediction.
    * a penalty when a line continues one full spacing outside any side of the grid (the fit is
      shifted, or the chosen board size is too small).
    """
    nn = n - 1
    m = 3  # margin in cells
    dst = np.array([[m, m], [m + nn, m], [m + nn, m + nn], [m, m + nn]], dtype=np.float32) * cell
    h = cv2.getPerspectiveTransform(np.asarray(corners, dtype=np.float32), dst)
    side = int((nn + 2 * m) * cell)
    warp = cv2.warpPerspective(
        img_gray, h, (side, side), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    cmap, rv, rh = cross_maps(warp, cell)
    a, b = m * cell, (m + nn) * cell

    def vline(x: float) -> float:
        xi = int(round(x))
        return float(rv[a:b, max(0, xi - 1) : xi + 2].max(axis=1).mean())

    def hline(y: float) -> float:
        yi = int(round(y))
        return float(rh[max(0, yi - 1) : yi + 2, a:b].max(axis=0).mean())

    on_v = np.mean([vline((m + i) * cell) for i in range(n)])
    off_v = np.mean([vline((m + i + 0.5) * cell) for i in range(nn)])
    on_h = np.mean([hline((m + i) * cell) for i in range(n)])
    off_h = np.mean([hline((m + i + 0.5) * cell) for i in range(nn)])
    on, off = (on_v + on_h) / 2, (off_v + off_h) / 2
    contrast = float((on - off) / (on + off + 1e-6))
    # On a real board the grid lines stop at the outermost line; if the perpendicular lines keep
    # going into the area just outside a side, the fit is shifted (or the board size too small).
    ys = [int(round((m + j) * cell)) for j in range(n)]
    xs = [int(round((m + i) * cell)) for i in range(n)]
    o0, o1 = int(round(0.15 * cell)), int(round(0.85 * cell))

    def ext_h(x0: int, x1: int) -> float:  # horizontal lines over columns x0..x1
        return float(np.mean([rh[max(0, y - 1) : y + 2, x0:x1].max(axis=0).mean() for y in ys]))

    def ext_v(y0: int, y1: int) -> float:
        return float(np.mean([rv[y0:y1, max(0, x - 1) : x + 2].max(axis=1).mean() for x in xs]))

    left, right = a - o1, b + o0
    outside = [
        ext_h(left, a - o0) / (on_h + 1e-6),
        ext_h(right, b + o1) / (on_h + 1e-6),
        ext_v(left, a - o0) / (on_v + 1e-6),
        ext_v(right, b + o1) / (on_v + 1e-6),
    ]
    outside_pen = float(max(outside))
    rad = max(2, int(0.3 * cell))
    good = total = 0
    covered_thr = float(np.percentile(cmap[a:b, a:b], 60))
    for gx, gy in grid_points(n):
        px, py = int(round((gx + m) * cell)), int(round((gy + m) * cell))
        win = cmap[py - rad : py + rad + 1, px - rad : px + rad + 1]
        if win.size == 0:
            continue
        if win.max() < covered_thr:
            continue  # covered by a stone
        total += 1
        iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
        if np.hypot(ix - rad, iy - rad) <= 0.12 * cell + 1:
            good += 1
    frac = good / total if total else 0.0
    score = 0.6 * max(0.0, contrast) + 0.4 * frac - 0.9 * max(0.0, outside_pen - 0.4)
    return float(score), contrast, float(frac)


def detect_grid(
    rgb: np.ndarray, n: int, work_side: int = WORK_SIDE, max_candidates: int = 10
) -> list[GridFit]:
    """Detect the n x n grid. Returns candidates sorted by score (best first), corners in rgb pixels."""
    h, w = rgb.shape[:2]
    s = min(1.0, work_side / max(h, w))
    img = (
        cv2.resize(rgb, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
        if s < 1
        else rgb
    )
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    quads = dedupe_quads(color_quads(img) + edge_quads(img), tol=0.02 * max(img.shape[:2]))
    first: list[tuple[float, np.ndarray]] = []
    for q in quads[: max_candidates * 2]:
        r = fit_grid_in_quad(gray, q, n)
        if r is None:
            continue
        corners0, s1 = r
        if not is_convex_quad(corners0, 0.02 * img.shape[0] * img.shape[1]):
            continue
        first.append((s1, corners0))
    first.sort(key=lambda t: t[0], reverse=True)
    uniq: list[tuple[float, np.ndarray]] = []
    for s1, c0 in first:
        if all(np.max(np.linalg.norm(c0 - u[1], axis=1)) > 0.01 * max(img.shape[:2]) for u in uniq):
            uniq.append((s1, c0))
    fits: list[GridFit] = []
    for s1, corners0 in uniq[:5]:
        refined, info = refine_grid(gray, corners0, n)
        score, contrast, frac = grid_quality(gray, refined, n)
        fits.append(
            GridFit(
                corners=refined / s,
                score=score,
                method="classical",
                line_contrast=contrast,
                cross_fraction=frac,
                details={"fit1d": s1, **info},
            )
        )
    fits.sort(key=lambda f: f.score, reverse=True)
    return fits


def refine_and_score(
    rgb: np.ndarray, corners: np.ndarray, n: int, method: str, radii=(0.3, 0.18, 0.1)
) -> GridFit:
    """Refine externally supplied corners (manual or moku) on the grid lines and score them."""
    h, w = rgb.shape[:2]
    s = min(1.0, WORK_SIDE / max(h, w))
    img = (
        cv2.resize(rgb, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
        if s < 1
        else rgb
    )
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    c0 = order_corners(np.asarray(corners, dtype=np.float64) * s)
    refined, info = refine_grid(gray, c0, n, radii=radii) if radii else (c0, {})
    sc_ref, con_ref, fr_ref = grid_quality(gray, refined, n)
    sc0, con0, fr0 = grid_quality(gray, c0, n)
    if sc0 >= sc_ref:
        return GridFit(
            corners=c0 / s,
            score=sc0,
            method=method,
            line_contrast=con0,
            cross_fraction=fr0,
            details={"refined": False},
        )
    return GridFit(
        corners=refined / s,
        score=sc_ref,
        method=method,
        line_contrast=con_ref,
        cross_fraction=fr_ref,
        details={"refined": True, **info},
    )

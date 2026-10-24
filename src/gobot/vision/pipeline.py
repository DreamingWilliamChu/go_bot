"""Photo -> board position, with explicit uncertainty and honest failure.

recognize() never invents a board: when no grid can be located with confidence it returns
``status="needs_corners"`` (the UI then asks the user to place the four corners), and points the
detectors disagree on are flagged ``uncertain`` for the user to check.
"""

from __future__ import annotations

import itertools
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gobot.go.coords import SUPPORTED_SIZES
from gobot.vision import board_detect, stones
from gobot.vision.geometry import apply_h, grid_to_image_h, is_convex_quad, order_corners
from gobot.vision.moku import MokuDetections, MokuDetector

log = logging.getLogger("gobot.vision")

GRID_ACCEPT = 0.48  # see docs/RECOGNITION.md: no wrong grid scored above 0.42 in the test sets
MANUAL_ACCEPT = 0.2  # manual corners are trusted unless the grid clearly does not match
UNCERTAIN_CONF = 0.8
SIZE_MARGIN = 0.01  # see docs/RECOGNITION.md: the right size never scored below another size in the test sets


@dataclass
class Recognition:
    status: str  # "ok" | "needs_corners"
    size: int
    corners: list[list[float]] | None = None  # TL, TR, BR, BL image pixels (normalised image)
    corner_source: str | None = None  # classical | moku | manual
    grid_score: float | None = None
    board: list[list[int]] | None = None  # [y][x]: 1 black, -1 white, 0 empty
    confidence: list[list[float]] | None = None
    uncertain: list[list[int]] = field(default_factory=list)  # [[x, y], ...]
    messages: list[str] = field(default_factory=list)
    size_suggestion: int | None = None
    detectors: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "size": self.size,
            "corners": self.corners,
            "cornerSource": self.corner_source,
            "gridScore": None if self.grid_score is None else round(self.grid_score, 3),
            "board": self.board,
            "confidence": self.confidence,
            "uncertain": self.uncertain,
            "messages": self.messages,
            "sizeSuggestion": self.size_suggestion,
            "detectors": self.detectors,
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
        }


class Recognizer:
    def __init__(self, moku_path: Path | None = None):
        self.moku: MokuDetector | None = None
        self.moku_error: str | None = None
        if moku_path is not None and moku_path.is_file():
            try:
                self.moku = MokuDetector(moku_path)
            except Exception as e:  # pragma: no cover - depends on onnxruntime install
                self.moku_error = f"moku model could not be loaded: {e}"
                log.warning(self.moku_error)

    @property
    def detector_names(self) -> list[str]:
        return ["classical"] + (["moku-v4"] if self.moku else [])

    # ------------------------------------------------------------------ corners
    def _moku_corner_fits(self, rgb: np.ndarray, det: MokuDetections, n: int) -> list[board_detect.GridFit]:
        fits = []
        top = det.corners[:5]
        area_min = 0.01 * rgb.shape[0] * rgb.shape[1]
        for combo in itertools.combinations(range(len(top)), 4):
            q = order_corners(top[list(combo)])
            if not is_convex_quad(q, area_min):
                continue
            fits.append(board_detect.refine_and_score(rgb, q, n, "moku"))
            if len(fits) >= 3:
                break
        return fits

    def find_grid(self, rgb: np.ndarray, n: int, det: MokuDetections | None) -> list[board_detect.GridFit]:
        fits = board_detect.detect_grid(rgb, n)
        if det is not None:
            fits += self._moku_corner_fits(rgb, det, n)
        fits.sort(key=lambda f: f.score, reverse=True)
        return fits

    # ------------------------------------------------------------------ stones
    @staticmethod
    def moku_board(det: MokuDetections, corners: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Map moku stone detections onto the grid: (labels, scores)."""
        labels = np.zeros((n, n), np.int8)
        scores = np.zeros((n, n), np.float32)
        if len(det.stones) == 0:
            return labels, scores
        h = grid_to_image_h(corners, n)
        g = apply_h(np.linalg.inv(h), det.stones)
        order = np.argsort(-det.stone_scores)
        for i in order:
            gx, gy = g[i]
            ix, iy = int(round(gx)), int(round(gy))
            if not (0 <= ix < n and 0 <= iy < n):
                continue
            if np.hypot(gx - ix, gy - iy) > 0.5 or scores[iy, ix] > 0:
                continue
            labels[iy, ix] = 1 if det.stone_colors[i] == 0 else -1
            scores[iy, ix] = det.stone_scores[i]
        return labels, scores

    def recognize(self, rgb: np.ndarray, n: int, corners: np.ndarray | None = None) -> Recognition:
        t0 = time.perf_counter()
        res = Recognition(status="needs_corners", size=n, detectors=self.detector_names)
        det = None
        if self.moku is not None:
            try:
                det = self.moku.detect(rgb)
                res.timings["moku"] = det.seconds
            except Exception as e:  # pragma: no cover
                log.warning("moku failed: %s", e)
                res.messages.append(
                    "The neural detector failed on this photo; using the classical detector only."
                )

        t1 = time.perf_counter()
        if corners is not None:
            c = order_corners(np.asarray(corners, dtype=np.float64))
            if not is_convex_quad(c, 100):
                res.messages.append("The four corners must form a convex shape around the grid.")
                return res
            fit = board_detect.refine_and_score(rgb, c, n, "manual", radii=(0.2, 0.1))
            moved = float(np.max(np.linalg.norm(fit.corners - c, axis=1)))
            cell = float(np.mean([np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)])) / (n - 1)
            if moved > 0.35 * cell:  # refinement may only nudge manual corners
                fit = board_detect.refine_and_score(rgb, c, n, "manual", radii=())
            best = fit
            accept = MANUAL_ACCEPT
            if best.score < accept:
                res.messages.append(
                    "The grid does not line up well with these corners. Check that each handle sits on the "
                    "outermost line crossing and that the board size is right."
                )
        else:
            fits = self.find_grid(rgb, n, det)
            res.timings["grid"] = time.perf_counter() - t1
            top = fits[0] if fits else None
            accept = GRID_ACCEPT
            if top is None or top.score < accept:
                res.messages.append(
                    "Could not find the board grid reliably. Drag the four handles onto the outermost "
                    "line crossings (corners of the grid), or retake the photo from straight above with the "
                    "whole board in view."
                )
                res.size_suggestion = self._suggest_size(rgb, n)
                if top is not None:
                    res.corners = top.corners.round(2).tolist()  # starting point for manual adjustment
                    res.corner_source = top.method
                    res.grid_score = top.score
                res.timings["total"] = time.perf_counter() - t0
                return res
            best = top
        res.corners = best.corners.round(2).tolist()
        res.corner_source = best.method
        res.grid_score = best.score

        t2 = time.perf_counter()
        sr = stones.classify(rgb, best.corners, n)
        res.timings["stones"] = time.perf_counter() - t2
        labels = sr.labels.copy()
        conf = sr.confidence.copy()
        uncertain = conf < UNCERTAIN_CONF
        if det is not None:
            ml, ms = self.moku_board(det, best.corners, n)
            disagree = ml != labels
            # Where the classical detector is unsure and moku saw a stone, take moku's call.
            take = disagree & (conf < 0.65) & (ml != 0)
            labels[take] = ml[take]
            uncertain |= disagree
        res.board = labels.astype(int).tolist()
        res.confidence = np.round(conf, 3).tolist()
        res.uncertain = [[int(x), int(y)] for y, x in np.argwhere(uncertain)]
        res.status = "ok"
        if best.score < GRID_ACCEPT:
            res.messages.append("Grid fit is weak; check the stones carefully.")
        if res.uncertain:
            res.messages.append(
                f"{len(res.uncertain)} point(s) are marked '?' because the detectors were unsure."
            )
        if corners is None:
            t3 = time.perf_counter()
            res.size_suggestion = self._suggest_size(rgb, n, accepted=True)
            res.timings["size_check"] = time.perf_counter() - t3
            if res.size_suggestion:
                m = res.size_suggestion
                res.messages.append(
                    f"The photo fits a {m}×{m} grid better than {n}×{n}. Check the board size before confirming."
                )
        res.timings["total"] = time.perf_counter() - t0
        return res

    @staticmethod
    def _size_scores(rgb: np.ndarray) -> dict[int, float]:
        """Best automatic grid score at every supported size (sizes run in parallel)."""

        def score(m: int) -> float:
            fits = board_detect.detect_grid(rgb, m, max_candidates=4)
            return fits[0].score if fits else -9.0

        with ThreadPoolExecutor(len(SUPPORTED_SIZES)) as ex:
            return dict(zip(SUPPORTED_SIZES, ex.map(score, SUPPORTED_SIZES), strict=True))

    def _suggest_size(self, rgb: np.ndarray, n: int, accepted: bool = False) -> int | None:
        """Another size whose grid fits the photo, or None. Once a grid has been accepted at size n,
        the other size must also beat n's own score (measured the same way) by SIZE_MARGIN."""
        scores = self._size_scores(rgb)
        best_n = max(scores, key=lambda m: scores[m])
        if best_n == n or scores[best_n] < GRID_ACCEPT:
            return None
        if accepted and scores[best_n] < scores[n] + SIZE_MARGIN:
            return None
        return best_n

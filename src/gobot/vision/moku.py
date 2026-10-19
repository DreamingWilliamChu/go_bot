"""moku-v4 detector (https://github.com/kaya-go/moku, AGPL-3.0), run with ONNX Runtime on the CPU.

ONNX contract (moku docs/kaya-v4-integration.md):
  input  ``pixel_values`` (1, 3, 640, 640): RGB in [0, 1], squashed resize, no normalisation
  output ``logits`` (1, 300, 3): classes black_stone, white_stone, board_corner (sigmoid -> score)
         ``pred_boxes`` (1, 300, 4): normalised (cx, cy, w, h)
         ``corner_points`` (1, 8, 3): (x, y, score) corner peaks, score already a probability
This module only decodes those outputs; the geometry/fusion is gobot's own code.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

INPUT = 640
STONE_THRESHOLD = 0.035  # moku's calibrated default for the v4 logits


@dataclass
class MokuDetections:
    stones: np.ndarray  # (k, 2) image pixel centres
    stone_colors: np.ndarray  # (k,) 0 = black, 1 = white
    stone_scores: np.ndarray  # (k,)
    stone_sizes: np.ndarray  # (k,) mean box side in pixels
    corners: np.ndarray  # (8, 2) image pixels
    corner_scores: np.ndarray  # (8,)
    seconds: float


class MokuDetector:
    def __init__(self, model_path: Path, threads: int | None = None):
        import onnxruntime as ort

        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=["CPUExecutionProvider"]
        )
        self.path = model_path
        self._lock = threading.Lock()

    def detect(self, rgb: np.ndarray, threshold: float = STONE_THRESHOLD) -> MokuDetections:
        import time

        h, w = rgb.shape[:2]
        x = cv2.resize(rgb, (INPUT, INPUT), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None]
        t0 = time.perf_counter()
        with self._lock:
            logits, boxes, corners = self.session.run(
                ["logits", "pred_boxes", "corner_points"], {"pixel_values": x}
            )
        dt = time.perf_counter() - t0
        logits, boxes, corners = logits[0], boxes[0], corners[0]
        scores = 1.0 / (1.0 + np.exp(-logits))
        cls = scores[:, :2].argmax(axis=1)
        best = scores[np.arange(len(scores)), cls]
        is_stone = (scores[:, :2].max(axis=1) >= scores[:, 2]) & (best >= threshold)
        b = boxes[is_stone]
        centers = np.stack([b[:, 0] * w, b[:, 1] * h], axis=1)
        sizes = (b[:, 2] * w + b[:, 3] * h) / 2
        return MokuDetections(
            stones=centers,
            stone_colors=cls[is_stone],
            stone_scores=best[is_stone],
            stone_sizes=sizes,
            corners=np.stack([corners[:, 0] * w, corners[:, 1] * h], axis=1),
            corner_scores=corners[:, 2].copy(),
            seconds=dt,
        )

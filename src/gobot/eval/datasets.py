"""Loaders for recognition test sets (downloaded by scripts/fetch_test_data.sh into data/testsets/).

Every sample yields an RGB image and whatever ground truth the set provides:
  * ``board``: N x N int8 (1 black, -1 white, 0 empty) in the photo's orientation, or None
  * ``grid_corners``: the 4 outermost grid intersections in image pixels (unordered), or None
  * ``rotation_free``: the set's position labels may be in any of the 4 board rotations
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from gobot.vision.geometry import apply_h, grid_to_image_h, order_corners


@dataclass
class Sample:
    set: str
    name: str
    group: str  # photos of the same physical scene share a group (for clustered statistics)
    size: int
    load: Callable[[], np.ndarray]
    board: np.ndarray | None
    grid_corners: np.ndarray | None
    rotation_free: bool = False
    note: str = ""


def _load_path(p: Path) -> Callable[[], np.ndarray]:
    def f() -> np.ndarray:
        return np.asarray(ImageOps.exif_transpose(Image.open(p)).convert("RGB"))

    return f


def parse_dotbw(text: str) -> np.ndarray:
    rows = [re.sub(r"\s+", "", line) for line in text.splitlines()]
    rows = [r for r in rows if r]
    n = len(rows)
    b = np.zeros((n, n), dtype=np.int8)
    for y, r in enumerate(rows):
        if len(r) != n:
            raise ValueError(f"dotBW row {y} has {len(r)} entries, expected {n}")
        for x, ch in enumerate(r):
            b[y, x] = 1 if ch == "B" else -1 if ch == "W" else 0
    return b


def tengen(root: Path) -> Iterator[Sample]:
    """Tengen-Go tests/vision/resources (AGPL-3.0, github.com/OliverBenz/Tengen-Go)."""
    base = root / "tests" / "vision" / "resources"
    for img in sorted(base.rglob("*")):
        if img.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        txt = img.with_suffix(".txt")
        board_txt = img.parent / "board.txt"
        board = None
        rotation_free = False
        if txt.is_file():
            board = parse_dotbw(txt.read_text())
        elif board_txt.is_file():
            board = parse_dotbw(board_txt.read_text())
            rotation_free = True
        geo = img.with_suffix(".json")
        corners = None
        size = board.shape[0] if board is not None else None
        if geo.is_file():
            g = json.loads(geo.read_text())
            corners = np.array(g["gridCorners"], dtype=np.float64)
            size = int(g.get("boardSize", size or 19))
        if size is None:
            continue
        rel = img.relative_to(base)
        yield Sample(
            set="tengen",
            name=str(rel),
            group=str(rel.parent),
            size=size,
            load=_load_path(img),
            board=board,
            grid_corners=corners,
            rotation_free=rotation_free,
        )


def _infer_size(rect: np.ndarray) -> int:
    if len(rect) == 0:
        return 19  # no stones to infer from: moku's own evaluation assumes 19x19
    best, best_res = 19, 1e9
    for n in (9, 13, 19):
        s = rect * (n - 1)
        res = float(np.mean(np.abs(s - np.round(s)))) if len(s) else 0.0
        if res < best_res:
            best, best_res = n, res
    return best


def moku_parquet(path: Path, split_name: str) -> Iterator[Sample]:
    """kaya-go/moku-v3 dataset split (Roboflow-derived photos, 640x640 squashed). Evaluation only."""
    import pyarrow.parquet as pq

    rows = pq.read_table(path).to_pylist()
    for r in rows:
        obj = r["objects"]
        cats = np.array(obj["category"])
        boxes = np.array(obj["bbox"], dtype=np.float64).reshape(-1, 4)
        centers = boxes[:, :2] + boxes[:, 2:] / 2
        corner_pts = centers[cats == 2]
        if len(corner_pts) != 4:
            continue
        corners = order_corners(corner_pts)
        h = grid_to_image_h(corners, 2)  # unit square (0..1)
        stones = centers[cats != 2]
        colors = cats[cats != 2]
        rect = apply_h(np.linalg.inv(h), stones) if len(stones) else np.zeros((0, 2))
        n = _infer_size(rect)
        board = np.zeros((n, n), dtype=np.int8)
        ok = True
        for (rx, ry), c in zip(rect, colors, strict=False):
            gx, gy = int(round(rx * (n - 1))), int(round(ry * (n - 1)))
            if not (0 <= gx < n and 0 <= gy < n) or board[gy, gx] != 0:
                ok = False
                continue
            board[gy, gx] = 1 if c == 0 else -1
        data = r["image"]["bytes"]

        def load(data: bytes = data) -> np.ndarray:
            return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))

        src = r.get("source_dataset", "")
        yield Sample(
            set=f"moku-v3-{split_name}",
            name=f"{split_name}/{r['image_id']}",
            group=f"{src}",
            size=n,
            load=load,
            board=board,
            grid_corners=corners,
            rotation_free=True,  # corner order in the annotations is ambiguous for rotated boards
            note="" if ok else "annotation conflict",
        )


def goban_data_set(root: Path, limit: int | None = None) -> Iterator[Sample]:
    """irglbriz/goban_data_set (MIT): 1000x1000 photos with grid ('field') corner labels; no stones."""
    labels = sorted((root / "training_corner_labels").glob("*.xml"))
    if limit:
        labels = labels[:limit]
    for lab in labels:
        tree = ET.parse(lab)
        pts = {}
        for obj in tree.findall("object"):
            name = obj.findtext("name") or ""
            if not name.endswith("field_corner"):
                continue
            pt = obj.find("point")
            if pt is not None:
                pts[name] = (float(pt.findtext("x1") or 0), float(pt.findtext("y1") or 0))
                continue
            bb = obj.find("bndbox")
            if bb is None:
                continue
            x = (float(bb.findtext("xmin") or 0) + float(bb.findtext("xmax") or 0)) / 2
            y = (float(bb.findtext("ymin") or 0) + float(bb.findtext("ymax") or 0)) / 2
            pts[name] = (x, y)
        if len(pts) != 4:
            continue
        img = root / "training_images_for_labels" / (lab.stem + ".png")
        if not img.is_file():
            cands = list((root / "training_images_for_labels").glob(lab.stem + ".*"))
            if not cands:
                continue
            img = cands[0]
        yield Sample(
            set="goban_data_set",
            name=lab.stem,
            group=lab.stem,
            size=19,
            load=_load_path(img),
            board=None,
            grid_corners=np.array(list(pts.values())),
        )


def user_testset(root: Path) -> Iterator[Sample]:
    """Photos saved from the app with "Save as test case" (data/user_testset/<id>/)."""
    if not root.is_dir():
        return
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        photo, board, labels = d / "photo.jpg", d / "board.txt", d / "labels.json"
        if not (photo.is_file() and board.is_file() and labels.is_file()):
            continue
        meta = json.loads(labels.read_text())
        yield Sample(
            set="user",
            name=d.name,
            group=d.name,
            size=int(meta["boardSize"]),
            load=_load_path(photo),
            board=parse_dotbw(board.read_text()),
            grid_corners=np.array(meta["gridCorners"], dtype=np.float64),  # as confirmed by the user
            rotation_free=False,  # saved in photo orientation
        )

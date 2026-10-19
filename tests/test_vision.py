from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from gobot.eval.datasets import parse_dotbw
from gobot.eval.recognition import corner_error
from gobot.eval.synthetic import synth_photo
from gobot.settings import Settings
from gobot.vision.geometry import order_corners
from gobot.vision.pipeline import GRID_ACCEPT, Recognizer

PHOTOS = Path(__file__).parent / "fixtures" / "photos"
MOKU = Settings().models_dir / "moku-v4.onnx"


def load(name: str) -> np.ndarray:
    return np.asarray(Image.open(PHOTOS / name).convert("RGB"))


def same_up_to_rotation(pred: np.ndarray, truth: np.ndarray) -> int:
    return min(int((np.rot90(truth, k) != pred).sum()) for k in range(4))


@pytest.fixture(scope="module", params=["classical", "classical+moku"])
def recognizer(request):
    if request.param == "classical+moku":
        if not MOKU.is_file():
            pytest.skip("moku-v4.onnx not downloaded")
        return Recognizer(MOKU)
    return Recognizer(None)


@pytest.mark.parametrize(
    "photo,labels,board,size",
    [
        ("tengen_angled_easy_1.jpeg", "tengen_angled_easy_1.json", "tengen_angled_easy_board.txt", 13),
        ("tengen_hard_lighting_2.jpeg", "tengen_hard_lighting_2.json", "tengen_hard_lighting_board.txt", 13),
        ("tengen_empty_19.jpeg", "tengen_empty_19.json", None, 19),
    ],
)
def test_real_photos(recognizer, photo, labels, board, size):
    rgb = load(photo)
    res = recognizer.recognize(rgb, size)
    assert res.status == "ok", res.messages
    gt = np.array(json.loads((PHOTOS / labels).read_text())["gridCorners"], float)
    assert corner_error(np.array(res.corners), gt, size) < 0.25
    pred = np.array(res.board)
    truth = parse_dotbw((PHOTOS / board).read_text()) if board else np.zeros((size, size), np.int8)
    errors = same_up_to_rotation(pred, truth)
    flagged = len(res.uncertain)
    assert errors <= 1, f"{errors} wrong points"
    assert errors <= flagged  # any mistake must be visible to the user as '?'


@pytest.mark.parametrize("seed,size", [(11, 9), (12, 13), (13, 19), (14, 19)])
def test_synthetic_boards(recognizer, seed, size):
    rgb, truth, gc = synth_photo(seed, size)
    res = recognizer.recognize(rgb, size)
    assert res.status == "ok"
    assert corner_error(np.array(res.corners), gc, size) < 0.3
    pred = np.array(res.board)
    flagged = np.zeros_like(pred, bool)
    for x, y in res.uncertain:
        flagged[y, x] = True
    k = min(range(4), key=lambda k: int((np.rot90(truth, k) != pred).sum()))
    wrong = np.rot90(truth, k) != pred
    assert wrong.sum() <= 2
    # The renderer shifts stones up to 0.25 cell (parallax), so on dense boards a neighbour's stone
    # can genuinely cover an empty crossing (seed 14 has one such point). Allow one unflagged error
    # here; the real-photo tests above require every error to be flagged.
    assert (wrong & ~flagged).sum() <= 1


def test_wrong_size_is_not_accepted_and_suggests_size():
    rgb = load("tengen_angled_easy_1.jpeg")  # a 13x13 board
    res = Recognizer(None).recognize(rgb, 19)
    assert res.status == "needs_corners"
    assert res.board is None  # never a guessed board
    assert res.size_suggestion == 13


def test_non_board_image_needs_corners():
    rng = np.random.default_rng(0)
    rgb = (rng.random((900, 1200, 3)) * 255).astype(np.uint8)
    res = Recognizer(None).recognize(rgb, 19)
    assert res.status == "needs_corners" and res.board is None
    assert res.messages


def test_manual_corners_used_and_only_nudged():
    rgb = load("tengen_angled_easy_1.jpeg")
    gt = order_corners(
        np.array(json.loads((PHOTOS / "tengen_angled_easy_1.json").read_text())["gridCorners"], float)
    )
    rough = gt + np.array([[6, -5], [-4, 6], [5, 4], [-6, -3]])  # a few pixels off, like a finger
    res = Recognizer(None).recognize(rgb, 13, corners=rough)
    assert res.status == "ok" and res.corner_source == "manual"
    assert corner_error(np.array(res.corners), gt, 13) < 0.15
    assert res.grid_score is not None and res.grid_score > GRID_ACCEPT


@pytest.mark.parametrize("seed,size", [(21, 9), (22, 13), (23, 19)])
def test_photo_orientation_matches_board_coordinates(seed, size):
    """The reconstructed board must be in photo orientation: grid (0, 0) is the top-left crossing of
    the photo, so no rotation is needed to match the rendered truth (x right, y down)."""
    rgb, truth, _ = synth_photo(seed, size)
    assert (truth != np.rot90(truth)).any()  # asymmetric position, so a rotation would be detected
    res = Recognizer(None).recognize(rgb, size)
    assert res.status == "ok"
    pred = np.array(res.board)
    errors_by_rotation = [int((np.rot90(truth, k) != pred).sum()) for k in range(4)]
    assert errors_by_rotation[0] == min(errors_by_rotation) <= 2
    # top-left reported corner is the top-left of the image quadrant
    c = np.array(res.corners)
    assert c[0].sum() == c.sum(axis=1).min()

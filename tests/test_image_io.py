import io

import numpy as np
import pytest
from PIL import Image

from gobot.vision.image_io import ImageError, load_image

# How to *store* an upright image so that a viewer applying EXIF orientation v shows it upright.
STORE = {
    1: lambda im: im,
    2: lambda im: im.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
    3: lambda im: im.transpose(Image.Transpose.ROTATE_180),
    4: lambda im: im.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
    5: lambda im: im.transpose(Image.Transpose.TRANSPOSE),
    6: lambda im: im.transpose(Image.Transpose.ROTATE_90),  # iPhone portrait photos
    7: lambda im: im.transpose(Image.Transpose.TRANSVERSE),
    8: lambda im: im.transpose(Image.Transpose.ROTATE_270),
}


def upright() -> Image.Image:
    """600x400 grey image with a red block in the top-left and a blue block in the top-right."""
    a = np.full((400, 600, 3), 128, np.uint8)
    a[:80, :80] = (230, 20, 20)
    a[:80, -80:] = (20, 20, 230)
    return Image.fromarray(a)


@pytest.mark.parametrize("orientation", list(range(1, 9)))
def test_exif_orientation_restores_upright(orientation):
    stored = STORE[orientation](upright())
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    stored.save(buf, format="JPEG", quality=95, exif=exif)
    img = load_image(buf.getvalue())
    assert img.rgb.shape[:2] == (400, 600)
    assert img.exif_orientation == orientation
    tl = img.rgb[:40, :40].mean(axis=(0, 1))
    tr = img.rgb[:40, -40:].mean(axis=(0, 1))
    assert tl[0] > 180 and tl[2] < 80, f"red block not top-left for orientation {orientation}"
    assert tr[2] > 180 and tr[0] < 80, f"blue block not top-right for orientation {orientation}"


def test_heic_decoding():
    pillow_heif = pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    try:
        pillow_heif.from_pillow(upright()).save(buf, quality=90)
    except Exception as e:  # pragma: no cover - encoder missing on some platforms
        pytest.skip(f"HEIF encoder unavailable: {e}")
    data = buf.getvalue()
    assert data[4:8] == b"ftyp"
    img = load_image(data)
    assert img.rgb.shape[:2] == (400, 600)
    assert img.rgb[:40, :40].mean(axis=(0, 1))[0] > 170


def test_png_rgba_and_downscale():
    a = np.zeros((3000, 4000, 4), np.uint8)
    a[..., 1] = 200
    a[..., 3] = 255
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="PNG")
    img = load_image(buf.getvalue())
    assert max(img.rgb.shape[:2]) == 2048
    assert img.scale == pytest.approx(2048 / 4000)
    assert img.original_size == (4000, 3000)


def test_rejects_bad_input():
    with pytest.raises(ImageError):
        load_image(b"")
    with pytest.raises(ImageError, match="not a supported image"):
        load_image(b"hello world, not an image")
    buf = io.BytesIO()
    Image.new("RGB", (100, 80)).save(buf, format="JPEG")
    with pytest.raises(ImageError, match="too small"):
        load_image(buf.getvalue())


def test_quality_warnings_dark():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 10, 10)).save(buf, format="JPEG")
    img = load_image(buf.getvalue())
    assert any("dark" in w for w in img.warnings)

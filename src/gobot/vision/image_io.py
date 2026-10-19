"""Photo decoding and normalisation.

* EXIF orientation is applied (iPhone photos are usually stored rotated with an orientation tag).
* JPEG, PNG, WebP and HEIC/HEIF are accepted. HEIC is decoded with pillow-heif; on macOS the
  built-in ``sips`` tool is a fallback.
* The result is an upright RGB image, downscaled so the long side is at most ``MAX_SIDE``. The browser
  always displays this normalised image, so corner/stone pixel coordinates mean the same thing on
  both sides.
"""

from __future__ import annotations

import io
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # registers HEIF/HEIC support in Pillow
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # pragma: no cover - optional
    HEIF_AVAILABLE = False

MAX_SIDE = 2048
MIN_SHORT_SIDE = 320
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 120_000_000  # 12k x 10k; larger is refused (decompression bombs)


class ImageError(ValueError):
    """A user-facing problem with the photo (message explains what to do)."""


@dataclass
class LoadedImage:
    rgb: np.ndarray  # HxWx3 uint8, upright
    original_size: tuple[int, int]  # (w, h) after EXIF transpose, before downscale
    scale: float  # normalised = original * scale
    format: str
    exif_orientation: int | None
    warnings: list[str] = field(default_factory=list)


def _is_heif(data: bytes) -> bool:
    return (
        len(data) > 12
        and data[4:8] == b"ftyp"
        and data[8:12]
        in (
            b"heic",
            b"heix",
            b"hevc",
            b"hevx",
            b"mif1",
            b"msf1",
            b"heim",
            b"heis",
            b"avic",
        )
    )


def _sips_to_jpeg(data: bytes) -> bytes | None:  # pragma: no cover - macOS only
    if platform.system() != "Darwin" or not shutil.which("sips"):
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.heic"
        dst = Path(td) / "out.jpg"
        src.write_bytes(data)
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)], capture_output=True, timeout=60
        )
        if r.returncode == 0 and dst.is_file():
            return dst.read_bytes()
    return None


def load_image(data: bytes, max_side: int = MAX_SIDE) -> LoadedImage:
    if not data:
        raise ImageError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError("The photo is larger than 40 MB. Please use a normal camera photo.")
    img: Image.Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        if _is_heif(data):
            conv = _sips_to_jpeg(data)
            if conv is None:
                raise ImageError(
                    "This HEIC photo could not be decoded. On the iPhone, choose 'Most Compatible' in "
                    "Settings > Camera > Formats, or share the photo as JPEG."
                ) from e
            img = Image.open(io.BytesIO(conv))
            img.load()
        else:
            raise ImageError("This file is not a supported image (use JPEG, PNG or HEIC).") from e
    except Image.DecompressionBombError as e:
        raise ImageError("The image is too large.") from e
    fmt = img.format or "unknown"
    orientation = None
    try:
        orientation = img.getexif().get(0x0112)
    except Exception:
        orientation = None
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    w, h = img.size
    if min(w, h) < MIN_SHORT_SIDE:
        raise ImageError(
            f"The photo is too small ({w}x{h}). Please take a full-resolution photo of the whole board."
        )
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.Resampling.LANCZOS)
    rgb = np.asarray(img, dtype=np.uint8).copy()
    return LoadedImage(
        rgb=rgb,
        original_size=(w, h),
        scale=scale,
        format=fmt,
        exif_orientation=orientation,
        warnings=quality_warnings(rgb),
    )


def quality_warnings(rgb: np.ndarray) -> list[str]:
    """Cheap checks for photos that are unlikely to work; phrased as advice."""
    small = cv2.resize(rgb, (0, 0), fx=800 / max(rgb.shape[:2]), fy=800 / max(rgb.shape[:2]))
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    out = []
    mean = float(gray.mean())
    if mean < 45:
        out.append("The photo is very dark. More light (or no shadow over the board) will help.")
    if mean > 225:
        out.append("The photo is very bright/overexposed.")
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharp < 25:
        out.append("The photo looks blurry. Hold the phone still and tap to focus on the board.")
    return out


def to_jpeg(rgb: np.ndarray, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()

"""Photo upload and recognition endpoints."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, field_validator

from gobot.settings import Settings
from gobot.vision.image_io import MAX_UPLOAD_BYTES, ImageError, load_image, to_jpeg
from gobot.vision.pipeline import Recognizer

ID_RE = re.compile(r"^[0-9a-f]{16}$")
KEEP_SECONDS = 7 * 24 * 3600
MAX_KEEP = 300


class RecognizeRequest(BaseModel):
    size: int
    corners: list[list[float]] | None = Field(default=None, description="TL, TR, BR, BL in image pixels")

    @field_validator("size")
    @classmethod
    def _size(cls, v: int) -> int:
        if v not in (9, 13, 19):
            raise ValueError("size must be 9, 13 or 19")
        return v

    @field_validator("corners")
    @classmethod
    def _corners(cls, v: list[list[float]] | None) -> list[list[float]] | None:
        if v is None:
            return v
        if len(v) != 4 or any(len(p) != 2 for p in v):
            raise ValueError("corners must be four [x, y] points")
        return v


class TestCaseRequest(BaseModel):
    size: int
    corners: list[list[float]]
    board: list[list[int]]
    next_player: str | None = None
    note: str | None = None


class PhotoStore:
    def __init__(self, settings: Settings):
        self.dir = settings.uploads_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.testset_dir = settings.user_testset_dir
        self._prune()

    def _prune(self) -> None:
        files = sorted(self.dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        for i, f in enumerate(files):
            if i >= MAX_KEEP or now - f.stat().st_mtime > KEEP_SECONDS:
                f.unlink(missing_ok=True)
                f.with_suffix(".json").unlink(missing_ok=True)

    def path(self, pid: str) -> Path:
        if not ID_RE.match(pid):
            raise HTTPException(404, "unknown photo")
        p = self.dir / f"{pid}.jpg"
        if not p.is_file():
            raise HTTPException(404, "Photo not found (it may have expired). Please upload it again.")
        return p

    def save(self, rgb: np.ndarray, meta: dict[str, Any]) -> str:
        pid = uuid.uuid4().hex[:16]
        (self.dir / f"{pid}.jpg").write_bytes(to_jpeg(rgb, 92))
        (self.dir / f"{pid}.json").write_text(json.dumps(meta))
        self._prune()
        return pid

    def load_rgb(self, pid: str) -> np.ndarray:
        return np.asarray(Image.open(self.path(pid)).convert("RGB"))


def build_router(settings: Settings) -> tuple[APIRouter, Any]:
    router = APIRouter()
    store = PhotoStore(settings)
    state: dict[str, Recognizer | None] = {"rec": None}
    lock = asyncio.Lock()
    moku_path = settings.models_dir / "moku-v4.onnx"

    async def recognizer() -> Recognizer:
        async with lock:
            if state["rec"] is None:
                state["rec"] = await asyncio.to_thread(Recognizer, moku_path if moku_path.is_file() else None)
            rec = state["rec"]
            assert rec is not None
            return rec

    def status() -> dict[str, Any]:
        rec = state["rec"]
        return {
            "detectors": rec.detector_names
            if rec
            else (["classical"] + (["moku-v4"] if moku_path.is_file() else [])),
            "moku": {
                "installed": moku_path.is_file(),
                "file": moku_path.name if moku_path.is_file() else None,
                "error": rec.moku_error if rec else None,
            },
        }

    @router.post("/api/photos")
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        try:
            img = await asyncio.to_thread(load_image, data)
        except ImageError as e:
            raise HTTPException(422, str(e)) from e
        h, w = img.rgb.shape[:2]
        meta = {
            "filename": file.filename,
            "contentType": file.content_type,
            "format": img.format,
            "exifOrientation": img.exif_orientation,
            "originalSize": img.original_size,
            "width": w,
            "height": h,
            "uploaded": time.time(),
        }
        pid = await asyncio.to_thread(store.save, img.rgb, meta)
        return {
            "id": pid,
            "url": f"/api/photos/{pid}.jpg",
            "width": w,
            "height": h,
            "format": img.format,
            "exifOrientation": img.exif_orientation,
            "warnings": img.warnings,
        }

    @router.get("/api/photos/{pid}.jpg")
    async def get_photo(pid: str) -> FileResponse:
        return FileResponse(
            store.path(pid), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
        )

    @router.post("/api/photos/{pid}/recognize")
    async def recognize(pid: str, body: RecognizeRequest) -> dict[str, Any]:
        rgb = await asyncio.to_thread(store.load_rgb, pid)
        rec = await recognizer()
        corners = np.array(body.corners, dtype=np.float64) if body.corners else None
        res = await asyncio.to_thread(rec.recognize, rgb, body.size, corners)
        out = res.to_json()
        out["photoId"] = pid
        return out

    @router.post("/api/photos/{pid}/testcase")
    async def save_testcase(pid: str, body: TestCaseRequest) -> dict[str, Any]:
        src = store.path(pid)
        n = body.size
        if n not in (9, 13, 19) or len(body.board) != n or any(len(r) != n for r in body.board):
            raise HTTPException(422, "board does not match size")
        d = store.testset_dir / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "photo.jpg").write_bytes(src.read_bytes())
        rows = ["".join("B" if v == 1 else "W" if v == -1 else "." for v in row) for row in body.board]
        (d / "board.txt").write_text("\n".join(rows) + "\n")
        (d / "labels.json").write_text(
            json.dumps(
                {
                    "boardSize": n,
                    "gridCorners": body.corners,
                    "nextPlayer": body.next_player,
                    "note": body.note,
                },
                indent=2,
            )
        )
        return {"saved": str(d)}

    return router, status


def attach(app: Any, settings: Settings) -> None:
    router, status = build_router(settings)
    app.include_router(router)
    app.state.recognition_status = status

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from gobot.app import create_app

PHOTOS = Path(__file__).parent / "fixtures" / "photos"


def client(tmp_settings, tmp_path):
    tmp_settings.models_dir = tmp_path / "models"  # no moku: classical only, fast
    tmp_settings.models_dir.mkdir()
    import shutil

    from gobot.settings import repo_root

    shutil.copy(repo_root() / "models" / "manifest.json", tmp_settings.models_dir / "manifest.json")
    return TestClient(
        create_app(tmp_settings, start_engine=False), client=("127.0.0.1", 5000), base_url="http://localhost"
    )


def test_upload_recognize_and_testcase(tmp_settings, tmp_path):
    with client(tmp_settings, tmp_path) as c:
        data = (PHOTOS / "tengen_angled_easy_1.jpeg").read_bytes()
        r = c.post("/api/photos", files={"file": ("IMG_0001.jpeg", data, "image/jpeg")})
        assert r.status_code == 200, r.text
        up = r.json()
        assert up["width"] > 0 and up["url"].endswith(".jpg")
        img = c.get(up["url"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        rec = c.post(f"/api/photos/{up['id']}/recognize", json={"size": 13}).json()
        assert rec["status"] == "ok"
        assert len(rec["board"]) == 13 and len(rec["corners"]) == 4
        assert rec["detectors"] == ["classical"]
        # manual corners round trip
        rec2 = c.post(
            f"/api/photos/{up['id']}/recognize", json={"size": 13, "corners": rec["corners"]}
        ).json()
        assert rec2["status"] == "ok" and rec2["cornerSource"] == "manual"
        saved = c.post(
            f"/api/photos/{up['id']}/testcase",
            json={"size": 13, "corners": rec["corners"], "board": rec["board"], "next_player": "W"},
        )
        assert saved.status_code == 200
        d = tmp_settings.user_testset_dir / up["id"]
        assert (d / "photo.jpg").is_file() and (d / "board.txt").read_text().count("\n") == 13
        assert json.loads((d / "labels.json").read_text())["nextPlayer"] == "W"
        st = c.get("/api/status").json()
        assert st["recognition"]["moku"]["installed"] is False


def test_bad_uploads(tmp_settings, tmp_path):
    with client(tmp_settings, tmp_path) as c:
        r = c.post("/api/photos", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
        assert r.status_code == 422 and "not a supported image" in r.json()["detail"]
        assert c.get("/api/photos/..%2F..%2Fetc%2Fpasswd.jpg").status_code == 404
        assert c.get("/api/photos/0123456789abcdef.jpg").status_code == 404
        r = c.post("/api/photos/0123456789abcdef/recognize", json={"size": 13})
        assert r.status_code == 404
        r = c.post("/api/photos/0123456789abcdef/recognize", json={"size": 12})
        assert r.status_code == 422

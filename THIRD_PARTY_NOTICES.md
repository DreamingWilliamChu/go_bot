# Third-party software, models and data

This repository's own code is licensed under the GNU Affero General Public License v3.0 (see `LICENSE`). It uses the components below. Downloaded models and datasets are **not** stored in this repository; the scripts fetch them from their original sources at pinned versions.

## Go engine and networks

| Component | Source | License | How it is used |
|---|---|---|---|
| KataGo engine (v1.18.x) | https://github.com/lightvector/KataGo | MIT (© David J Wu and contributors). Vendored third-party parts carry their own licenses (see KataGo's `cpp/external`) | Installed separately via Homebrew on the Mac (`brew install katago`); official Linux release binary for tests/CI |
| kata1 neural networks (`kata1-tf3-b11c768…`, `kata1-tf3-b10c512…`, `kata1-tf2-b10c384…`, `kata1-b18c384nbt…`, `kata1-b28c512nbt…`) | https://katagotraining.org/networks/ | KataGo Neural Network License (MIT-style), https://katagotraining.org/network_license/ | Downloaded by `scripts/download_models.sh`; versions and sha256 in `models/manifest.json` |

## Photo recognition

| Component | Source | License | How it is used |
|---|---|---|---|
| moku-v4 detector (`model.onnx`, rev `0449e6af`) | https://huggingface.co/kaya-go/moku-v4, code https://github.com/kaya-go/moku | AGPL-3.0 | Optional; downloaded by the setup script and run with ONNX Runtime. No moku code is copied: the decoding follows its documented ONNX contract |
| OpenCV (opencv-python-headless) | https://opencv.org | Apache-2.0 | Image processing |
| ONNX Runtime | https://onnxruntime.ai | MIT | Runs moku-v4 on the CPU |
| Pillow | https://python-pillow.org | MIT-CMU (HPND) | Image decoding, EXIF orientation |
| pillow-heif (bundles libheif, libde265, x265) | https://github.com/bigcat88/pillow_heif | BSD-3-Clause; bundled libheif/libde265 LGPL-3.0, x265 GPL-2.0 | HEIC/HEIF decoding |

## Server and web app

| Component | License |
|---|---|
| FastAPI, Starlette, Pydantic, uvicorn, websockets, python-multipart | MIT / BSD-3-Clause |
| NumPy, psutil | BSD-3-Clause |
| cryptography | Apache-2.0 or BSD-3-Clause |
| segno (QR codes) | BSD-3-Clause |
| Preact | MIT |
| @sabaki/go-board, @sabaki/sgf (and its dependency doken) | MIT |
| Vite, Vitest, TypeScript, Playwright (development only) | MIT / Apache-2.0 |

Exact versions are pinned in `uv.lock` and `frontend/package-lock.json`.

## Test and evaluation data

| Data | Source | License | Use |
|---|---|---|---|
| Tengen-Go test photos (3 copied into `tests/fixtures/photos/`, the rest downloaded) | https://github.com/OliverBenz/Tengen-Go @ `ca874803` | AGPL-3.0 | Tests and evaluation; attribution in `tests/fixtures/photos/README.md` |
| goban_data_set | https://github.com/irglbriz/goban_data_set @ `11ec4f90` | MIT | Corner-detection evaluation (downloaded, not committed) |
| kaya-go/moku-v3 dataset (validation and test splits) | https://huggingface.co/datasets/kaya-go/moku-v3 @ `89dd9476` | No license stated; images originate from Roboflow Universe datasets (mostly CC BY 4.0) | Downloaded for local evaluation only and never redistributed. The validation split was used to fit the stone classifier's 12 logistic weights |
| Benchmark games `src/gobot/bench/games/*.sgf` | Generated with KataGo (kata1-b18c384nbt) for this project | Same as this repository | Benchmark positions |

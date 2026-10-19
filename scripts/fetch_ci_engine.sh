#!/usr/bin/env bash
# Linux only (CI and cloud development): download the official KataGo Eigen/AVX2 build and the
# small networks used by the integration tests into data/ and models/. The Mac uses Homebrew.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/katago-linux
ZIP=data/katago-linux/katago.zip
URL=https://github.com/lightvector/KataGo/releases/download/v1.18.1/katago-v1.18.1-eigenavx2-linux-x64.zip
SHA=33e79780dbe3bf6ee859e16f64952cdfc90f7210c8f71ad978ffcba85ad20d79
if [ ! -x data/katago-linux/katago ]; then
  curl -fL --retry 3 -o "$ZIP" "$URL"
  echo "$SHA  $ZIP" | sha256sum -c -
  unzip -o -q "$ZIP" -d data/katago-linux
  rm -f "$ZIP"
fi
data/katago-linux/katago version | head -2
uv run --frozen gobot models download ${@:-b18c384-conv tf2-b10c384}

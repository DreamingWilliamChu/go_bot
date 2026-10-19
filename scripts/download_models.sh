#!/usr/bin/env bash
# Download KataGo networks and the moku-v4 recognition model listed in models/manifest.json.
#   ./scripts/download_models.sh            default set (light + strong default + moku; b18 comes with Homebrew)
#   ./scripts/download_models.sh --max      also the maximum-strength network
#   ./scripts/download_models.sh --all      every network in the manifest
#   ./scripts/download_models.sh tf3-b11c768 b28c512-conv   specific ids
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --frozen gobot models download "$@"

#!/usr/bin/env bash
# Measure KataGo speed and memory on this computer for every installed network and pick the
# default (written to data/engine.json; report in data/benchmark/report.md).
#   ./scripts/benchmark.sh --quick     about 10-20 minutes on an M1 (GPU mode, 2 thread counts)
#   ./scripts/benchmark.sh             full: GPU and GPU+Neural-Engine modes, 5 thread counts (30-90 min)
#   ./scripts/benchmark.sh --models tf3-b10c512 tf2-b10c384 --no-select
# Stop the app first (both use the GPU).
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/_env.sh
exec uv run --frozen gobot bench "$@"

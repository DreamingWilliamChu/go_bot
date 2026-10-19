#!/usr/bin/env bash
# Pull the latest code and refresh dependencies, the web app and any new networks.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
./scripts/setup_mac.sh "$@"

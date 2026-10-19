#!/usr/bin/env bash
# One-time (and repeatable) setup on an Apple Silicon Mac.
#   ./scripts/setup_mac.sh            # install/refresh everything, download default networks
#   ./scripts/setup_mac.sh --max      # also download the maximum-strength network (~262 MB)
#   ./scripts/setup_mac.sh --no-models
# Requires: macOS 14+ on Apple Silicon, Homebrew (https://brew.sh). Uses about 1 GB of disk.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL_ARGS=()
DO_MODELS=1
for a in "$@"; do
  case "$a" in
    --max) MODEL_ARGS+=(--max) ;;
    --all) MODEL_ARGS+=(--all) ;;
    --no-models) DO_MODELS=0 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Checking this Mac"
if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS. For Linux (tests/CI) see scripts/fetch_ci_engine.sh."; exit 1
fi
ARCH=$(uname -m)
MACOS=$(sw_vers -productVersion)
CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)
MEM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "  $CHIP, ${MEM_GB} GB RAM, macOS $MACOS ($ARCH)"
if [ "$ARCH" != "arm64" ]; then
  echo "  WARNING: not Apple Silicon. Homebrew's KataGo will use the CPU (Eigen) backend and be much slower."
fi
if [ "${MACOS%%.*}" -lt 14 ]; then
  echo "  macOS 14 (Sonoma) or newer is required (Homebrew and the onnxruntime/OpenCV wheels need it)."
  echo "  Update via System Settings > General > Software Update, then re-run this script."; exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "  Homebrew is required. Install it from https://brew.sh, then re-run this script."; exit 1
fi

say "Installing KataGo (Metal backend), uv and Node.js with Homebrew"
for pkg in katago uv node; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then echo "  $pkg: $(brew list --versions "$pkg")"; else brew install "$pkg"; fi
done
echo "  $(katago version | head -1); $(katago version | grep -i 'backend' || true)"

say "Python environment (uv sync --frozen, Python from .python-version)"
uv sync --frozen

say "Building the web app"
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build

if [ "$DO_MODELS" = 1 ]; then
  say "Downloading neural networks (size and sha256 verified)"
  uv run gobot models download ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}
fi

say "Detecting hardware"
uv run gobot hardware | tail -1

say "Installed networks"
uv run gobot models list

cat <<'MSG'

Setup complete.
  Recommended once: ./scripts/benchmark.sh --quick   (measures speed on this Mac, picks the default network)
  Start (this Mac only):        ./scripts/start.sh
  Start for your iPhone:        ./scripts/start.sh --lan        (add --https for the live camera view)
Then open http://localhost:8000 on the Mac, or scan the QR code with the iPhone.
MSG

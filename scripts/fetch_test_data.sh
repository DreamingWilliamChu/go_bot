#!/usr/bin/env bash
# Download the recognition evaluation sets into data/testsets/ (git-ignored, never committed).
#   tengen-go        Tengen-Go real 13x13 photos with positions + grid corners   (AGPL-3.0)
#   goban_data_set   494 photos with grid-corner labels                            (MIT)
#   moku-v3          kaya-go/moku-v3 validation + test splits (Roboflow-derived)   (no license stated; eval only)
# Usage: scripts/fetch_test_data.sh [tengen] [goban] [moku]     (default: all)
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=data/testsets
mkdir -p "$DEST"
want=("$@"); [ ${#want[@]} -eq 0 ] && want=(tengen goban moku)

has() { local x; for x in "${want[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }

fetch_git() { # dir url commit
  if [ ! -d "$DEST/$1/.git" ]; then git clone --quiet "$2" "$DEST/$1"; fi
  git -C "$DEST/$1" fetch --quiet origin "$3" 2>/dev/null || true
  git -C "$DEST/$1" checkout --quiet "$3"
  echo "OK  $1 @ $3"
}

fetch_file() { # path url sha256
  local out="$DEST/$1"
  mkdir -p "$(dirname "$out")"
  if [ -f "$out" ] && echo "$3  $out" | shasum -a 256 -c --status 2>/dev/null; then echo "OK  $1 (cached)"; return; fi
  curl -fL --retry 3 -o "$out.part" "$2"
  echo "$3  $out.part" | shasum -a 256 -c --status || { echo "ERR checksum mismatch for $1" >&2; rm -f "$out.part"; exit 1; }
  mv "$out.part" "$out"; echo "OK  $1"
}

has tengen && fetch_git tengen-go https://github.com/OliverBenz/Tengen-Go.git ca8748034f71b558d109d65e2c07466ee8ec66b9
has goban && fetch_git goban_data_set https://github.com/irglbriz/goban_data_set.git 11ec4f90f278e0f2bb0168c9cf3851aeda765ba3
if has moku; then
  REV=89dd9476d6b30b5e39dd2af935a02b3bfe615559
  fetch_file moku-v3/test.parquet "https://huggingface.co/datasets/kaya-go/moku-v3/resolve/$REV/data/test-00000-of-00001.parquet" 23aafd06fca77eaac0995b461eb07a92559d525e756a0ef3bcef226496ff02f9
  fetch_file moku-v3/validation.parquet "https://huggingface.co/datasets/kaya-go/moku-v3/resolve/$REV/data/validation-00000-of-00001.parquet" 20e7aecbc35d71380990013f4b3cf13f467fb90fda7528e08b1d2fad1160533b
fi
echo "Run the evaluation with:  uv run gobot eval"

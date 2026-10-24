#!/usr/bin/env bash
# Start the app.  ./scripts/start.sh [--lan] [--https] [--tunnel] [--port 8000] [--model ID] [--new-token]
#   (no flags)  only this Mac: http://localhost:8000
#   --lan       also phones on the same Wi-Fi (prints a pairing QR code; a token is required)
#   --https     HTTPS with a local certificate authority (needed for the live camera preview)
#   --tunnel    remote access from anywhere through a Cloudflare tunnel (sign-in required)
#               (automatic when config/tunnel.json and this device's token exist; --no-tunnel to skip)
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/_env.sh
if [ ! -f frontend/dist/index.html ]; then
  echo "The web app is not built yet. Run ./scripts/setup_mac.sh first."; exit 1
fi
# Sources changed since the last build: build again, so the server never serves an old app.
if [ -n "$(find frontend/src frontend/index.html frontend/public -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]; then
  echo "The web app changed since it was built; building it again..."
  npm --prefix frontend run build
fi
exec uv run --frozen gobot serve "$@"

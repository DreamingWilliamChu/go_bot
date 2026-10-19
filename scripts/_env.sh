# Sourced by the other scripts: make Homebrew tools (katago, uv, node) findable even if the
# shell profile has not been set up for Homebrew yet.
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
  done
fi
if ! command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
  PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Run ./scripts/setup_mac.sh first (it installs uv with Homebrew)." >&2
  exit 1
fi

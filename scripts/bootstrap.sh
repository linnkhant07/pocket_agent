#!/bin/sh
# One command after clone: install uv if needed, sync, and initialize.
set -eu
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # shellcheck disable=SC1091
  . "$HOME/.local/bin/env" 2>/dev/null || true
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync --extra dev
exec uv run pocket-agent init "$@"

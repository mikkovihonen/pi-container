#!/usr/bin/env bash
# Builds the pi-coding-agent image.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec uv run --project "$SCRIPT_DIR" python "$SCRIPT_DIR/src/build.py" "$@"

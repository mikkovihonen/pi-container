#!/usr/bin/env bash
# Runs the pi-coding-agent container.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# --project points uv at this repo's environment while leaving the working
# directory unchanged, so run.py still mounts the caller's CWD as /workspace.
exec uv run --project "$SCRIPT_DIR" python "$SCRIPT_DIR/src/run.py" "$@"

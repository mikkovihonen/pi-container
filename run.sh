#!/usr/bin/env bash
# Runs the pi-coding-agent container.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/src/run.py" "$@"

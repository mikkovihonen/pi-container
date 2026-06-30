#!/usr/bin/env bash
# Builds the pi-coding-agent image.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/src/build.py" "$@"

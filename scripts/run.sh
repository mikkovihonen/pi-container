#!/usr/bin/env bash
# Runs the pi-coding-agent container.
set -euo pipefail

python3 "$(dirname "$0")/run.py" "$@"

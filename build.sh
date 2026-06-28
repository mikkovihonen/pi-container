#!/usr/bin/env bash
# Builds the pi-coding-agent image for the Apple container.
set -euo pipefail

python3 "$(dirname "$0")/src/build.py" "$@"

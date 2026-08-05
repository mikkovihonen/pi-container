#!/bin/bash
set -euo pipefail

# Root-level commands for this workspace, run at project-image BUILD time.
#
# The CPython 3.14 build, uv, and podman-compose used to live here. They are now
# part of the shared base image, compiled in pi-coding-agent-builder/ and copied in,
# so every workspace gets them and they are built once by build.sh instead of on
# every project-image rebuild.
#
# pi-container's own development needs nothing beyond that — `uv venv` / `uv sync`
# run as the pi user at startup, from .pi-container/dependencies/pi/commands.sh.
# Add system packages here if that changes, e.g.:
#   apt-get update && apt-get install -y --no-install-recommends <package>

echo "Root commands: nothing to do (Python, uv and podman-compose are in the base image)"

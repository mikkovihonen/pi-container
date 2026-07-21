#!/bin/bash
set -e

# Pi-user commands for project-specific setup.
# This script runs as the pi user during container startup, before pi launches.
# Use this for:
#   - Initializing Python venvs: python -m venv .venv && .venv/bin/pip install ...
#   - Cloning repositories: git clone <url>
#   - Workspace-specific configuration
#
# Example:
#   python -m venv .venv && .venv/bin/pip install -r requirements.txt
#   git clone https://github.com/example/repo.git

echo "Pi commands: no-op (project-specific setup not configured)"

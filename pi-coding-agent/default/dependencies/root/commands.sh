#!/bin/bash
set -e

# Root-level commands for project-specific setup.
# This script runs when project specific container image is built.
# Use this for:
#   - Installing system packages: apt-get update && apt-get install -y <package>
#   - Installing npm globals: npm install -g <package>
#   - System configuration that requires root privileges
#
# Example:
#   apt-get update && apt-get install -y ffmpeg
#   npm install -g typescript

echo "Root commands: no-op (project-specific setup not configured)"

#!/bin/bash
set -e

if [ -f /workspace/dependencies/apt/packages.txt ]; then
    echo "dependencies/apt/packages.txt exists in workdir. Installing apt dependencies from workspace."
    {
        apt-get update
        cat /workspace/dependencies/apt/packages.txt | xargs -r apt-get install -y
        rm -rf /var/lib/apt/lists/*
    }  >/dev/null 2>&1
fi

GATEWAY_IP=$(ip route | awk '/default/ {print $3}')
export GATEWAY_IP

exec gosu pi bash -c '
    uv venv --python /usr/local/bin/python3 --no-managed-python "$UV_PROJECT_ENVIRONMENT"
    source /home/pi/.venv/bin/activate
    /home/pi/substitute-models.sh
    exec pi "$@"
' -- "$@"
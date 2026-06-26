#!/bin/bash
set -e

if [ -f /workspace/.pi/dependencies/apt/packages.txt ]; then
    echo "dependencies/apt/packages.txt exists in workdir. Installing apt dependencies from workspace."
    {
        apt-get update
        cat /workspace/.pi/dependencies/apt/packages.txt | xargs -r apt-get install -y
        rm -rf /var/lib/apt/lists/*
    }  >/dev/null 2>&1
fi

export GATEWAY_IP=$(ip route | awk '/default/ {print $3}')

exec gosu pi bash -c '
    socat TCP-LISTEN:9999,fork,reuseaddr TCP:${GATEWAY_IP}:${LLAMA_PORT} &
    uv venv --python /usr/local/bin/python3 --no-managed-python "$UV_PROJECT_ENVIRONMENT"
    source /home/pi/.venv/bin/activate
    exec pi "$@"
' -- "$@"
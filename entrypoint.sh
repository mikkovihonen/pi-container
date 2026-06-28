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
export PARSED_PAIRS=$(echo "$LLAMA_PORTS" | jq -r '.[] | "\(.cp):\(.hp)"')

exec gosu pi bash -c '
    shift 2
    for pair in $PARSED_PAIRS; do
        cp="${pair%%:*}"
        hp="${pair##*:}"
        socat "TCP-LISTEN:${cp},fork,reuseaddr" "TCP:${GATEWAY_IP}:${hp}" &
    done
    {
        uv venv --python /usr/local/bin/python3 --no-managed-python "$UV_PROJECT_ENVIRONMENT"
        source /home/pi/.venv/bin/activate
    } >/dev/null 2>&1
    exec pi "$@"
' -- "$@"
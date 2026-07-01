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

if [ -z "$DEFAULT_ROUTE" ]; then
    echo "ERROR: DEFAULT_ROUTE is not set. The pi-coding-agent container must be started via run.sh."
    exit 1
fi
ip route replace default via $DEFAULT_ROUTE

if [ -n "$HOST_GIT_CONFIG" ]; then
    while IFS=$'\t' read -r key value; do
        if [[ -n "$key" ]]; then
            gosu pi git config --global "$key" "$value"
        fi
    done < <(echo "$HOST_GIT_CONFIG" | jq -r 'to_entries[] | [.key, .value] | @tsv')
fi

exec gosu pi bash -c '
    {
        uv venv --python /usr/local/bin/python3 --no-managed-python --with pip "$UV_PROJECT_ENVIRONMENT"
        source /home/pi/.venv/bin/activate
    } >/dev/null 2>&1
    exec pi "$@"
' -- "$@"

#!/bin/bash
set -e

# Inject the default route to the proxy FIRST — the isolated network has no
# gateway of its own, so without this the container can reach nothing (including
# the apt mirrors below, which egress through the proxy).
if [ -n "$DEFAULT_ROUTE" ]; then
    ip route replace default via $DEFAULT_ROUTE
fi

# ─── IPv6 policy (IPV6_ENABLED, injected by run.py) ───────────────────────
# Off (default): explicitly disable IPv6 so no tool silently tries an AAAA
# record and dead-ends (the proxy stack is IPv4-only), and force apt onto IPv4.
# On: add the IPv6 default route to the proxy (DEFAULT_ROUTE6) and leave apt
# free to use v6.
if [ "${IPV6_ENABLED}" = "true" ]; then
    if [ -n "$DEFAULT_ROUTE6" ]; then
        ip -6 route replace default via "$DEFAULT_ROUTE6" || echo "WARNING: could not set IPv6 default route"
    fi
    rm -f /etc/apt/apt.conf.d/99force-ipv4
else
    # Disable IPv6 so no tool silently tries an AAAA record and dead-ends (the
    # proxy stack is IPv4-only). podman/docker already set this at run time via
    # --sysctl — rootless namespaces mount /proc/sys/net read-only, so the write
    # below fails there (harmlessly); Apple `container` relies on this write.
    # Only warn if IPv6 is STILL enabled afterwards, so the redundant failed
    # write on podman/docker isn't misreported as a problem.
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
    sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
    _v6_all=/proc/sys/net/ipv6/conf/all/disable_ipv6
    if [ -e "$_v6_all" ] && [ "$(cat "$_v6_all" 2>/dev/null)" != "1" ]; then
        echo "WARNING: could not disable IPv6 (all)"
    fi
    printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4
fi

if [ -f /workspace/.pi-container/dependencies/apt/packages.txt ]; then
    echo "dependencies/apt/packages.txt exists in workdir. Installing apt dependencies from workspace."
    {
        apt-get update
        cat /workspace/.pi-container/dependencies/apt/packages.txt | xargs -r apt-get install -y
        rm -rf /var/lib/apt/lists/*
    } >/dev/null 2>&1
fi

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

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
    # proxy stack is IPv4-only). podman already sets this at run time via
    # --sysctl — rootless namespaces mount /proc/sys/net read-only, so the write
    # below fails there (harmlessly);
    # Only warn if IPv6 is STILL enabled afterwards, so the redundant failed
    # write on podman isn't misreported as a problem.
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
    sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
    _v6_all=/proc/sys/net/ipv6/conf/all/disable_ipv6
    if [ -e "$_v6_all" ] && [ "$(cat "$_v6_all" 2>/dev/null)" != "1" ]; then
        echo "WARNING: could not disable IPv6 (all)"
    fi
    printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4
fi

# ─── Home directory ownership (/home/pi is mounted as tmpfs at runtime) ───
chown pi:pi /home/pi
chmod 755 /home/pi

# ─── Shadow directories ownership (tmpfs / named volume mounts) ───────────
# Mount roots (tmpfs, named volumes) are created owned by root:root (0755).
# Chown them to pi:pi so non-root agent processes can write files and directories.
if [ -n "$CONTAINER_CHOWN_PATHS" ]; then
    IFS=':' read -ra _paths <<< "$CONTAINER_CHOWN_PATHS"
    for _path in "${_paths[@]}"; do
        if [ -d "$_path" ]; then
            chown pi:pi "$_path" 2>/dev/null || true
            chmod 755 "$_path" 2>/dev/null || true
        fi
    done
fi

# ─── Nested container support (PI_CONTAINER_NESTED, injected by run.py) ───
# Only the setup that genuinely needs root happens here, before `gosu pi`.
# Rootless podman needs a private XDG_RUNTIME_DIR for its locks and pid files,
# and it must own its own image store: the volume (or tmpfs) mounted at
# ~/.local/share/containers is freshly created and root-owned on the first run.
# The chown is deliberately non-recursive — a populated store already belongs to
# pi, and walking every layer would be slow for nothing.
if [ "${PI_CONTAINER_NESTED}" = "true" ]; then
    mkdir -p /run/user/1000
    chown pi:pi /run/user/1000
    chmod 700 /run/user/1000
    for _dir in /home/pi/.local /home/pi/.local/share /home/pi/.local/share/containers; do
        mkdir -p "$_dir" 2>/dev/null && chown pi:pi "$_dir" 2>/dev/null \
            || echo "WARNING: could not prepare nested image store at $_dir"
    done
fi

if [ -n "$HOST_GIT_CONFIG" ]; then
    while IFS=$'\t' read -r key value; do
        if [[ -n "$key" ]]; then
            gosu pi git config --global "$key" "$value"
        fi
    done < <(echo "$HOST_GIT_CONFIG" | jq -r 'to_entries[] | [.key, .value] | @tsv')
fi

exec gosu pi bash -c '
    set -a; source /etc/environment; set +a;

    if [ -x .pi-container/dependencies/pi/commands.sh ]; then
        .pi-container/dependencies/pi/commands.sh
    fi

    if [ "$PI_CONTAINER_MODE" = "t3" ]; then
        if ! command -v t3 >/dev/null 2>&1; then
            echo "ERROR: t3 CLI is not installed in the container." >&2
            echo "Install it via tools.include: [\"t3\"], npm install -g t3, or .pi-container/dependencies/root/commands.sh." >&2
            exit 1
        fi
        echo "=================================================================="
        echo "  Starting T3 Code server for Pi inside pi-container..."
        echo "  Web UI: https://t3.codes"
        echo "=================================================================="
        _shim=""
        if [ -f /usr/local/share/pi-container/t3-advertise-host.cjs ]; then
            _shim="/usr/local/share/pi-container/t3-advertise-host.cjs"
        elif [ -f /usr/local/lib/t3/t3-advertise-host.cjs ]; then
            _shim="/usr/local/lib/t3/t3-advertise-host.cjs"
        fi
        if [ -n "$_shim" ]; then
            export NODE_OPTIONS="${NODE_OPTIONS:+${NODE_OPTIONS} }--require ${_shim}"
        fi
        export T3_ADVERTISE_HOST="${T3_ADVERTISE_HOST:-localhost}"
        extra_args=()
        if [ "${T3_LAN:-}" = "true" ] || [ "${T3CODE_LAN:-}" = "true" ]; then
            extra_args+=("--lan")
        fi

        # Seed T3 Code settings to enable Pi and disable other agents
        _seed_script=""
        if [ -f /usr/local/share/pi-container/t3-seed-settings.cjs ]; then
            _seed_script="/usr/local/share/pi-container/t3-seed-settings.cjs"
        elif [ -f /usr/local/lib/t3/t3-seed-settings.cjs ]; then
            _seed_script="/usr/local/lib/t3/t3-seed-settings.cjs"
        fi
        if [ -n "$_seed_script" ]; then
            node "$_seed_script" 2>/dev/null || true
        fi

        exec t3 serve --base-dir /home/pi/.pi/agent/t3 --host 0.0.0.0 --port "${T3_PORT:-3773}" "${extra_args[@]}" "$@"
    else
        exec pi "$@"
    fi
' -- "$@"

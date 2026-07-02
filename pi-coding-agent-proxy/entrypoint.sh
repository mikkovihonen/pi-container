#!/bin/bash
set -e

# ─── Validate ADMIN_PASSWORD ─────────────────────────────────────────────────
if [ -z "$ADMIN_PASSWORD" ] || [ "$ADMIN_PASSWORD" = "CHANGEME" ]; then
    echo "ERROR: ADMIN_PASSWORD must be set to a non-default value."
    echo "Update the .env file on the host before running."
    exit 1
fi

# Enable IP forwarding in the container (requires CAP_NET_ADMIN).
# Tolerate failure (e.g. rootless podman where the sysctl is read-only) so the
# container still starts; a genuine forwarding problem surfaces as lost
# connectivity, which is easier to diagnose than an opaque container exit.
sysctl -w net.ipv4.ip_forward=1 || echo "WARNING: could not set net.ipv4.ip_forward=1"

# ─── IPv6 policy (IPV6_ENABLED, forwarded by run.py) ──────────────────────
# Off (default): explicitly tear IPv6 down so no agent traffic can escape
# UNINSPECTED over v6 — the transparent REDIRECT + allowlist below are IPv4
# rules, so a working v6 path would bypass mitmproxy entirely.
# On: enable v6 forwarding and mirror the v4 REDIRECT/NAT/FORWARD rules in
# ip6tables (see the `ipt` helper). VM runtimes also pin these sysctls at
# `run` time via --sysctl, so a failure here (rootless) is tolerated.
if [ "${IPV6_ENABLED}" = "true" ]; then
    echo "[ipv6] IPV6_ENABLED=true — enabling IPv6 forwarding and ip6tables rules"
    sysctl -w net.ipv6.conf.all.forwarding=1 || echo "WARNING: could not enable IPv6 forwarding"
else
    echo "[ipv6] IPV6_ENABLED=false — disabling IPv6"
    # VM runtimes already set this at run time via --sysctl (rootless mounts
    # /proc/sys/net read-only, so the write below fails harmlessly). Only warn
    # if IPv6 is STILL enabled afterwards.
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
    sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
    _v6_all=/proc/sys/net/ipv6/conf/all/disable_ipv6
    if [ -e "$_v6_all" ] && [ "$(cat "$_v6_all" 2>/dev/null)" != "1" ]; then
        echo "WARNING: could not disable IPv6 (all)"
    fi
fi

# Run an (ip6)tables rule against every active address family: always IPv4, and
# IPv6 too when enabled. Used for the family-agnostic REDIRECT/NAT/FORWARD rules
# whose syntax is identical for both (no literal addresses). The llama DNAT
# stays IPv4-only (the host llama-server is reached over v4).
IP_FAMILIES=(iptables)
[ "${IPV6_ENABLED}" = "true" ] && IP_FAMILIES+=(ip6tables)
ipt() { for _fam in "${IP_FAMILIES[@]}"; do "$_fam" "$@"; done; }

# ─── Resolve "llama" hostname to this container's eth1 IP ─────────────────
# mitmproxy's DNS addon reads /etc/hosts by default (dns_use_hosts_file=True),
# so the pi-coding-agent can use http://llama:<cp>/v1 in models.json and
# mitmproxy (running as its DNS server) will resolve "llama" to eth1's IP.
ETH1_IP=$(ip -j -4 addr show eth1 2>/dev/null | jq -r '.[0].addr_info[0].local // empty')
if [ -n "$ETH1_IP" ]; then
    echo "$ETH1_IP  llama" >> /etc/hosts
    echo "[hosts] llama → $ETH1_IP"
else
    echo "WARNING: Could not determine eth1 IP; 'llama' hostname will not resolve."
fi

# Redirect HTTP (80) to mitmproxy (8080)
ipt -t nat -A PREROUTING -i eth1 -p tcp --dport 80 -j REDIRECT --to-port 8080

# Redirect HTTPS (443) to mitmproxy (8080)
ipt -t nat -A PREROUTING -i eth1 -p tcp --dport 443 -j REDIRECT --to-port 8080

# Redirect DNS from isolated-net to mitmproxy's unprivileged DNS port
ipt -t nat -A PREROUTING -i eth1 -p udp --dport 53 -j REDIRECT --to-port 5353
ipt -t nat -A PREROUTING -i eth1 -p tcp --dport 53 -j REDIRECT --to-port 5353

# ─── Llama-server port forwarding (isolated-net → host) ───────────────────
# The pi-coding-agent resolves "llama" via this container's mitmproxy DNS
# (hosts-file entry above) to eth1's IP, then hits http://llama:<cp>/v1.
# DNAT redirects that traffic out eth0 to wherever the host llama-server is
# reachable:
#   * LLAMA_HOST_ADDR set (podman/docker): the host loopback is reached via
#     host.containers.internal / host.docker.internal (gvproxy). Resolved to an
#     IP here because iptables DNAT requires a numeric destination.
#   * LLAMA_HOST_ADDR unset (Apple container): fall back to the default gateway,
#     which is the host bridge IP where a host-side socat exposes llama-server.
if [ -n "$LLAMA_PORTS" ]; then
    if [ -n "$LLAMA_HOST_ADDR" ]; then
        if echo "$LLAMA_HOST_ADDR" | grep -qE '^[0-9]+(\.[0-9]+){3}$'; then
            LLAMA_TARGET="$LLAMA_HOST_ADDR"
        else
            LLAMA_TARGET=$(getent hosts "$LLAMA_HOST_ADDR" | awk '{print $1}' | head -1)
        fi
    else
        LLAMA_TARGET=$(ip route show default 2>/dev/null | grep -oP 'default via \K[\d.]+' | head -1)
    fi

    if [ -z "$LLAMA_TARGET" ]; then
        echo "ERROR: Could not determine llama-server target address for DNAT (LLAMA_HOST_ADDR='$LLAMA_HOST_ADDR')."
        exit 1
    fi

    echo "$LLAMA_PORTS" | jq -r '.[] | "\(.cp):\(.hp)"' | while IFS=: read -r cp hp; do
        [ -z "$cp" ] || [ -z "$hp" ] && continue
        iptables -t nat -A PREROUTING -i eth1 -p tcp --dport "$cp" -j DNAT --to-destination "${LLAMA_TARGET}:${hp}"
        # Permit the DNAT'd model traffic through the (default-deny) FORWARD chain.
        iptables -A FORWARD -i eth1 -o eth0 -p tcp -d "$LLAMA_TARGET" --dport "$hp" -j ACCEPT
        echo "[llama-dnat] eth1:$cp → $LLAMA_TARGET:$hp"
    done
fi

# ─── Forwarding policy: default-deny, opt-in per protocol ─────────────────
# HTTP(80)/HTTPS(443)/DNS(53) from the agent are REDIRECTed to mitmproxy above
# and egress via mitmproxy's own connections, so they never traverse the FORWARD
# chain and stay inspected. Any OTHER protocol the agent emits would otherwise be
# forwarded straight to the internet UNINSPECTED, bypassing mitmproxy and the
# allowlist. So the FORWARD chain defaults to DROP and operators opt specific
# protocols in via PROXY_ALLOW_* env vars (see .env). Traffic allowed this way is
# NOT inspected by mitmproxy — it is plain NAT forwarding.
ipt -t nat -A POSTROUTING -o eth0 -j MASQUERADE
ipt -P FORWARD DROP
ipt -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

_truthy() { case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in true|1|yes|on) return 0;; *) return 1;; esac; }
_allow_fwd() {  # $1=proto  $2=comma-separated dports  $3=label
    ipt -A FORWARD -i eth1 -o eth0 -p "$1" -m multiport --dports "$2" -j ACCEPT
    echo "[forward-allow] $3 → $1/$2 (UNINSPECTED)"
}

_truthy "$PROXY_ALLOW_SSH"  && _allow_fwd tcp 22         "SSH"
_truthy "$PROXY_ALLOW_SMTP" && _allow_fwd tcp 25,465,587 "SMTP"
_truthy "$PROXY_ALLOW_GIT"  && _allow_fwd tcp 9418       "git-protocol"
_truthy "$PROXY_ALLOW_NTP"  && _allow_fwd udp 123        "NTP"
[ -n "$PROXY_ALLOW_TCP_PORTS" ] && _allow_fwd tcp "$PROXY_ALLOW_TCP_PORTS" "custom-tcp"
[ -n "$PROXY_ALLOW_UDP_PORTS" ] && _allow_fwd udp "$PROXY_ALLOW_UDP_PORTS" "custom-udp"

# ─── mitmproxy addon config paths ─────────────────────────────────────────
# The addon scripts read these at import time to locate their YAML configs
# (baked defaults, overridden by the host configs run.py mounts here).
export ALLOWLIST_CONFIG_PATH="${ALLOWLIST_CONFIG_PATH:-/home/mitmproxy/config/allowlist.yaml}"
export TOKEN_REPLACER_CONFIG_PATH="${TOKEN_REPLACER_CONFIG_PATH:-/home/mitmproxy/config/token_replacer.yaml}"

# Execute the CMD as mitmproxy user. Load the allowlist (host/IP filtering) and
# token_replacer (secret redaction) addons.
exec gosu mitmproxy bash -c '
    mitmweb --mode transparent@8080 --mode dns@5353 --web-host 0.0.0.0 \
        -s /home/mitmproxy/scripts/allowlist.py \
        -s /home/mitmproxy/scripts/token_replacer.py \
        --set web_password=$ADMIN_PASSWORD
' -- "$@"

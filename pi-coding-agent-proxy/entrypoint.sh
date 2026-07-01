#!/bin/bash
set -e

# ─── Validate ADMIN_PASSWORD ─────────────────────────────────────────────────
if [ -z "$ADMIN_PASSWORD" ] || [ "$ADMIN_PASSWORD" = "CHANGEME" ]; then
    echo "ERROR: ADMIN_PASSWORD must be set to a non-default value."
    echo "Update the .env file on the host before running."
    exit 1
fi

# Enable IP forwarding in the container
# This requires CAP_NET_ADMIN
sysctl -w net.ipv4.ip_forward=1

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
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 80 -j REDIRECT --to-port 8080

# Redirect HTTPS (443) to mitmproxy (8080)
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 443 -j REDIRECT --to-port 8080

# Redirect DNS from isolated-net to mitmproxy's unprivileged DNS port
iptables -t nat -A PREROUTING -i eth1 -p udp --dport 53 -j REDIRECT --to-port 5353
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 53 -j REDIRECT --to-port 5353

# ─── Llama-server port forwarding (isolated-net → bridge) ─────────────────
# The pi-coding-agent resolves "llama" via this container's mitmproxy DNS
# (hosts-file entry below) to eth1's IP, then hits http://llama:<cp>/v1.
# DNAT redirects that traffic through eth0 to the host-side socat on
# the bridge interface where llama-server is exposed.
if [ -n "$LLAMA_PORTS" ]; then
    GATEWAY_IP=$(ip route show default 2>/dev/null | grep -oP 'default via \K[\d.]+' | head -1)
    if [ -z "$GATEWAY_IP" ]; then
        echo "ERROR: Could not determine default gateway IP for llama-server DNAT."
        exit 1
    fi
    echo "$LLAMA_PORTS" | jq -r '.[] | "\(.cp):\(.hp)"' | while IFS=: read -r cp hp; do
        [ -z "$cp" ] || [ -z "$hp" ] && continue
        iptables -t nat -A PREROUTING -i eth1 -p tcp --dport "$cp" -j DNAT --to-destination "${GATEWAY_IP}:${hp}"
        echo "[llama-dnat] eth1:$cp → $GATEWAY_IP:$hp"
    done
fi

# NAT everything else going out eth0 (internet side)
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
iptables -A FORWARD -j ACCEPT

# Execute the CMD as mitmproxy user
exec gosu mitmproxy bash -c '
    mitmweb --mode transparent@8080 --mode dns@5353 --web-host 0.0.0.0 --set web_password=$ADMIN_PASSWORD
' -- "$@"

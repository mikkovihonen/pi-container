#!/bin/bash
set -e

# Enable IP forwarding in the container
# This requires CAP_NET_ADMIN
sysctl -w net.ipv4.ip_forward=1

# Flush iptables
iptables -t nat -F
iptables -t mangle -F

# Redirect HTTP (80) to mitmproxy (8080)
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 80 -j REDIRECT --to-port 8080

# Redirect HTTPS (443) to mitmproxy (8080)
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 443 -j REDIRECT --to-port 8080

# NAT everything else going out eth0 (internet side)
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
iptables -A FORWARD -j ACCEPT

# Start caddy in the background
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &

# Execute the CMD as mitmproxy user
exec gosu mitmproxy "$@"

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

# Redirect DNS from isolated-net to mitmproxy's unprivileged DNS port
iptables -t nat -A PREROUTING -i eth1 -p udp --dport 53 -j REDIRECT --to-port 5353
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 53 -j REDIRECT --to-port 5353

# NAT everything else going out eth0 (internet side)
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
iptables -A FORWARD -j ACCEPT

# Execute the CMD as mitmproxy user
exec gosu mitmproxy bash -c '
    mitmweb --mode transparent@8080 --mode dns@5353 --web-host 0.0.0.0 --set web_password=$ADMIN_PASSWORD
' -- "$@"

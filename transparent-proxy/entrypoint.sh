#!/bin/bash
set -e

# 1. Start gost in the background (SOCKS5 server)
# Using a stable version link for gost
gost -L socks5://:1080 &

# 2. Start redsocks in the background
redsocks -f /etc/redsocks.conf &

# 3. Configure iptables
# Clear existing rules to avoid conflicts during restarts
iptables -t nat -F
iptables -t nat -X

# Create a new chain for our redirection
iptables -t nat -N REDIRECT_TO_REDSOCKS

# Avoid redirecting local traffic (to the proxy itself or other local services)
iptables -t nat -A REDIRECT_TO_REDSOCKS -d 127.0.0.1 -j RETURN
iptables -t nat -A REDIRECT_TO_REDSOCKS -d 10.0.0.0/8 -j RETURN
iptables -t nat -A REDIRECT_TO_REDSOCKS -d 172.16.0.0/12 -j RETURN
iptables -t nat -A REDIRECT_TO_REDSOCKS -d 192.168.0.0/16 -j RETURN

# Redirect all other TCP traffic to redsocks port
iptables -t nat -A REDIRECT_TO_REDSOCKS -p tcp -j REDIRECT --to-ports 12345

# Apply the chain to the OUTPUT chain
iptables -t nat -A OUTPUT -p tcp -j REDIRECT_TO_REDSOCKS

echo "-------------------------------------------------------"
echo "🚀 Transparent Gateway is running!"
echo "-------------------------------------------------------"
echo "1. SOCKS5 Proxy: localhost:1080"
echo "2. All container outbound TCP traffic is redirected."
echo "-------------------------------------------------------"

# Wait for the processes to stay alive
wait

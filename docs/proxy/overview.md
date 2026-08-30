# Pi Coding Agent Proxy container

- A Debian-based router container that transparently intercepts the agent's HTTP/HTTPS/DNS traffic through mitmproxy.
- mitmproxy generates a self-signed certificate with its certificate authority on first run.
- mitmweb provides a web UI (port 8081) for monitoring traffic.
- It runs [`allowlist`](allowlist.md), [`token_replacer`](token-replacer.md), and [`flow_export`](flow-export.md) addons on the intercepted traffic (host filtering + secret redaction + session flow export).
- It forwards the isolated network's traffic to the Internet. Non-HTTP protocols are denied by default (fail-closed, opt-in via `PROXY_ALLOW_*`).

## Building the proxy container image

The system builds the proxy container by running `build.sh` in the project root. The build runs before other containers because other containers refer to it in their own build files.

The transparent proxy container's Containerfile definition at `pi-coding-agent-proxy/Containerfile` runs mitmproxy. mitmproxy generates the keys for its certificate authority (CA) in the config directory (~/.mitmproxy by default).

``` Containerfile
USER mitmproxy
WORKDIR /home/mitmproxy
RUN timeout 3s mitmweb || [ $? -eq 124 ]
```

## Running the container

The container uses additional capabilities to manage network interfaces and routing tables:
- `CAP_NET_ADMIN`

The [entrypoint](https://github.com/mikkovihonen/pi-container/blob/main/pi-coding-agent-proxy/entrypoint.sh) uses `iptables` on the isolated-net interface
(`eth1`) to transparently intercept the agent's traffic. HTTP, HTTPS, and DNS are
redirected into mitmproxy (running transparent + DNS modes). The local
`llama-server` API is DNAT'd out to the host. A `POSTROUTING -j MASQUERADE`
rule handles NAT on `eth0` for the egress interface. Everything else is denied
by default on the `FORWARD` chain. Operators can opt in extra protocols via
`PROXY_ALLOW_*` env vars (see [Egress policy](#egress-policy-default-deny)).

```bash
# Redirect HTTP/HTTPS into mitmproxy (transparent proxy on 8080)
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 80  -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 443 -j REDIRECT --to-port 8080

# Redirect DNS into mitmproxy's DNS mode (5353); the proxy resolves "llama" to
# itself and forwards other lookups upstream
iptables -t nat -A PREROUTING -i eth1 -p udp --dport 53 -j REDIRECT --to-port 5353
iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 53 -j REDIRECT --to-port 5353
```

### Egress policy (default-deny)

mitmproxy intercepts and inspects only HTTP/HTTPS/DNS. Any other
protocol the agent emits goes straight to the internet
**uninspected** otherwise. The `FORWARD` chain defaults to `DROP`. The model API
(DNAT'd to the host) is explicitly permitted. Operators can opt specific
extra protocols in via `PROXY_ALLOW_*` env vars (`PROXY_ALLOW_SSH`,
`PROXY_ALLOW_SMTP`, `PROXY_ALLOW_GIT`, `PROXY_ALLOW_NTP`, `PROXY_ALLOW_TCP_PORTS`,
`PROXY_ALLOW_UDP_PORTS`). See [Proxy egress policy](../design-details.md#proxy-egress-policy). **Traffic
allowed this way is plain NAT and is NOT seen by mitmproxy or the allowlist.**

## Addons

mitmproxy loads three addons (baked into the image, loaded via `-s` in the
entrypoint). The addons operate on the intercepted HTTP/HTTPS traffic:

| Addon | Purpose | Config (host → container) |
|-------|---------|---------------------------|
| [`allowlist`](allowlist.md) | Blocks requests to non-allowlisted hosts/IPs (default action `block`). | `.pi-container/allowlist.yaml` → `/home/mitmproxy/config/allowlist.yaml` |
| [`token_replacer`](token-replacer.md) | Redacts secrets (API keys, Bearer tokens, cookies, JWTs) from requests/responses. | `.pi-container/token_replacer.yaml` → `/home/mitmproxy/config/token_replacer.yaml` |
| [`flow_export`](flow-export.md) | Exports completed flows (JSON Lines) to per-client-IP files for post-session inspection. | N/A (baked in, writes to `/home/mitmproxy/exports/`) |

The image bakes fail-closed default configs. `run.py` mounts the host configs
from `.pi-container/` over them at runtime (and injects any `${ENV:VAR}` secrets
the token_replacer config references). Edit the host files to change policy. See
[addon development guide](addon-development.md) for how mitmproxy addons work.

## Installing the mitmproxy CA certificate to Pi Coding Agent

The mitmproxy CA cert is located in `/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem` in a local container image tagged with the `PROXY_IMAGE_TAG` .env variable (default: `pi-coding-agent-proxy:local`).

Copy `mitmproxy-ca-cert.crt` from the transparent proxy container to install it into the pi-coding-agent container during build:

In `pi-coding-agent/Containerfile`, `mitmproxy-ca-cert.pem` is copied from the transparent proxy container image, converted to .cer format, and installed:

``` Containerfile
COPY --from=pi-coding-agent-proxy:local /home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.pem
```

Then:

- A directory for extra CA certificates is created
    - `RUN mkdir /usr/local/share/ca-certificates/extra`
- PEM format `mitmproxy-ca.pem` is converted into CRT format `mitmproxy-ca.crt`
    - `RUN openssl x509 -in /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.pem -inform PEM -out /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.crt`
- System CA certificates are updated
    - `RUN update-ca-certificates`

After this, the pi-coding-agent image has the mitmproxy CA installed.

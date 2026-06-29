# Transparent proxy container

- Debian based router container which routes all traffic via mitmproxy
- mitmproxy generates a self-signed certificate with its certificate authority first time it's run
- mitmweb provides a web UI for monitoring traffic
- routes traffic from other containers to Internet via mitmproxy

## Building transparent proxy container image

Proxy container is built by running `build.sh` in project root. It is built before other containers as they refer to it in their own build files.

Transparent proxy container's Containerfile definition at `pi-coding-agent-proxy/Containerfile` runs mitmproxy in order to generate the keys for its certificate authority (CA) in the config directory (~/.mitmproxy by default).

``` Containerfile
USER mitmproxy
WORKDIR /home/mitmproxy
RUN timeout 3s mitmweb || [ $? -eq 124 ]
```

## Running the container

To use the transparent proxy, the container must be run with additional capabilities to allow it to manage network interfaces and routing tables:
- `CAP_NET_ADMIN`

The container uses `iptables` to redirect incoming traffic to `mitmproxy` (default port 8080):

```bash
# Redirect HTTP traffic to mitmproxy
iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080

# Redirect HTTPS traffic to mitmproxy
iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to-port 8080
```

## Installing the mitmproxy CA certificate to Pi Coding Agent

The mitmproxy CA cert is located in `/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem` in a local container image tagged with `PROXY_IMAGE_TAG` .env variable (default: `pi-coding-agent-proxy:local`).

Copy `mitmproxy-ca-cert.crt` from the transparent proxy container to be installed into pi-coding-agent container when building it:

In `pi-coding-agent/Containerfile`, `mitmproxy-ca-cert.pem` is copied from transparent proxy container image, covert it into .cer format and installed:

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

After this, pi-coding-agent image has mitmproxy CA installed

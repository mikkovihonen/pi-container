# Allowlist Addon

Filters HTTP traffic so that only requests to allowlisted domains and IP address ranges pass through the proxy. All other connections are blocked (HTTP 403 by default, or connection closed via status 444).

This addon also works in reverse: in `block` mode, only blocked hosts/IPs are denied and everything else is allowed.

## Features

- **Domain allowlisting** — allow or block specific hostnames using glob wildcards or full regex patterns
- **IP range allowlisting** — allow or block IP addresses and CIDR ranges (IPv4 and IPv6)
- **Dual mode** — `allow` (whitelist) and `block` (blacklist)
- **mitmweb visual indicators** — blocked flows show a 🚫 marker and comment in the flow list
- **Configurable status code** — return 403, or close the connection with 444
- **Dry-run mode** — log blocked requests without actually blocking them
- **Localhost always allowed** — loopback and private IP connections bypass all rules

## Quick Start

> **In this project the allowlist is already wired in and active.** The
> `pi-coding-agent-proxy` image bakes the script and a fail-closed default config,
> and `run.py` mounts the host's [`.pi-container/allowlist.yaml`](https://github.com/mikkovihonen/pi-container/blob/main/.pi-container/allowlist.yaml)
> over it at runtime. Edit that host file to change the policy — you don't need
> the manual steps below unless you're wiring the addon into a different proxy.

### In this project

The addon is loaded as part of the mitmproxy startup in the [entrypoint](https://github.com/mikkovihonen/pi-container/blob/main/pi-coding-agent-proxy/entrypoint.sh), alongside the `token_replacer` and `flow_export` addons:

```bash
uv run mitmweb --mode transparent@8080 --mode dns@5353 --web-host 0.0.0.0 \
    -s /home/mitmproxy/scripts/allowlist.py \
    -s /home/mitmproxy/scripts/token_replacer.py \
    -s /home/mitmproxy/scripts/flow_export.py \
    --set web_password=$ADMIN_PASSWORD
```

The config file path is set via the `ALLOWLIST_CONFIG_PATH` environment
variable, which points to the mounted host config at
`/home/mitmproxy/config/allowlist.yaml`.

### Standalone usage

Load the addon in mitmproxy/mitmweb (`-s` is the short form of `--set scripts=`,
and can be repeated for multiple addons):

```bash
mitmweb -s allowlist.py
```

Or in Docker — see the [Containerfile](https://github.com/mikkovihonen/pi-container/blob/main/pi-coding-agent-proxy/Containerfile):

```dockerfile
COPY addons/allowlist/allowlist.py /home/mitmproxy/scripts/allowlist.py
COPY addons/allowlist/allowlist_config.yaml /home/mitmproxy/config/allowlist.yaml
ENV ALLOWLIST_CONFIG_PATH=/home/mitmproxy/config/allowlist.yaml
```

Then in the entrypoint:

```bash
mitmweb --mode transparent@8080 -s /home/mitmproxy/scripts/allowlist.py
```

## Configuration

The addon reads from a YAML config file (`allowlist_config.yaml`) and supports runtime overrides via `--set` flags.

### Config File Structure

```yaml
# Only permit Python package servers (pypi.org / files.pythonhosted.org),
# npm package registries, and Debian/Ubuntu apt infrastructure. All other
# traffic is blocked by default.
global:
  mode: "allow"                                 # "allow" or "block"
  default_action: "block"                       # what to do when no rule matches
  status_code: 403                              # HTTP status for blocked requests (444 = close connection)
  log_blocked: true                             # log blocked requests
  log_allowed: false                            # log allowed requests (useful for auditing)
  dry_run: false                                # log matches without blocking
  # Named rules
  rules:
    - name: "pypi-org-allow"
      mode: "allow"
      hostnames:
        - "pypi.org"
        - "*.pypi.org"
      ip_ranges: []

    - name: "pythonhosted-org-allow"
      mode: "allow"
      hostnames:
        - "files.pythonhosted.org"
        - "*.files.pythonhosted.org"
      ip_ranges: []

    - name: "npm-registry-allow"
      mode: "allow"
      hostnames:
        - "registry.npmjs.org"
        - "*.registry.npmjs.org"
        - "*.npmjs.org"
      ip_ranges: []

    - name: "github-allow"
      mode: "allow"
      hostnames:
        - "github.com"
        - "api.github.com"
        - "codeload.github.com"
        - "objects.githubusercontent.com"
        - "collector.github.com"
        - "*.github.com"
        - "*.githubassets.com"
        - "*.githubusercontent.com"
      ip_ranges: []

    - name: "yarn-registry-allow"
      mode: "allow"
      hostnames:
        - "registry.yarnpkg.com"
        - "*.yarnpkg.com"
      ip_ranges: []

    - name: "debian-apt-allow"
      mode: "allow"
      hostnames:
        - "debian.map.fastlydns.net"
        - "deb.debian.org"
        - "*.deb.debian.org"
        - "security.debian.org"
        - "*.security.debian.org"
        - "packages.debian.org"
        - "*.packages.debian.org"
      ip_ranges: []
```

### Flat vs. Named Rules

The addon supports two mutually exclusive configuration styles, determined by what is present in the config:

| Style | Trigger | Behaviour |
|-------|---------|-----------|
| **Flat allowlist** | `global.rules` is absent or empty, and `global.hostnames`/`global.ip_ranges` are defined | A single implicit rule is created from the flat lists. |
| **Named rules** | `global.rules` contains one or more rule objects | Each rule is evaluated in order; the first match wins. |
| **Both present** | `global.rules` is non-empty **and** `global.hostnames`/`global.ip_ranges` are also defined | Named rules take full priority; the flat lists are silently ignored. |

**Rule**: always define one style or the other — never both. Mixing them will
cause the flat lists to be dropped without any warning.

## Pattern Syntax

### Hostname Patterns

| Type | Example | Matches |
|------|---------|---------|
| Exact | `api.example.com` | Only `api.example.com` |
| Glob wildcard | `*.example.com` | `api.example.com`, `staging.example.com` |
| Regex | `^auth\..*\.example\.com$` | `auth.v1.example.com`, `auth.v2.example.com` |
| Mixed | `api*.internal.local` | `api.internal.local`, `api-v2.internal.local` |

Patterns are **case-insensitive**. Glob wildcards use `*` (any number of chars) and `?` (exactly one char). Patterns containing regex metacharacters (`^ $ + { } [ ] ( ) |`) are treated as full regex.

### IP Patterns

| Type | Example | Matches |
|------|---------|---------|
| Single IP | `192.168.1.1` | Only that IP |
| CIDR range | `10.0.0.0/8` | `10.0.0.0` – `10.255.255.255` |
| IPv6 CIDR | `fd00::/8` | All IPv6 ULA addresses |
| Single IPv6 | `::1` | Loopback only |

### Matching Semantics

Within a single rule, hostname and IP matching use **OR logic**:

- **Allow mode**: request is allowed if hostname matches **OR** IP matches
- **Block mode**: request is denied if hostname matches **OR** IP matches

Between rules, evaluation is **first-match-wins**:

1. Rule 1 evaluated → if match, apply its action and stop
2. Rule 2 evaluated → if match, apply its action and stop
3. ... continue until a match or all rules exhausted
4. If no rule matches, apply `default_action`

## mitmweb Visual Indicators

Blocked flows are visually distinct in the mitmweb UI:

| Indicator | What it shows |
|-----------|---------------|
| 🚫 Flow marker | Appears next to blocked flows in the flow list |
| 💬 Comment | Shows "Blocked by allowlist" with the reason in the flow detail |
| Red status code | 403 (or configured code) shown in red in the flow list |

### Filtering Blocked Flows

Type `@marked` in the mitmweb search bar to show only blocked flows:

```
@marked
```

Or use the command:

```
:view.properties.marked.toggle
```

### Example Flow List

```
┌──────────────────────────────────────────────────────────────┐
│ 🚫 evil.com        GET /login     403  Blocked by allowlist. │
│     api.example.com GET /api/data  200                       │
│ 🚫 tracker.ads.com GET /pixel     403  Blocked by allowlist  │
│     google.com     GET /search    200                        │
└──────────────────────────────────────────────────────────────┘
```

## Runtime Options

Override config file settings via `--set` flags:

```bash
mitmweb --set scripts=allowlist.py \
        --set allowlist_mode=block \
        --set allowlist_default_action=allow \
        --set allowlist_status_code=444 \
        --set allowlist_log_blocked=true \
        --set allowlist_log_allowed=false
```

| Option | Values | Default | Description |
|--------|--------|---------|-------------|
| `allowlist_mode` | `allow`, `block` | `allow` | Operating mode |
| `allowlist_default_action` | `allow`, `block` | `block` | Action for unmatched requests |
| `allowlist_status_code` | `403`, `444`, etc. | `403` | HTTP status for blocked requests |
| `allowlist_log_blocked` | `true`, `false` | `true` | Log blocked requests |
| `allowlist_log_allowed` | `true`, `false` | `false` | Log allowed requests |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWLIST_CONFIG_PATH` | `allowlist_config.yaml` (same directory as the script) | Path to config file. In the `pi-coding-agent-proxy` image this is set to `/home/mitmproxy/config/allowlist.yaml`, which `run.py` mounts from the host's `.pi-container/allowlist.yaml`. |

> **Note:** the allowlist only governs HTTP/HTTPS traffic that mitmproxy
> intercepts. Non-HTTP protocols are handled separately by the proxy's `FORWARD`
> policy (default-deny, opt-in via `PROXY_ALLOW_*` — see the project README), not
> by this addon.

## Examples

### Example 1: Simple internal-only proxy

Only allow traffic to internal domains and private IPs:

```yaml
global:
  mode: "allow"
  hostnames:
    - "api.internal.local"
    - "*.internal.local"
    - "services.local"
  ip_ranges:
    - "10.0.0.0/8"
    - "172.16.0.0/12"
    - "192.168.0.0/16"
```

### Example 2: Flat allowlist (production-style)

A flat allowlist for a development environment that permits Python, npm, Debian
apt, and GitHub traffic without named rules:

```yaml
global:
  mode: "allow"
  default_action: "block"
  status_code: 403
  log_blocked: true
  hostnames:
    # Python packaging
    - "pypi.org"
    - "*.pypi.org"
    - "files.pythonhosted.org"
    - "*.files.pythonhosted.org"
    - "registry.yarnpkg.com"
    - "*.yarnpkg.com"
    # npm
    - "registry.npmjs.org"
    - "*.registry.npmjs.org"
    - "*.npmjs.org"
    # Debian/Ubuntu
    - "debian.map.fastlydns.net"
    - "deb.debian.org"
    - "*.deb.debian.org"
    - "security.debian.org"
    - "*.security.debian.org"
    - "packages.debian.org"
    - "*.packages.debian.org"
    # GitHub
    - "github.com"
    - "api.github.com"
    - "codeload.github.com"
    - "objects.githubusercontent.com"
    - "*.github.com"
    - "*.githubassets.com"
    - "*.githubusercontent.com"
  ip_ranges:
    - "10.0.0.0/8"
    - "172.16.0.0/12"
    - "192.168.0.0/16"
```

This creates a single implicit allowlist rule covering all the listed hosts and
IP ranges. Use flat style when you only need one rule; switch to named rules when
you need per-rule modes, names, or selective blocking.

### Example 3: Kill connection instead of 403

Close the TCP connection immediately for blocked requests (no HTTP response sent):

```yaml
global:
  mode: "allow"
  status_code: 444
  hostnames:
    - "api.example.com"
    - "*.internal.local"
```

### Example 4: Dry-run mode

Log what would be blocked without actually blocking:

```yaml
global:
  mode: "allow"
  dry_run: true
  hostnames:
    - "api.example.com"
    - "*.internal.local"
```

### Example 5: Block specific IP ranges

Block traffic to/from known-bad IP ranges while allowing everything else:

```yaml
global:
  mode: "block"
  default_action: "allow"
  rules:
    - name: "block-bad-ips"
      mode: "block"
      ip_ranges:
        - "203.0.113.0/24"
        - "198.51.100.0/24"
        - "45.33.32.0/24"
```

## Always-Allowed Connections

The following are **always allowed** regardless of rules:

- **Loopback hosts**: `localhost`, `127.0.0.1`, `::1`
- **Private IPs**: RFC 1918 ranges (`10.x`, `172.16.x`, `192.168.x`), link-local (`169.254.x`), reserved addresses

This ensures that local services and the proxy's own connections are never blocked.

## How It Works

1. **`request(flow)`** hook fires for every HTTP request
2. Hostname (`flow.request.pretty_host` — the Host header / SNI, which is correct under transparent proxying; `flow.request.host` would be the destination IP) and server IP are extracted from the flow
3. If localhost/private → allowed immediately
4. Rules are evaluated in order; first match determines action
5. If no rule matches → `default_action` is applied
6. Blocked flows get `flow.marked = ":no_entry_sign:"` and `flow.comment` set for mitmweb visibility
7. Flow is either returned a 403 response or killed (status 444)

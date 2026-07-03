# Configuration

[← Documentation index](../README.md) · [Getting Started](getting-started.md) · [Architecture](architecture.md) · [Development](development.md)

## Environment Configuration

The application uses a `.env` file for managing environment-specific settings. See `.env.example` for all available options.

### Security

- **`ADMIN_PASSWORD`** MUST be changed from the default `CHANGEME` before running.
  The proxy's mitmweb UI will refuse to start with a default or empty password.
- **Model integrity**: Set `sha256` in `models.json` to verify downloaded model files.
  Without a checksum, downloads proceed without integrity verification.

### Run Configuration

The following environment variables are used by `build.sh` and `run.sh` to configure the container runtime, proxy, and `llama-server`:

| Variable | Description | Default |
|----------|-------------|---------|
| `PI_IMAGE_TAG` | The tag of the pi container image to run | `pi-coding-agent:local` |
| `PROXY_IMAGE_TAG` | The tag of the proxy container image to run | `pi-coding-agent-proxy:local` |
| `LLAMA_BIN` | Path to the `llama-server` executable | `llama-server` or `/opt/homebrew/bin/llama-server` |
| `BRIDGE_INTERFACE` | Host bridge interface for the `socat` bridge (Apple `container` only) | Per-runtime: `bridge100` / `podman0` / `docker0` |
| `PROXY_UPSTREAM_NETWORK` | The upstream network the proxy connects to for internet access | Per-runtime: `default` / `podman` / `bridge` |
| `LOG_LEVEL` | Log level | `INFO` |
| `ADMIN_PASSWORD` | Password for mitmproxy Web UI | `CHANGEME` |
| `CONTAINER_RUNTIME` | Container CLI to use (`container`, `docker`, or `podman`) | Auto-detected (prefers `container` > `docker` > `podman`) |

`BRIDGE_INTERFACE` and `PROXY_UPSTREAM_NETWORK` are derived from `CONTAINER_RUNTIME` and rarely need setting; provide them only to override the per-runtime default for your host.

> Per-project settings (IPv6, proxy DNS, mitmweb UI exposure, llama-server startup tuning, resource limits, tmpfs, flow export, egress, extra agent env/mounts) are **not** environment variables — they live in `.pi-container/config.yaml`, documented below.

## Per-workspace Configuration

### Introduction

When launched, pi-container looks for workspace-specific overrides in `./.pi-container` and package dependencies in the directory it's launched in. Each workspace gets its own agent config, proxy, isolated network, and chat templates — all under that workspace's `.pi-container/` (seeded from the `pi-coding-agent/default/` template on first run).

Orchestration settings live in a single **`config.yaml`**; the proxy addon configs (`allowlist.yaml`, `token_replacer.yaml`) stay in their own files because they're mounted into and parsed by the proxy container.

### The `config.yaml` file

`.pi-container/config.yaml` is the single source of truth for this workspace's orchestration knobs:

```yaml
# .pi-container/config.yaml
resources:
  agent: { memory: 16g, cpus: 8 }
  proxy: { memory: 4g, cpus: 4 }
llama:
  startup_timeout: 180        # seconds to wait for /health per attempt
  startup_attempts: 2         # relaunches before giving up
network:
  ipv6: false                 # plumb IPv6 through the isolated net + proxy
  dns: "1.1.1.1"              # upstream resolver the proxy uses
proxy:
  expose_ui: localhost        # mitmweb UI bind: localhost | lan
agent:
  env: {}                     # extra --env vars for the agent container
  mounts: []                  # extra bind mounts (absolute host paths)
tmpfs:
  paths: []
flow_export:
  enabled: false
egress:
  allow: { ssh: false, smtp: false, git: false, ntp: false, tcp_ports: [], udp_ports: [] }
```

Any missing section falls back to a safe default (values above; egress → deny-all; flow_export → off). Each subsection is documented below.

### Resource limits

`resources.agent` and `resources.proxy` set CPU/memory caps on the two containers this workspace launches (`--memory` / `--cpus`). A `null` (or omitted) value drops the corresponding flag → **no limit** for that dimension. Defaults are `agent: 16g/8`, `proxy: 4g/4`.

### llama-server startup tuning

`llama.startup_timeout` (seconds) is how long to wait for each model's `/health` before treating the launch as failed; `llama.startup_attempts` is how many times to relaunch before giving up. Raise both for large models that are slow to load. Defaults: `180` / `2`.

### Network

`network.ipv6` toggles IPv6 for this project's isolated network + proxy (only works if the runtime **and** host route IPv6 — leave `false` on macOS/Apple `container`; see [Network topology](architecture.md#network-topology)). `network.dns` is the upstream resolver the proxy uses for the agent's DNS lookups (default `1.1.1.1`) — set it to a corporate/internal resolver when needed.

### Proxy UI exposure

`proxy.expose_ui` controls where the proxy's mitmweb UI (on its auto-assigned port) is published:

- `localhost` (default) — bound to `127.0.0.1` only; not reachable from other machines.
- `lan` — bound to `0.0.0.0`; reachable across the network (still password-gated by `ADMIN_PASSWORD`).

### Extra agent env / mounts

`agent.env` (a map) adds environment variables to the agent container, and `agent.mounts` (a list of `host:container[:ro]` specs, absolute host paths) adds bind mounts — for one-off tools, caches, or credentials a project needs:

```yaml
agent:
  env:
    MY_API_BASE: https://internal.example.com
  mounts:
    - /Users/me/.cache/pip:/home/pi/.cache/pip:ro
```

### APT dependencies

The agent container installs system packages listed in `.pi-container/dependencies/apt/packages.txt` at startup (via `entrypoint.sh`). Each line is a package name passed to `apt-get install -y`. The file is read from the mounted `/workspace` — changes take effect on the next `run.sh` invocation.

If the agent encounters an unmet system dependency during operation, it should append the package name to this file and inform the user that a container restart is needed.

Example:
```text
# .pi-container/dependencies/apt/packages.txt
curl
```

### Allowlist

The `allowlist.yaml` config in the project's `.pi-container/` defines hostname rules for the [allowlist addon](proxy/allowlist.md) running on that project's mitmproxy transparent proxy. It is **per-project** — each workspace's proxy mounts its own allowlist (seeded from a generic pypi/npm/github/apt template on first run; edit it per project). Traffic from the agent container to non-allowlisted hosts is **blocked with HTTP 403**. If the file is missing entirely, the image's fail-closed default blocks all hosts.

Each rule has a `name`, `mode` (`allow`), a list of `hostnames` (supporting `*` wildcards), and optional `ip_ranges`. Traffic matching any rule is permitted; all other traffic is denied. The default mode is `allow` with a `block` default action.

Current default rules allow:
- **PyPI**: `pypi.org`, `files.pythonhosted.org`
- **npm**: `registry.npmjs.org`, `*.npmjs.org`
- **GitHub**: `github.com`, `api.github.com`, `codeload.github.com`, `objects.githubusercontent.com`, and related subdomains
- **Yarn**: `registry.yarnpkg.com`
- **Debian apt**: `deb.debian.org`, `security.debian.org`, `packages.debian.org`

Add new rules for any additional hostnames the agent needs to reach (e.g. internal APIs, private package registries).

### Token Replacer Secrets

The `token_replacer.yaml` config in `.pi-container/` may reference `${ENV:VAR}` values that must be set in the host environment before running. `run.py` scans this config and injects the values as environment variables into the proxy container. Override `ContainerNetworkManager._pull_secrets_from_config()` (in [`src/network.py`](../src/network.py)) to integrate with a secret store (Vault, AWS Secrets Manager, etc.).

### Transient tmpfs Mounts

`config.yaml`'s `tmpfs.paths` defines paths mounted as **tmpfs** (volatile RAM disks) inside the pi container. Data written to these paths is **lost when the container stops** — useful for build artifacts, caches, and temp files that should not persist across runs.

```yaml
# .pi-container/config.yaml
tmpfs:
  paths:
    - /workspace/.venv
    - /workspace/.pytest_cache
    - /workspace/node_modules/.cache
```

Each path is mounted at the same absolute location inside the container. On podman/docker, mounts use the `notmpcopyup` flag so they start empty (matching Apple `container` behavior) rather than copying the host's bind-mounted content into the tmpfs. Paths are deduplicated and sorted for deterministic output.

### Flow export

`config.yaml`'s `flow_export.enabled` toggles whether the proxy's captured HTTP/HTTPS flow history for this workspace is exported after the agent shuts down (defaults to disabled):

```yaml
# .pi-container/config.yaml
flow_export:
  enabled: true
```

When enabled, `run.py` reads the flows the proxy staged for this session and writes a merged snapshot bucketed by UTC date under `.pi-container/exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.json`. When the section is absent or malformed, export is **off** (fail-safe). The export contains full request/response bodies and headers — see [Version control](#version-control-gitignore) for why `.pi-container/exports/` must never be committed.

### Egress policy

`config.yaml`'s `egress.allow` is the **per-project** proxy egress policy. Only HTTP/HTTPS/DNS are intercepted by mitmproxy; every other protocol is denied by default. Opt a protocol in here to let the agent use it — but note these are forwarded **uninspected** (plain NAT); mitmproxy and the allowlist do not see them.

```yaml
# .pi-container/config.yaml
egress:
  allow:
    ssh: false            # TCP 22 (e.g. git over SSH)
    smtp: false           # TCP 25, 465, 587
    git: false            # TCP 9418 (git://)
    ntp: false            # UDP 123
    tcp_ports: []         # arbitrary extra TCP ports, e.g. [2222, 8443]
    udp_ports: []         # arbitrary extra UDP ports, e.g. [51820]
```

`run.py` translates truthy flags and non-empty port lists into the proxy container's `PROXY_ALLOW_*` env vars, which its entrypoint uses to open the matching `iptables` FORWARD rules. An absent or malformed section means **deny-all** (fail-safe). See [Proxy egress policy](architecture.md#proxy-egress-policy) for the full protocol/port reference.

### Chat templates

Some models need an explicit Jinja chat template. Place them under `.pi-container/chat-templates/<model>/` and reference them from a model's `serverCustomParameters.flags` with a path **relative to the workspace**:

```json
"--chat-template-file", ".pi-container/chat-templates/Ornith-1.0-35B-FP8/chat_template.jinja"
```

`llama-server` runs on the host from the workspace directory, so the relative path resolves against `.pi-container/chat-templates/` in whichever project you launched `pi` from — the templates are seeded there on first run alongside the rest of the config. (Model *weights* are shared across projects under `llama-server/models/`; only the small chat-template files are per-project.)

### Version control (.gitignore)

A ready-to-copy [`.gitignore.example`](../.gitignore.example) lists every entry a workspace needs. Copy the relevant lines into your project's `.gitignore`.

Most of `.pi-container/` is project configuration you **should commit** so the environment is reproducible: `config.yaml`, `allowlist.yaml`, `token_replacer.yaml`, `chat-templates/`, and `dependencies/apt/packages.txt`. (`token_replacer.yaml` holds only `${ENV:VAR}` references, never resolved secrets — see [Token Replacer Secrets](#token-replacer-secrets).)

The one directory you **must ignore** is the flow-export output:

```gitignore
# pi-container: proxy flow capture — sensitive and ephemeral, never commit
.pi-container/exports/
```

`.pi-container/exports/` holds the proxy's captured HTTP/HTTPS traffic — full request/response bodies and headers, including any `Authorization`/cookie values the [token_replacer](proxy/token-replacer.md) did not redact — as raw `flows-<ip>.jsonl` files and date-bucketed snapshots under `exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.json`. Treat it as sensitive. It is also where run-time shadows an empty tmpfs (so the agent can't read prior captures), which can leave an empty `exports/` dir in a workspace even when no traffic was captured. This repo already ignores it; add the entry above to **your** project's `.gitignore` when you run pi-container inside it.

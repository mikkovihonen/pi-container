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
| `MAX_STARTUP_ATTEMPTS` | Number of llama-server startup attempts per model | `2` |
| `CONTAINER_RUNTIME` | Container CLI to use (`container`, `docker`, or `podman`) | Auto-detected (prefers `container` > `docker` > `podman`) |
| `IPV6_ENABLED` | Whether the network stack supports IPv6. When `false`, IPv6 is explicitly disabled across both containers; when `true`, the isolated network gets an IPv6 subnet and the proxy mirrors its rules in `ip6tables` (requires runtime + host v6 egress) | `false` |
| `PROXY_ALLOW_SSH`, `PROXY_ALLOW_SMTP`, `PROXY_ALLOW_GIT`, `PROXY_ALLOW_NTP`, `PROXY_ALLOW_TCP_PORTS`, `PROXY_ALLOW_UDP_PORTS` | Opt-in egress for uninspected non-HTTP protocols (see [Proxy egress policy](architecture.md#proxy-egress-policy)) | unset (denied) |

`BRIDGE_INTERFACE` and `PROXY_UPSTREAM_NETWORK` are derived from `CONTAINER_RUNTIME` and rarely need setting; provide them only to override the per-runtime default for your host.

## Per-workspace Configuration

### Introduction

When launched, pi-container looks for workspace-specific overrides in `./.pi-container` and package dependencies in the directory it's launched in. Each workspace gets its own agent config, proxy, isolated network, allowlist, token-replacer config, tmpfs list, and flow-export directory — all under that workspace's `.pi-container/` (seeded from the `pi-coding-agent/default/` template on first run).

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

The `tmpfs.yaml` config in `.pi-container/` defines paths that are mounted as **tmpfs** (volatile RAM disks) inside the pi container. Data written to these paths is **lost when the container stops** — useful for build artifacts, caches, and temp files that should not persist across runs.

```yaml
# .pi-container/tmpfs.yaml
paths:
  - /workspace/.venv
  - /workspace/.pytest_cache
  - /workspace/.ruff_cache
  - /workspace/src/__pycache__
```

Each path is mounted at the same absolute location inside the container. On podman/docker, mounts use the `notmpcopyup` flag so they start empty (matching Apple `container` behavior) rather than copying the host's bind-mounted content into the tmpfs.

Paths are validated on startup — invalid paths are silently skipped. The list is deduplicated and sorted for deterministic output.

### Version control (.gitignore)

A ready-to-copy [`.gitignore.example`](../.gitignore.example) lists every entry a workspace needs. Copy the relevant lines into your project's `.gitignore`.

Most of `.pi-container/` is project configuration you **should commit** so the environment is reproducible: `allowlist.yaml`, `token_replacer.yaml`, `tmpfs.yaml`, and `dependencies/apt/packages.txt`. (`token_replacer.yaml` holds only `${ENV:VAR}` references, never resolved secrets — see [Token Replacer Secrets](#token-replacer-secrets).)

The one directory you **must ignore** is the flow-export output:

```gitignore
# pi-container: proxy flow capture — sensitive and ephemeral, never commit
.pi-container/exports/
```

`.pi-container/exports/` holds the proxy's captured HTTP/HTTPS traffic — full request/response bodies and headers, including any `Authorization`/cookie values the [token_replacer](proxy/token-replacer.md) did not redact — as raw `flows-<ip>.jsonl` files and date-bucketed snapshots under `exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.json`. Treat it as sensitive. It is also where run-time shadows an empty tmpfs (so the agent can't read prior captures), which can leave an empty `exports/` dir in a workspace even when no traffic was captured. This repo already ignores it; add the entry above to **your** project's `.gitignore` when you run pi-container inside it.

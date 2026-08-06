# pi-container

<p align="center">
  <img src="docs/assets/pi-container-logo.svg" alt="pi-container" width="360">
</p>

A containerized environment for running the [`pi-coding-agent`](https://pi.dev) with local LLM inference and full auditability. A transparent proxy container based on [`mitmproxy`](https://mitmproxy.org) intercepts all HTTP/HTTPS/DNS traffic from the agent container, enforcing allowlisting and injecting secrets as needed. Supports macOS, Linux, and WSL2.

<div align="center" style="text-align:center;" markdown = "1">
  [![CI](https://github.com/mikkovihonen/pi-container/actions/workflows/ci.yml/badge.svg)](https://github.com/mikkovihonen/pi-container/actions/workflows/ci.yml)
  [![Coverage](docs/assets/coverage.svg)](docs/development.md#coverage)
  [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
  [![Python: 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
  [![uv](https://img.shields.io/badge/desc/uv-managed-brightgreen.svg)](https://docs.astral.sh/uv/)
</div>

## Highlights

- **Sandboxed agent** — the agent container reaches the internet **only** through the proxy, on an `--internal` network with no gateway; every other protocol is denied by default.
- **Auditable traffic** — all HTTP/HTTPS/DNS is intercepted by [`mitmproxy`](https://mitmproxy.org), with a hostname **allowlist** and a **token injector**, and captured to a per-project flow export.
- **Local inference** — [`llama.cpp`](https://llama.app)'s `llama-server` runs natively on the host (Metal / CUDA / ROCm), shared across projects by config fingerprint.
- **Per-workspace isolation** — each workspace gets its own pi-container container image, proxy, isolated network, mitmweb port, and config, seeded on first run.
- **Rootless by construction** — runs on [`podman`](https://podman.io), so the agent container lives inside a user namespace where container root is an unprivileged host user. On macOS/Windows the podman machine needs **at least 4 GB** of memory (`podman machine set --memory 4096`); see [Getting Started](docs/getting-started.md).

## Quick start

```bash
cp .env.example .env       # then set ADMIN_PASSWORD to a strong value
./build.sh                 # build the proxy, toolchain and agent images
alias pi="$PWD/run.sh"     # convenience alias
cd /path/to/your/project   # any workspace
pi                         # launch the agent for that workspace
```

See **[Getting Started](docs/getting-started.md)** for prerequisites, hardware requirements, and platform-specific notes.

## Documentation

| Page | What's inside |
|------|---------------|
| [Getting Started](docs/getting-started.md) | Prerequisites, hardware, platform notes, build & run |
| [Architecture](docs/architecture.md) | Components, network topology, egress policy, project structure |
| [Configuration](docs/configuration.md) | Environment variables and per-workspace config (allowlist, token replacer, tmpfs, apt deps, `.gitignore`) |
| [Development](docs/development.md) | Local dev setup, tests, lint, coverage |
| [Releases](docs/releases.md) | Branch strategy, versioning, and release process |
| [Proxy & addons](docs/proxy/overview.md) | Transparent proxy operation, CA cert, and the [allowlist](docs/proxy/allowlist.md) / [token replacer](docs/proxy/token-replacer.md) / [flow export](docs/proxy/flow-export.md) addons + [addon development guide](docs/proxy/addon-development.md) |

## License

[MIT](LICENSE)

## Agentic coding disclosure

Built using agentic coding tools.

- [Pi Coding Agent](https://pi.dev) via [pi-container](https://mikkovihonen.github.io/pi-container/) for agentic coding.
- [Claude Code](https://claude.com/product/claude-code)

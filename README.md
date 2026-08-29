# pi-container

<p align="center">
  <img src="docs/assets/pi-container-logo.svg" alt="pi-container" width="360">
</p>

A containerized environment for running the [`pi-coding-agent`](https://pi.dev) with local large language model (LLM) inference and full auditability. A transparent proxy container based on [`mitmproxy`](https://mitmproxy.org) intercepts all HTTP, HTTPS, and DNS traffic from the agent container. It enforces an allowlist and injects secrets when required. It supports macOS, Linux, and WSL2.

<div align="center" style="text-align:center;" markdown="1">

[![CI](https://github.com/mikkovihonen/pi-container/actions/workflows/ci.yml/badge.svg)](https://github.com/mikkovihonen/pi-container/actions/workflows/ci.yml)
[![Coverage](docs/assets/coverage.svg)](docs/development.md#coverage)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python: 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/desc/uv-managed-brightgreen.svg)](https://docs.astral.sh/uv/)

</div>

## Highlights

- **Sandboxed agent** — the agent container sends all internet traffic **only** through the proxy. It uses an `--internal` network without a gateway. The system denies all other protocols by default.
- **Auditable traffic** — all HTTP, HTTPS, and DNS traffic is intercepted by [`mitmproxy`](https://mitmproxy.org). It uses a hostname **allowlist** and a **token injector**. It saves the traffic to a flow export file for each project.
- **Local inference** — The `llama-server` tool from [`llama.cpp`](https://llama.app) runs directly on the host computer (Metal / CUDA / ROCm). Multiple projects share it by using a configuration fingerprint.
- **Per-workspace isolation** — each workspace has its own pi-container image, proxy, isolated network, mitmweb port, and configuration. The system initializes these items on the first run.
- **Rootless by construction** — It runs on [`podman`](https://podman.io). The agent container runs inside a user namespace. The container root user maps to an unprivileged user on the host. On macOS or Windows, the podman machine requires **at least 4 GB** of memory (`podman machine set --memory 4096`); see [Getting Started](docs/getting-started.md).

## Quick start

```bash
cp .env.example .env       # then set ADMIN_PASSWORD to a complex value
./build.sh                 # build the proxy, toolchain and agent images
alias pi="$PWD/run.sh"     # useful alias
cd /path/to/your/project   # any workspace
pi                         # launch the agent for that workspace
```

See **[Getting Started](docs/getting-started.md)** for prerequisites, hardware requirements, and platform-specific notes.

## Documentation

| Page | Contents |
|------|---------------|
| [Getting Started](docs/getting-started.md) | Prerequisites, hardware, platform notes, build & run |
| [Architecture](docs/architecture.md) | Components, network topology, egress policy, project structure |
| [Sandboxing Technologies](docs/sandboxing.md) | Cloud vs. local sandbox paradigms, microVMs, host kernel isolation, and container tradeoffs |
| [Configuration](docs/configuration.md) | Environment variables and per-workspace configuration (allowlist, token replacer, temporary file system, apt dependencies, `.gitignore`) |
| [Development](docs/development.md) | Local dev setup, tests, lint, coverage |
| [Releases](docs/releases.md) | Branch strategy, versioning, and release process |
| [Proxy & addons](docs/proxy/overview.md) | Transparent proxy operation, CA cert, and the [allowlist](docs/proxy/allowlist.md) / [token replacer](docs/proxy/token-replacer.md) / [flow export](docs/proxy/flow-export.md) addons + [addon development guide](docs/proxy/addon-development.md) |

## License

[MIT](LICENSE)

## Agentic coding disclosure

Built using agentic coding tools.

- [Pi Coding Agent](https://pi.dev) via [pi-container](https://mikkovihonen.github.io/pi-container/) for agentic coding.
- [Claude Code](https://claude.com/product/claude-code)

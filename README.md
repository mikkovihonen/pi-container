# pi-container

<p align="center">
  <img src="docs/assets/pi-container-logo.svg" alt="pi-container" width="360">
</p>

This tool runs a sandboxed [`pi-coding-agent`](https://pi.dev) and uses a local LLM. The sandbox proxy provides full auditability. In addition, the sandbox proxy intercepts the traffic from the agent. Also, this proxy blocks traffic to hosts not on the allowlist and injects secrets into the traffic. The tool works on macOS, Linux, and WSL2.

<div align="center" style="margin-top:50px;text-align:center;" markdown="1">

[![CI](https://github.com/mikkovihonen/pi-container/actions/workflows/ci.yml/badge.svg)](https://github.com/mikkovihonen/pi-container/actions/workflows/ci.yml)
[![Coverage](docs/assets/coverage.svg)](docs/development.md#coverage)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python: 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/desc/uv-managed-brightgreen.svg)](https://docs.astral.sh/uv/)

</div>

## Highlights

**Sandboxed agent.** The agent sends all internet traffic through the proxy. The agent uses an internal network without a gateway. The system blocks all other protocols.

**Traffic logging.** [`mitmproxy`](https://mitmproxy.org) intercepts the HTTP traffic, the HTTPS traffic, and the DNS traffic. The proxy uses a hostname allowlist and a token injector. The proxy saves traffic to a flow file per project.

**Local inference.** `llama-server` from [`llama.cpp`](https://llama.app) runs on the host computer. The tool supports the Metal backend, the CUDA backend, and the ROCm backend. The projects share one server per configuration fingerprint.

**Isolated workspaces.** Each workspace gets an image, a proxy, a network, and own configuration. The system creates the artifacts automatically on the first run.

**Rootless design.** The system runs on [`podman`](https://podman.io). The agent runs inside a user namespace. The root user in the container maps to a host user. The Podman machine requires 4 GB of memory on macOS or Windows. The [Getting Started](docs/getting-started.md) page provides details.

## Quick setup

```bash
cp .env.example .env
./build.sh
alias pi="$PWD/run.sh"
cd /path/to/your/project
pi
```

Read **[Getting Started](docs/getting-started.md)** for the prerequisites, the hardware needs, and the platform details.

## Documentation

| Page | Contents |
|------|----------|
| [Getting Started](docs/getting-started.md) | The prerequisites, the hardware, and the platform details. The page shows the build steps and the execution steps. |
| [Architecture](docs/architecture.md) | The page covers the components, the network topology, the egress policy, and the project structure. |
| [Configuration](docs/configuration.md) | The environment variables and the workspace settings. The page covers the allowlist, the token replacer, the file system, the apt dependencies, and `.gitignore`. |
| [Development](docs/development.md) | The local setup, the verification procedures, the lint procedures, and the coverage reports. |
| [Releases](docs/releases.md) | The branch strategy, the versioning, and the release process. |
| [Proxy and addons](docs/proxy/overview.md) | The page covers the proxy operation, the CA cert, and the proxy addons. |

## License

[MIT](LICENSE)

## Tools disclosure

The project uses AI tools.

- [Pi Coding Agent](https://pi.dev) via [pi-container](https://mikkovihonen.github.io/pi-container/)
- [Claude Code](https://claude.com/product/claude-code)

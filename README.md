# pi-container

A containerized environment for running the `pi-coding-agent` with local LLM inference. Supports macOS, Linux, and WSL2.

## Prerequisites

Before running, ensure you have the following installed on your host machine:

- **Container Runtime**:

  The project supports three container runtimes. Set `CONTAINER_RUNTIME` or pass it via the build/run scripts:

  | Runtime | Platform | Installation |
  |---------|----------|--------------|
  | `container` | macOS | Download the [macOS installer (.pkg)](https://github.com/apple/container/releases/download/1.0.0/container-1.0.0-installer-signed.pkg) |
  | `docker` | macOS / Linux / WSL2 | [Install Docker](https://docs.docker.com/get-docker/) |
  | `podman` | macOS / Linux / WSL2 | `brew install podman` (macOS) or `sudo apt install podman` (Debian/Ubuntu) or your distro's package manager |

- **llama.cpp**: Specifically `llama-server`.
  - On macOS: `brew install llama.cpp`
  - On Linux (Debian/Ubuntu): `sudo apt install llama.cpp`
  - On Linux (other): [build from source](https://github.com/ggerganov/llama.cpp)
  - On WSL2: `sudo apt install llama.cpp`
- **socat**:
  - On macOS: `brew install socat`
  - On Linux (Debian/Ubuntu): `sudo apt install socat`
  - On WSL2: `sudo apt install socat`
- **Hugging Face CLI**: Required for downloading models.
  - `pip install huggingface_hub[cli]`

## Hardware Requirements

To run this environment comfortably, especially when utilizing the full 128k context window, the following is recommended:

- **Processor:**
  - Apple Silicon (M2-series Max/Ultra or above) for high memory bandwidth.
  - On Linux/WSL2: A modern multi-core CPU with AVX2 support.
- **Memory (RAM):**
  - **Minimum:** 32 GB (Performance may degrade with large contexts)
  - **Recommended:** 64 GB or more (For optimal performance)
- **Storage:** 50 GB of available SSD space.

## Architecture

The system consists of three components running as containers or processes:

1. **`llama-server`** (host process): Runs `llama.cpp`'s `llama-server` natively on the host. Provides OpenAI-compatible API endpoints for one or more local LLM models. Each model is configured via `pi-coding-agent/home/.pi/agent/models.json`.

2. **`pi-coding-agent-proxy`** (container): A transparent proxy container based on Debian with [mitmproxy](https://mitmproxy.org/). It intercepts and monitors all HTTP/HTTPS/DNS traffic from the pi container. A self-signed CA certificate is installed into the pi container image so HTTPS traffic can be decrypted. The mitmweb web UI is available at port 8081. The proxy uses [addons](pi-coding-agent-proxy/addons/) for traffic manipulation — an allowlist and a token replacer that redacts sensitive data (API keys, bearer tokens, session cookies) from intercepted requests.

3. **`pi-coding-agent`** (container): The main agent container. It runs on a multi-stage build from `node:26.3.1-trixie-slim`, with Python 3.14.6 compiled from source and `uv` for dependency management. The agent container connects to the proxy via an internal `isolated-net` network. `socat` bridges on the host forward traffic from the host's llama-server ports into the container network so the agent can reach the local LLM APIs.

### Network topology

```
Host
 ├── llama-server (llama.cpp) ── socat bridge ──▶ isolated-net
 │
 ├── pi-coding-agent-proxy ── eth0 → internet (NAT/MASQUERADE)
 │                              eth1 → isolated-net (transparent proxy)
 │
 └── pi-coding-agent ── isolated-net (DNS → proxy eth1)
```

The proxy container requires `CAP_NET_ADMIN` to manage iptables rules and IP forwarding for transparent proxying.

## Platform-Specific Notes

### Linux / WSL2

- **Container runtime**: Use `docker` or `podman` instead of the Apple `container` CLI. Set `CONTAINER_RUNTIME=docker` or `CONTAINER_RUNTIME=podman` in your `.env`.
- **Network**: The default bridge interface is `docker0` (Docker) or `podman0` (Podman). The proxy upstream network defaults to `bridge` (Docker) or `podman` (Podman). Override via `BRIDGE_INTERFACE` and `PROXY_UPSTREAM_NETWORK` in `.env` if needed.
- **LLaMA backend**: The `llama-server` binary runs natively on Linux/WSL2. For GPU acceleration on Linux, build llama.cpp with CUDA or ROCm support.
- **WSL2**: Ensure WSL2 is properly configured with a Linux distro. Docker Desktop or Podman can be used inside WSL2 for containerization.

### macOS

- **Container runtime**: The Apple `container` CLI is the default.
- **Network**: The default bridge interface is `bridge100`. The proxy upstream network defaults to `default`.
- **LLaMA backend**: Runs natively using Apple's Metal GPU acceleration.

## Project Structure

```
.
├── build.sh                          # Build script (delegates to src/build.py)
├── run.sh                            # Run script (delegates to src/run.py)
├── .env.example                      # Example environment configuration
├── pyproject.toml                    # Python project config (ruff linting/formatting)
├── .gitignore
│
├── src/                              # Python source for build and run utilities
│   ├── build.py                      # Builds proxy and agent container images
│   ├── run.py                        # Manages full environment lifecycle
│   ├── util.py                       # Shared utilities (env loading, validation, signals)
│   └── tests/                        # Pytest test suite
│       ├── conftest.py
│       ├── test_build.py
│       ├── test_run.py
│       └── test_util.py
│
├── pi-coding-agent/                  # Main agent container
│   ├── Containerfile                 # Multi-stage build (builder + runner)
│   ├── entrypoint.sh                 # Container entrypoint (env setup, socat bridges, uv venv)
│   └── home/.pi/agent/
│       ├── models.json               # LLM provider/server configuration
│       ├── AGENTS.md                 # Agent instructions
│       ├── config.json
│       └── extensions/               # Agent extensions (e.g. terminal-beautifier)
│
├── pi-coding-agent-proxy/            # Transparent proxy container
│   ├── Containerfile                 # mitmproxy-based transparent proxy
│   ├── entrypoint.sh                 # iptables rules + mitmweb launch
│   └── addons/
│       ├── allowlist/                # Hostname allowlist addon
│       └── token_replacer/           # Token redaction addon (API keys, Bearer tokens, cookies, JWTs)
│
├── llama-server/                     # LLM server components
│   ├── models/                       # Downloaded GGUF model files (gitignored)
│   ├── chat-templates/               # Jinja chat templates for models
│   ├── logs/                         # llama-server log files (gitignored)
│   └── .locks/                       # Process lock files (gitignored)
│       └── local-gemma/              # Per-model lock directory
│           ├── .llama_server.pid
│           └── .llama_server_refcount
│
├── pi-coding-agent/setups/           # Model-specific setup directories
│   └── gemma-4-26b-a4b-it-qat-GGUF/  # Notes and config for specific model setups
│
└── .pi-container/                    # Host-side config mounted into proxy
    ├── token_replacer.yaml           # Token redaction rules
    └── allowlist.yaml                # Hostname allowlist
```

## Getting Started

### 1. Configure Environment

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

At minimum, **change `ADMIN_PASSWORD`** from `CHANGEME` to a strong password before running — the proxy's mitmweb UI will refuse to start with the default value.

### 2. Build the Container Images

```zsh
./build.sh
```

This builds two images in order: `pi-coding-agent-proxy:local` (the transparent proxy) and `pi-coding-agent:local` (the main agent). The agent image depends on the proxy image to copy the mitmproxy CA certificate into the system trust store.

### 3. Run the Agent

The `run.sh` script manages the entire lifecycle: it validates the environment, starts llama-server instances for each model defined in `models.json`, sets up the proxy container with its transparent proxy rules, and launches the pi container.

```sh
# Recommended: alias for convenience
alias pi="~/workspace/pi-container/run.sh"

# Run with an optional session ID
pi --session 1234abcd-ef56-78ab-cd90-1234abcd56ef
```

The script reads `pi-coding-agent/home/.pi/agent/models.json` to determine which LLM providers to start. Each entry defines a model (main, optional draft, and optional vision/mmproj files), download source, server flags, and OpenAI-compatible API configuration. The proxy container is managed with a refcount so it persists across multiple concurrent `pi` invocations.

### 4. Using the Agent

Once the server is ready, you can interact with the agent through the terminal. The current directory is mounted to `/workspace` inside the container, allowing the agent to read and write files in your project.

The agent's entrypoint automatically installs apt packages listed in `.pi/dependencies/apt/packages.txt` if present in the mounted workspace. It also sets up `socat` bridges so the container can reach llama-server processes running on the host.

### 5. Using the Proxy

The transparent proxy web UI (mitmweb) is available at [http://127.0.0.1:8081](http://127.0.0.1:8081). See [pi-coding-agent-proxy/README.md](pi-coding-agent-proxy/README.md) for details on proxy operation, CA certificate installation, and addons.

## Environment Configuration

The application uses a `.env` file for managing environment-specific settings. See `.env.example` for all available options.

### Security

- **`ADMIN_PASSWORD`** MUST be changed from the default `CHANGEME` before running.
  The proxy's mitmweb UI at port 8081 will refuse to start with a default or empty password.
- **Model integrity**: Set `sha256` in `models.json` to verify downloaded model files.
  Without a checksum, downloads proceed without integrity verification.

### Run Configuration

The following environment variables are used by `build.sh` and `run.sh` to configure the container runtime, proxy, and `llama-server`:

| Variable | Description | Default |
|----------|-------------|---------|
| `PI_IMAGE_TAG` | The tag of the pi container image to run | `pi-coding-agent:local` |
| `PROXY_IMAGE_TAG` | The tag of the proxy container image to run | `pi-coding-agent-proxy:local` |
| `LLAMA_BIN` | Path to the `llama-server` executable | `llama-server` or `/opt/homebrew/bin/llama-server` |
| `BRIDGE_INTERFACE` | The network interface for the `socat` bridge | `bridge100` |
| `PROXY_UPSTREAM_NETWORK` | The network the proxy container connects to | `default` |
| `LOG_LEVEL` | Log level | `INFO` |
| `ADMIN_PASSWORD` | Password for mitmproxy Web UI | `CHANGEME` |
| `MAX_STARTUP_ATTEMPTS` | Number of llama-server startup attempts per model | `2` |
| `CONTAINER_RUNTIME` | Container CLI to use (`container`, `docker`, or `podman`) | Auto-detected |

### Token Replacer Secrets

The `token_replacer.yaml` config in `.pi-container/` may reference `${ENV:VAR}` values that must be set in the host environment before running. `run.py` scans this config and injects the values as environment variables into the proxy container. Override `ContainerNetworkManager._pull_secrets_from_config()` to integrate with a secret store (Vault, AWS Secrets Manager, etc.).

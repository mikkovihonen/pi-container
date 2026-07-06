# Getting Started

## Prerequisites

Before running, ensure you have the following installed on your host machine:

- **[uv](https://docs.astral.sh/uv/)**: Manages the Python environment and dependencies for the host-side build/run scripts.
  - On macOS: `brew install uv`
  - Other platforms: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - `build.sh` and `run.sh` invoke `uv run`, which creates `.venv` and installs the declared dependencies (including the `hf` CLI and `huggingface_hub`) automatically on first use. No manual `pip install` is needed.

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
- **socat** (Apple `container` runtime only — used to expose the host `llama-server` on the container bridge; not needed for podman/docker):
  - On macOS: `brew install socat`

Python dependencies (`huggingface_hub[cli]`, `pyyaml`) are declared in
`pyproject.toml` and installed by `uv` — you do not install them manually.

## Hardware Requirements

To run this environment comfortably, especially when utilizing the full 128k context window, the following is recommended:

- **Processor:**
  - Apple Silicon (M2-series Max/Ultra or above) for high memory bandwidth.
  - On Linux/WSL2: A modern multi-core CPU with AVX2 support.
- **Memory (RAM):**
  - **Minimum:** 32 GB (Performance may degrade with large contexts)
  - **Recommended:** 64 GB or more (For optimal performance)
- **Storage:** 50 GB of available SSD space.

## Platform-Specific Notes

### Linux / WSL2

- **Container runtime**: Use `docker` or `podman`. Set `CONTAINER_RUNTIME=docker` or `CONTAINER_RUNTIME=podman` in your `.env`.
- **Network**: The default bridge interface is `docker0` (Docker) or `podman0` (Podman). The proxy upstream network defaults to `bridge` (Docker) or `podman` (Podman). Override via `BRIDGE_INTERFACE` and `PROXY_UPSTREAM_NETWORK` in `.env` if needed.
- **LLaMA backend**: The `llama-server` binary runs natively on Linux/WSL2. For GPU acceleration on Linux, build llama.cpp with CUDA or ROCm support.
- **WSL2**: Ensure WSL2 is properly configured with a Linux distro. Docker Desktop or Podman can be used inside WSL2 for containerization.

### macOS

- **Container runtime**: The Apple `container` CLI is the default.
- **Network**: The default bridge interface is `bridge100` and the proxy upstream network defaults to `default`. These per-runtime defaults are applied automatically; `BRIDGE_INTERFACE` / `PROXY_UPSTREAM_NETWORK` are only needed to override them.
- **LLaMA backend**: Runs natively using Apple's Metal GPU acceleration.
- **podman / docker on macOS**: These run containers inside a Linux VM (no `podman0`/`docker0` bridge exists on the host), so `socat` is not used — the proxy reaches host `llama-server` via `host.containers.internal` (gvproxy). The runtime abstraction ([`src/runtimes.py`](https://github.com/mikkovihonen/pi-container/blob/main/src/runtimes.py)) handles these differences. Each runtime configures the isolated network and proxy attachment differently:

| Runtime | Network flags | Interface pinning |
|---------|--------------|-------------------|
| Apple `container` | `--internal --subnet-v6 <ula-subnet>` | None — uses default `eth0`/`eth1` |
| Podman | `--internal --disable-dns` | `interface_name=eth0` / `interface_name=eth1` |
| Docker | `--internal` | None — uses default `eth0`/`eth1` |

## Build and Run

### 1. Configure Environment

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

At minimum, **change `ADMIN_PASSWORD`** from `CHANGEME` to a strong password before running — the proxy's mitmweb UI will refuse to start with the default value.

See [Configuration](configuration.md) for the full list of environment variables.

### 2. Build the Container Images

```zsh
./build.sh
```

`build.sh` (and `run.sh`) run through `uv`, which creates the `.venv` and installs dependencies from `uv.lock` on first invocation — no separate setup step is required. To provision the environment ahead of time, run `uv sync`.

This builds two images in order: `pi-coding-agent-proxy:local` (the transparent proxy) and `pi-coding-agent:local` (the main agent). The agent image depends on the proxy image to copy the mitmproxy CA certificate into the system trust store.

### 3. Run the Agent

The `run.sh` script manages the entire lifecycle: it validates the environment, starts llama-server instances for each model defined in `models.json`, sets up the proxy container with its transparent proxy rules, and launches the pi container.

```sh
# Recommended: alias for convenience
alias pi="~/workspace/pi-container/run.sh"

# Run with an optional session ID
pi --session 1234abcd-ef56-78ab-cd90-1234abcd56ef
```

The script reads `<project>/.pi-container/agent/models.json` (seeded from the `pi-coding-agent/default/` template on first run) to determine which LLM providers to start. Each entry defines a model, download source, server flags, and OpenAI-compatible API configuration. Each workspace gets its own proxy container and isolated network (named by a hash of the project path); concurrent `pi` invocations **from the same workspace** share that workspace's proxy via a refcount.

### 4. Using the Agent

Once the server is ready, you can interact with the agent through the terminal. The current directory is mounted to `/workspace` inside the container, allowing the agent to read and write files in your project.

The agent's entrypoint automatically installs apt packages listed in `.pi-container/dependencies/apt/packages.txt` if present in the mounted workspace, points the container's default route and DNS at the proxy, and applies the host's git config. Reaching the host `llama-server` is handled by the proxy (via a host-side `socat` bridge for Apple `container`, or `host.containers.internal` for podman/docker) — see [Architecture](architecture.md).

### 5. Using the Proxy

The transparent proxy web UI (mitmweb) is published on an auto-assigned host port — run.py logs the exact `http://127.0.0.1:<port>` URL at startup (each workspace's proxy gets its own port). See [Proxy overview](proxy/overview.md) for details on proxy operation, CA certificate installation, and addons.

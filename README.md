# pi-container

A containerized environment for running the `pi-coding-agent`. Supports macOS, Linux, and WSL2.

## Prerequisites

Before running, ensure you have the following installed on your host machine:

- **Python**: Required for running the scripts and installing dependencies.
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

- `pi-coding-agent/`: Main agent container definition and entrypoint.
- `pi-coding-agent-proxy/`: A Debian-based router container that routes traffic via [mitmproxy](https://mitmproxy.org/) to allow traffic monitoring.
- `llama-server/`: Directory for `llama.cpp` server components (models, templates, logs).
- `src/`: Python source code for the build and run utilities.
- `build.sh`: Script to build the container images.
- `run.sh`: Script to manage the environment lifecycle.

## Getting Started

### 1. Build the Container Image

Build the environment image using the provided script:

```zsh
./build.sh
```

By default, this builds an image tagged as `pi-coding-agent:local`.

### 2. Run the Agent

`run.sh` script manages the entire lifecycle: it downloads missing models, starts the `llama-server` in the background, and launches the container.
It is recommended to alias the script for convenience:

```sh
# macOS (zsh)
alias pi="~/workspace/pi-container/run.sh"

# Linux / WSL2 (bash or zsh)
alias pi="~/workspace/pi-container/run.sh"
```

After the aliasing, the script can be run in your project directory and it will mount the project directory in a Linux container with `npm` and `python` available to `pi`.

```sh
pi --session 1234abcd-ef56-78ab-cd90-1234abcd56ef
```

### 3. Using the Agent

Once the server is ready, you can interact with the agent through the terminal. The current directory is mounted to `/workspace` inside the container, allowing the agent to read and write files in your project.

Agent looks for `.pi/dependencies/apt/packages.txt` under the directory it was started in and prompts the user to install the packages listed in the file.

### 4. Using the Proxy

Web UI is available at 8081. See [pi-coding-agent-proxy/README.md](pi-coding-agent-proxy/README.md) for details.

## Environment Configuration

The application uses a `.env` file for managing environment-specific settings. To get started, copy the example configuration file to `.env`:

```bash
cp .env.example .env
```

Then, edit `.env` to include your specific configuration.

### Security

- **`ADMIN_PASSWORD`** MUST be changed from the default `CHANGEME` before running.
  The proxy's mitmweb UI at port 8081 will refuse to start with a default or empty password.
- **Model integrity**: Set `sha256` in `models.json` to verify downloaded model files.
  Without a checksum, downloads proceed without integrity verification.

### Run Configuration

The following environment variables are used by `run.sh` to configure the container runtime and the `llama-server`:

| Variable | Description | Default |
|----------|-------------|---------|
| `PI_IMAGE_TAG` | The tag of the pi container image to run | `pi-coding-agent:local` |
| `PROXY_IMAGE_TAG` | The tag of the  proxycontainer image to run | `pi-coding-agent-proxy:local` |
| `LLAMA_BIN` | Path to the `llama-server` executable | `llama-server` or `/opt/homebrew/bin/llama-server` |
| `BRIDGE_INTERFACE` | The network interface for the `socat` bridge | `bridge100` |
| `PROXY_UPSTREAM_NETWORK` | The network the proxy container connects to | `default` |
| `LOG_LEVEL` | Log level | `INFO` |
| `ADMIN_PASSWORD` | Password for mitmproxy Web UI | `password` |
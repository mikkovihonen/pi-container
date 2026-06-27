# pi-container

A containerized environment for running the `pi-coding-agent` on macOS.

## Prerequisites

Before running, ensure you have the following installed on your host machine:

- **Python**: Required for running the scripts and installing dependencies.
- **Container Runtime**:
  - On macOS, download and install the `container` CLI (v1.0.0) via the [macOS installer (.pkg)](https://github.com/apple/container/releases/download/1.0.0/container-1.0.0-installer-signed.pkg).
- **llama.cpp**: Specifically `llama-server`.
  - On macOS: `brew install llama.cpp`
- **socat**:
  - On macOS: `brew install socat`
- **Hugging Face CLI**: Required for downloading models.
  - `pip install huggingface_hub[cli]`

## Hardware Requirements

To run this environment comfortably, especially when utilizing the full 128k context window, the following is recommended:

- **Processor:**
  - Apple Silicon (M2-series Max/Ultra or above) for high memory bandwidth.
- **Memory (RAM/Unified Memory):**
  - **Minimum:** 32 GB (Performance may degrade with large contexts)
  - **Recommended:** 64 GB or more (For optimal performance)
- **Storage:** 50 GB of available SSD space.

## Getting Started

### 1. Build the Container Image

Build the environment image using the provided script:

```zsh
./scripts/build.sh
```

By default, this builds an image tagged as `pi-coding-agent:local`.

### 2. Run the Agent

`run.sh` script manages the entire lifecycle: it downloads missing models, starts the `llama-server` in the background, and launches the container.
On macOS the script is meant to be aliased as `pi` in `~/.zshrc`

``` zsh
alias pi="~/workspace/pi-container/scripts/run.sh"
```

After the aliasing, the script can be run in your project directory and it will mount the project directory in a Linux container with `npm` and `python` available to `pi`.

``` zsh
pi --session 1234abcd-ef56-78ab-cd90-1234abcd56ef
```

### 3. Using the Agent

Once the server is ready, you can interact with the agent through the terminal. The current directory is mounted to `/workspace` inside the container, allowing the agent to read and write files in your project.

Agent looks for `.pi/dependencies/apt/packages.txt` under the directory it was started in and prompts the user to install the packages listed in the file.

## Environment Configuration

The application uses a `.env` file for managing environment-specific settings. To get started, copy the example configuration file to `.env`:

```bash
cp .env.example .env
```

Then, edit `.env` to include your specific configuration.

### Run Configuration

The following environment variables are used by `scripts/run.py` to configure the container runtime and the `llama-server`:

| Variable | Description | Default |
|----------|-------------|---------|
| `IMAGE_TAG` | The tag of the container image to run | `pi-coding-agent:local` |
| `LLAMA_BIN` | Path to the `llama-server` executable | `llama-server` or `/opt/homebrew/bin/llama-server` |
| `BRIDGE_INTERFACE` | The network interface for the `socat` bridge | `bridge100` |

## Project Structure

### Root Directory
- `.env.example`: Template for environment variable configuration.
- `.gitignore`: Specifies files and directories for Git to ignore.
- `Containerfile`: Defines the container image (Node.js base, Python 3.14, `pi-coding-agent`).
- `README.md`: Project documentation.
- `entrypoint.sh`: The script executed when the container starts.

### `pi-home/`
Contains configuration templates and scripts used by the agent inside the container.
- `.pi/`: Internal configuration for the `pi-coding-agent`.
  - `agent/`:
    - `AGENTS.md`: Agent configuration/documentation.
    - `auth.json`: Authentication settings.
    - `config.json`: General agent configuration.
    - `models.json`: Model and provider configurations.
    - `settings.json`: Agent settings.
    - `.pi_ignore`: Internal ignore file used by the agent.
    - `extensions/`: Directory containing custom agent extensions (e.g., `plan-mode`, `terminal-beautifier`).
- `.gitconfig`: Git configuration for the container user.

### `scripts/`
Utility and orchestration scripts for managing the container environment.
- `build.sh`: Shell script to build the container image.
- `build.py`: Python version of the build script.
- `run.sh`: Shell script to orchestrate model downloads, `llama-server`, and container execution.
- `run.py`: Python version of the run script.
- `util.py`: Common utility functions used by the scripts.

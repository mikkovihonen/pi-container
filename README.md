# Gemma 4 Pi-Coding-Agent Environment

A containerized environment for running the `pi-coding-agent` on macOS.

## Features

- **Gemma 4 Support**: Optimized for Gemma 4 26B models.
- **Speculative Decoding**: Uses a draft model (`gemma4-26b-mtp`) to accelerate the main model (`gemma-4-26B-A4B-it-qat-GGUF`) via `llama-server`.
- **Containerized**: Provides a consistent environment with `Node 26.3.1`, `Python 3.14.6`, `uv`, and `pi-coding-agent` pre-installed.

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

Agent looks for `dependencies/apt/packages.txt` under the directory it was started in and prompts the user to install the packages listed in the file.

## Environment Configuration

The application uses a `.env` file for managing environment-specific settings. To get started, copy the example configuration file to `.env`:

```bash
cp .env.example .env
```

Then, edit `.env` to include your specific configuration.

The following environment variables can be tuned in your `.env` file to customize model behavior:

| Variable | Description | Default |
|----------|-------------|---------|
| `MAIN_MODEL_HF_REPO` | Main model Hugging Face repository | `unsloth/gemma-4-26B-A4B-it-qat-GGUF` |
| `MAIN_MODEL_HF_FILE` | Main model Hugging Face file | `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf` |
| `DRAFT_MODEL_HF_REPO` | Draft model Hugging Face repository | `unsloth/gemma-4-26B-A4B-it-qat-GGUF` |
| `DRAFT_MODEL_HF_FILE` | Draft model Hugging Face file | `mtp-gemma-4-26B-A4B-it.gguf` |
| `MODEL_ID` | The alias used by the llama-server | `gemma-4-26b-a4b-it-qat-ud-q4_k_xl` |
| `MODEL_CTX_SIZE` | Context window size | `131072` |
| `MODEL_COMPACTION_THRESHOLD` | Threshold for context compaction | `128000` |
| `MODEL_BATCH_SIZE` | Batch size for processing | `4096` |
| `MODEL_TEMPERATURE` | Sampling temperature | `0.2` |
| `MODEL_TOP_P` | Nucleus sampling parameter | `0.95` |
| `MODEL_SPEC_DRAFT_N_MAX` | Max number of speculative tokens | `4` |
| `MODEL_SPEC_DRAFT_N_MIN` | Min number of speculative tokens | `1` |
| `MODEL_PARALLEL` | Number of parallel sequences | `1` |
| `MODEL_U_BATCH_SIZE` | Unbatched batch size | `512` |
| `MODEL_FLASH_ATTN` | Enable flash attention (on/off) | `on` |
| `MODEL_CTX_CHECKPOINTS` | Number of context checkpoints | `32` |
| `MODEL_CHECKPOINT_MIN_STEP` | Min steps between checkpoints | `256` |
| `MODEL_PRIO` | Priority for the server | `2` |

## Project Structure

- `Containerfile`: Defines the container image (Node.js base, Python 3.14, `pi-coding-agent`).
- `models/`: Contains the GGUF model files (Main and Draft).
- `pi-home/`: Contains configuration templates and model substitution scripts.
- `scripts/`:
  - `build.sh`: Builds the container image.
  - `run.sh`: Orchestrates model downloads, `llama-server`, and container execution.
  - `entrypoint.sh`: Container entrypoint that sets up the Python environment.

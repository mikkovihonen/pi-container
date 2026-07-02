import sys

sys.dont_write_bytecode = True

import os
import shutil
import subprocess
from pathlib import Path

from util import EnvironmentError, load_dotenv, validate_environment

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DOTENV_PATH = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

LLAMA_BIN = os.environ.get("LLAMA_BIN") or shutil.which("llama-server")
PI_IMAGE_TAG = os.environ.get("PI_IMAGE_TAG", "pi-coding-agent:local")
PROXY_IMAGE_TAG = os.environ.get("PROXY_IMAGE_TAG", "pi-coding-agent-proxy:local")


def build_proxy(runtime: str) -> None:
    print(f"Building proxy image ({runtime}): {PROXY_IMAGE_TAG}")
    subprocess.run(
        [
            runtime,
            "build",
            "--tag",
            PROXY_IMAGE_TAG,
            "--file",
            str(REPO_ROOT / "pi-coding-agent-proxy" / "Containerfile"),
            str(REPO_ROOT),
        ],
        check=True,
    )


def build_agent(runtime: str) -> None:
    print(f"Building agent image ({runtime}): {PI_IMAGE_TAG}")
    subprocess.run(
        [
            runtime,
            "build",
            "--tag",
            PI_IMAGE_TAG,
            "--file",
            str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
            str(REPO_ROOT),
        ],
        check=True,
    )


def main() -> None:
    try:
        runtime = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        print(f"Environment Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        build_proxy(runtime)
        build_agent(runtime)
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: '{runtime}' command not found. Please ensure the container CLI is installed and in your PATH.")
        sys.exit(1)


if __name__ == "__main__":
    main()

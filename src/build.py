import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.dont_write_bytecode = True

from util import EnvironmentError, load_dotenv, validate_environment

logger = logging.getLogger(__name__)

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DOTENV_PATH = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

LLAMA_BIN = os.environ.get("LLAMA_BIN") or shutil.which("llama-server")
PI_IMAGE_TAG = os.environ.get("PI_IMAGE_TAG", "pi-coding-agent:local")
PROXY_IMAGE_TAG = os.environ.get("PROXY_IMAGE_TAG", "pi-coding-agent-proxy:local")


def _run_command_with_logging(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, logging each stdout/stderr line via logger.info.

    Merges stderr into stdout so both streams appear together in log output.
    Raises ``subprocess.CalledProcessError`` on non-zero exit.
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **kwargs,
    )

    def _log_stream() -> None:
        for line in process.stdout:  # type: ignore[union-attr]
            logger.info(line.rstrip())

    thread = threading.Thread(target=_log_stream, daemon=True)
    thread.start()
    returncode = process.wait()
    thread.join()

    result = subprocess.CompletedProcess(cmd, returncode)
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)
    return result


def build_proxy(runtime: str) -> None:
    logger.info(f"Building proxy image ({runtime}): {PROXY_IMAGE_TAG}")
    _run_command_with_logging(
        [
            runtime,
            "build",
            "--tag",
            PROXY_IMAGE_TAG,
            "--file",
            str(REPO_ROOT / "pi-coding-agent-proxy" / "Containerfile"),
            str(REPO_ROOT),
        ],
    )


def build_agent(runtime: str) -> None:
    logger.info(f"Building agent image ({runtime}): {PI_IMAGE_TAG}")
    _run_command_with_logging(
        [
            runtime,
            "build",
            "--tag",
            PI_IMAGE_TAG,
            "--file",
            str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
            str(REPO_ROOT),
        ],
    )


def build_project_image(
    runtime: str, root_commands_path: str, pi_commands_path: str, image_tag: str, label_hash: str
) -> None:
    """Build a project-specific agent image with baked-in command scripts.

    Args:
        runtime: Container runtime (docker or podman).
        root_commands_path: Absolute path to root/commands.sh on the host.
        pi_commands_path: Absolute path to pi/commands.sh on the host.
        image_tag: Image tag for the project-specific image (e.g., "pi-coding-agent-<hash>.local").
        label_hash: Content hash to store in the image label for cache invalidation.
    """
    logger.info(f"Building project-specific agent image ({runtime}): {image_tag}")
    _run_command_with_logging(
        [
            runtime,
            "build",
            "--build-context",
            f"root_commands_path={Path(root_commands_path).parent}",
            "--build-arg",
            f"ROOT_COMMANDS_PATH={Path(root_commands_path).name}",
            "--build-arg",
            f"LABEL_HASH={label_hash}",
            "--tag",
            image_tag,
            "--file",
            str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
            str(REPO_ROOT),
        ],
    )


def main() -> None:
    try:
        runtime = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)

    try:
        build_proxy(runtime)
        build_agent(runtime)
    except subprocess.CalledProcessError as e:
        logger.error(f"Build failed: {e}")
        sys.exit(1)
    except FileNotFoundError:
        logger.error(
            f"Error: '{runtime}' command not found. Please ensure the container CLI is installed and in your PATH."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

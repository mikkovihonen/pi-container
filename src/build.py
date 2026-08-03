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

# ─── Logging ─────────────────────────────────────────────────────────────────
# build.py is its own entry point (build.sh) and does not import ``config``,
# where the root logger is normally configured. Without this the per-line build
# output emitted at INFO is dropped and a failed build surfaces nothing but its
# exit status.

log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

LLAMA_BIN = os.environ.get("LLAMA_BIN") or shutil.which("llama-server")
PI_IMAGE_TAG = os.environ.get("PI_IMAGE_TAG", "pi-coding-agent:local")
PROXY_IMAGE_TAG = os.environ.get("PROXY_IMAGE_TAG", "pi-coding-agent-proxy:local")

# The Containerfile unconditionally does `COPY --from=root_commands_path`, so
# every build must supply that context. Project-specific builds point it at the
# workspace's own root/commands.sh; the shared image uses this no-op default.
DEFAULT_ROOT_COMMANDS = REPO_ROOT / "pi-coding-agent" / "default" / "dependencies" / "root" / "commands.sh"


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
    from datetime import UTC, datetime

    build_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info(f"Building proxy image ({runtime}): {PROXY_IMAGE_TAG}")
    _run_command_with_logging(
        [
            runtime,
            "build",
            "--label",
            f"pi-container.build.time={build_ts}",
            "--label",
            "pi-container.type=shared",
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
            "--build-context",
            f"root_commands_path={DEFAULT_ROOT_COMMANDS.parent}",
            "--build-arg",
            f"ROOT_COMMANDS_PATH={DEFAULT_ROOT_COMMANDS.name}",
            "--tag",
            PI_IMAGE_TAG,
            "--file",
            str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
            str(REPO_ROOT),
        ],
    )


def build_project_image(
    runtime: str,
    root_commands_path: str,
    pi_commands_path: str,
    image_tag: str,
    label_hash: str,
    project_hash: str = "",
    project_path: str = "",
    build_timestamp: str = "",
) -> None:
    """Build a project-specific agent image with baked-in command scripts.

    Args:
        runtime: Container runtime (docker or podman).
        root_commands_path: Absolute path to root/commands.sh on the host.
        pi_commands_path: Absolute path to pi/commands.sh on the host.
        image_tag: Image tag for the project-specific image
            (e.g., "pi-container-project-<hash>-<hash>.local").
        label_hash: Content hash to store in the image label for cache invalidation.
        project_hash: Project identity hash → set as pi-container.project.hash label.
        project_path: Absolute project directory path → set as pi-container.project.path label.
        build_timestamp: ISO 8601 timestamp → set as pi-container.build.time label.
    """
    logger.info(f"Building project-specific agent image ({runtime}): {image_tag}")
    cmd = [
        runtime,
        "build",
        "--build-context",
        f"root_commands_path={Path(root_commands_path).parent}",
        "--build-arg",
        f"ROOT_COMMANDS_PATH={Path(root_commands_path).name}",
        "--build-arg",
        f"LABEL_HASH={label_hash}",
    ]
    if project_hash:
        cmd += ["--build-arg", f"PROJECT_HASH={project_hash}"]
    if project_path:
        cmd += ["--build-arg", f"PROJECT_PATH={project_path}"]
    if build_timestamp:
        cmd += ["--build-arg", f"BUILD_TIMESTAMP={build_timestamp}"]
    cmd += [
        "--label",
        "pi-container.type=project",
        "--tag",
        image_tag,
        "--file",
        str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
        str(REPO_ROOT),
    ]
    _run_command_with_logging(cmd)


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

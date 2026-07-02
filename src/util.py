import sys

sys.dont_write_bytecode = True

import errno
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path


def load_dotenv(dotenv_path: Path):
    if not dotenv_path.exists():
        return
    with open(dotenv_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


class EnvironmentError(Exception):
    """Raised when the environment does not meet requirements."""

    pass


def validate_environment(llama_bin: str | None) -> str:
    if llama_bin is None or not Path(llama_bin).exists():
        raise EnvironmentError("llama-server not found. Please install it or set LLAMA_BIN.")

    if shutil.which("hf") is None:
        raise EnvironmentError("hf not found. Install via: pip install huggingface_hub[cli]")

    if shutil.which("socat") is None:
        raise EnvironmentError("socat not found. Install via: brew install socat (macOS) or apt install socat (Linux)")

    # Check for explicit CONTAINER_RUNTIME from .env first
    explicit_runtime = os.environ.get("CONTAINER_RUNTIME", "").strip()
    supported_runtimes = ("container", "docker", "podman")

    if explicit_runtime:
        if explicit_runtime not in supported_runtimes:
            raise EnvironmentError(
                f"Unsupported CONTAINER_RUNTIME '{explicit_runtime}'. "
                f"Supported values: {', '.join(supported_runtimes)}."
            )
        return explicit_runtime

    # Fall back to auto-detection
    runtime: str | None = None
    for candidate in supported_runtimes:
        if shutil.which(candidate) is not None:
            runtime = candidate
            break

    if runtime is None:
        raise EnvironmentError("No supported container runtime found (container, docker or podman).")

    return runtime


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def handle_signal(signum: int, logger) -> None:
    signame: str = signal.Signals(signum).name
    logger.info(f"Received {signame}. Initiating clean shutdown...")
    raise SystemExit


def stop_process_group(pid: int, name: str, logger) -> None:
    """Stops a process group to ensure all child processes are killed."""
    logger.info(f"Stopping process group for {name} (pid: {pid})...")
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)

        for _ in range(10):
            try:
                os.killpg(pgid, 0)
                time.sleep(0.5)
            except OSError as e:
                if e.errno in (errno.ESRCH, errno.EPERM):
                    break
                raise
        else:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as e:
        if e.errno != errno.ESRCH:
            logger.error(f"Error stopping process group for {name}: {e}")


def get_sanitized_git_config_json(logger):
    """
    Generates a JSON-serialized dictionary of 'key': 'value' pairs.
    """
    sanitized_dict = {}
    # Regex to strip 'user:pass@' from URLs
    url_credential_regex = re.compile(r"(https?://)[^/]+:[^/@]+@")

    try:
        result = subprocess.check_output(["git", "config", "--list", "--show-origin"], text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running git config: {e}")
        return "{}"
    except FileNotFoundError:
        logger.error("'git' command not found on host.")
        return "{}"

    for line in result.splitlines():
        try:
            pattern = r"^(.+?)\t([^=]+)=(.+)$"
            m = re.match(pattern, line)
            if m:
                origin, key, value = m.group(1), m.group(2), m.group(3)

                if origin in "file:.git/config":
                    continue

                key = key.strip()
                value = value.strip()

                if key.startswith("credential."):
                    continue

                value = url_credential_regex.sub(r"\1", value)

                sanitized_dict[key] = value

        except Exception as e:
            logger.error(f"git: {e}")
            continue

    return json.dumps(sanitized_dict)

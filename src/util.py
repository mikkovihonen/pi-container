import sys

sys.dont_write_bytecode = True

import errno
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

_LOG = logging.getLogger(__name__)


def run_quiet(
    cmd: list[str],
    *,
    check: bool = True,
    label: str | None = None,
    logger: logging.Logger | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run a command quietly on success but surface the reason on failure.

    Drop-in replacement for the ``subprocess.run(cmd, stdout=DEVNULL,
    stderr=DEVNULL)`` fire-and-forget pattern, which hides *why* a command
    failed. Output is captured instead of discarded; on a non-zero exit the
    captured stderr (or stdout) is logged, and — unless ``check=False`` — a
    ``CalledProcessError`` is raised so the failure cannot pass silently.

    Args:
        cmd: The command argv.
        check: If True (default), log the failure and raise
            ``CalledProcessError`` on non-zero exit. If False, log a warning
            and return anyway — for teardown/cleanup where a failed command
            should not abort the caller.
        label: Human-readable name for the command in messages. Defaults to the
            executable (``cmd[0]``). Also used in place of the raw argv in the
            raised exception, so secrets passed on the command line (e.g.
            ``ADMIN_PASSWORD``) never leak into tracebacks/logs.
        logger: Logger to emit failure messages to (defaults to this module's).
        **kwargs: Passed through to ``subprocess.run`` (e.g. ``timeout``).

    Returns:
        The ``CompletedProcess`` (with captured ``stdout``/``stderr``).
    """
    log = logger if logger is not None else _LOG
    name = label or (cmd[0] if cmd else "command")
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        msg = f"{name} failed (exit {result.returncode})"
        if detail:
            msg += f": {detail}"
        if check:
            log.error(msg)
            # Raise with ``name`` (not the raw argv) so command-line secrets
            # are not embedded in the exception string.
            raise subprocess.CalledProcessError(result.returncode, name, output=result.stdout, stderr=result.stderr)
        log.warning(msg)
    return result


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

    # Check for explicit CONTAINER_RUNTIME from .env first
    explicit_runtime = os.environ.get("CONTAINER_RUNTIME", "").strip()
    supported_runtimes = ("docker", "podman")

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
        raise EnvironmentError("No supported container runtime found (docker or podman).")

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


def extract_ipv4_from_ip_addr(output: str) -> str | None:
    """Extract the first IPv4 address from ``ip addr show`` output.

    Matches ``inet 1.2.3.4/n`` — the standard format for both ``ip addr`` and
    ``ifconfig`` output on Linux. Returns None if no match is found.
    """
    import re as _re

    match = _re.search(r"inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+", output)
    return match.group(1) if match else None


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

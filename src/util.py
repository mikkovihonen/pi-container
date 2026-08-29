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
    """Execute a subprocess command, capturing output and logging failures.

    Args:
        cmd: Command argv to execute.
        check: If True (default), log failure and raise CalledProcessError on non-zero exit.
        label: Display name for the command in error logs (hides raw command secrets).
        logger: Logger to receive failure messages (defaults to module logger).
        **kwargs: Additional keyword arguments passed to subprocess.run.

    Returns:
        CompletedProcess instance with captured stdout and stderr.
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
    """Load environment variables from a .env file into os.environ."""
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


def validate_environment(llama_bin: str | None) -> str:
    """Validate host prerequisites (llama-server, hf CLI, podman) and return the runtime name."""
    if llama_bin is None or not Path(llama_bin).exists():
        raise EnvironmentError("llama-server not found. Please install it or set LLAMA_BIN.")

    if shutil.which("hf") is None:
        raise EnvironmentError("hf not found. Install via: pip install huggingface_hub[cli]")

    # Podman is the only supported runtime.
    runtime: str | None = None
    if shutil.which("podman") is not None:
        runtime = "podman"

    if runtime is None:
        raise EnvironmentError("podman not found. Install it (macOS: brew install podman).")

    return runtime


def get_free_port() -> int:
    """Bind to an ephemeral port on 127.0.0.1, close the socket, and return the free port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running on the host.

    Returns False if pid <= 0 or if no such process exists.
    Returns True if the process exists (even if we lack permission to signal it).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        return e.errno == errno.EPERM


def read_live_clients(clients_file: Path) -> list[dict]:
    """Read a JSON client manifest and return only clients whose PIDs are alive.

    The manifest is expected to be a JSON array of dicts, each with at least a
    'pid' key (int). Dead clients are automatically filtered out.
    """
    if not clients_file.exists():
        return []
    try:
        data = json.loads(clients_file.read_text())
        if not isinstance(data, list):
            return []
        live = []
        for entry in data:
            if isinstance(entry, dict) and "pid" in entry:
                pid = entry["pid"]
                if isinstance(pid, int) and is_pid_alive(pid):
                    live.append(entry)
        return live
    except OSError, json.JSONDecodeError:
        return []


def write_clients(clients_file: Path, clients: list[dict]) -> None:
    """Write the client list as JSON to clients_file."""
    clients_file.parent.mkdir(parents=True, exist_ok=True)
    clients_file.write_text(json.dumps(clients, indent=2) + "\n")


def register_client(clients_file: Path, client_info: dict) -> list[dict]:
    """Prune dead clients, add `client_info` to clients_file, and return the live list."""
    live = read_live_clients(clients_file)
    pid = client_info.get("pid")
    run_id = client_info.get("run_id")
    live = [c for c in live if not (c.get("pid") == pid and (run_id is None or c.get("run_id") == run_id))]
    live.append(client_info)
    write_clients(clients_file, live)
    return live


def deregister_client(clients_file: Path, pid: int, run_id: str | None = None) -> list[dict]:
    """Remove client matching pid (and optionally run_id), prune dead clients, and update file."""
    live = read_live_clients(clients_file)
    remaining = [c for c in live if not (c.get("pid") == pid and (run_id is None or c.get("run_id") == run_id))]
    if remaining:
        write_clients(clients_file, remaining)
    else:
        clients_file.unlink(missing_ok=True)
    return remaining


def get_ref_count(clients_file: Path, ref_count_file: Path) -> int:
    """Return the number of active clients or fall back to ref_count_file."""
    live = read_live_clients(clients_file)
    if live:
        return len(live)
    if ref_count_file.exists():
        try:
            return int(ref_count_file.read_text().strip())
        except ValueError:
            return 0
    return 0


def handle_signal(signum: int, frame: object | None = None, logger: logging.Logger | None = None) -> None:
    """Handle termination signals (SIGINT, SIGTERM, SIGHUP, SIGQUIT).

    Compatible with Python signal handler callbacks ``(signum, frame)`` as well
    as explicit logger injections in tests.
    """
    log = logger
    if log is None:
        log = frame if hasattr(frame, "info") and callable(frame.info) else _LOG
    try:
        signame: str = signal.Signals(signum).name
    except ValueError:
        signame = f"signal {signum}"
    log.info(f"Received {signame}. Initiating clean shutdown...")
    raise SystemExit(128 + signum)


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
    """Extract global host git configuration as JSON, stripping repository-local settings and credentials."""
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

                if origin.endswith(("/.git/config", ".git/config")) or origin == "file:.git/config":
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


REQUIRED_GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".pi-container/agent/bin",
    ".pi-container/agent/sessions",
    ".pi-container/agent/trust.json",
    ".pi-container/agent/models-store.json",
    ".pi-container/exports/",
    ".pi-container/agent/sessions/",
)


def check_repo_gitignore(project_dir: Path) -> list[str]:
    """Check if the git repository containing project_dir ignores required pi-container paths.

    Returns a list of missing required gitignore entries, or an empty list if
    all are present or if project_dir is not in a git repository.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return []
        git_root = Path(res.stdout.strip())
    except Exception:
        return []

    gitignore_file = git_root / ".gitignore"
    existing_lines: set[str] = set()
    if gitignore_file.is_file():
        for raw_line in gitignore_file.read_text().splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if line:
                existing_lines.add(line.strip("/"))

    missing: list[str] = []
    for entry in REQUIRED_GITIGNORE_ENTRIES:
        norm = entry.strip().strip("/")
        if norm in existing_lines:
            continue
        try:
            check = subprocess.run(
                ["git", "-C", str(project_dir), "check-ignore", "-q", entry],
                capture_output=True,
                check=False,
            )
            if check.returncode == 0:
                continue
        except Exception:
            pass
        if entry not in missing:
            missing.append(entry)

    return missing


def warn_missing_gitignore(project_dir: Path, logger: logging.Logger) -> None:
    """Log a warning if recommended .gitignore entries are missing from the repository."""
    missing = check_repo_gitignore(project_dir)
    if missing:
        logger.warning(
            "Recommended .gitignore entries are missing from this repository:\n"
            + "\n".join(f"  {entry}" for entry in missing)
            + "\nAdd them to .gitignore to prevent committing session tokens, trust state, or proxy flow captures."
        )

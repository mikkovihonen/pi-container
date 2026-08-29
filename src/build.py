import logging
import os
import re
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
# The toolchain image (CPython, uv, podman-compose, podman, netavark, aardvark-dns).
# Both the shared agent image and every project-specific image COPY --from this tag,
# so it is referenced by name inside pi-coding-agent/Containerfile too; changing it
# here alone is not enough.
BUILDER_IMAGE_TAG = os.environ.get("BUILDER_IMAGE_TAG", "pi-coding-agent-builder:local")

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


# ─── Build-memory preflight ──────────────────────────────────────────────────
#
# The builder image compiles CPython (pi-coding-agent-builder/build-python.sh). A
# PGO compile job peaks near 900 MiB, and the podman machine defaults to 2 GiB total
# regardless of core count — so on a busy VM the build dies minutes in with
#   gcc: fatal error: Killed signal terminated program cc1
# The build script refuses to start for that reason, but by then the build is already
# running. This check happens BEFORE the build is launched, so the answer arrives
# immediately.
#
# Thresholds mirror MIB_PER_JOB in pi-coding-agent-builder/build-python.sh — keep
# them in step.
_MIB_PER_JOB_PGO = 900
_MIB_PER_JOB_PLAIN = 400


def _python_optimize_enabled() -> bool:
    """Check if Python PGO profile optimization is enabled via PYTHON_OPTIMIZE."""
    return os.environ.get("PYTHON_OPTIMIZE", "1").strip() not in ("0", "false", "no", "off")


def read_available_memory_mib(runtime: str) -> int | None:
    """Read available system memory in MiB on the host or inside the Podman VM."""
    local_meminfo = Path("/proc/meminfo")
    if local_meminfo.exists():
        return _parse_mem_available_mib(local_meminfo.read_text())

    try:
        result = subprocess.run(
            [runtime, "machine", "ssh", "cat /proc/meminfo"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as e:
        logger.debug(f"Could not read build-VM memory: {e}")
        return None
    if result.returncode != 0:
        logger.debug(f"Could not read build-VM memory: {result.stderr.strip()}")
        return None
    return _parse_mem_available_mib(result.stdout)


def _parse_mem_available_mib(meminfo: str) -> int | None:
    """Extract ``MemAvailable`` from /proc/meminfo text, as MiB."""
    match = re.search(r"^MemAvailable:\s+(\d+)\s*kB", meminfo, re.MULTILINE)
    return int(match.group(1)) // 1024 if match else None


def check_build_memory(runtime: str, fatal: bool = True) -> bool:
    """Verify that sufficient memory is available to compile CPython in the builder container."""
    if os.environ.get("PI_MEMORY_PREFLIGHT", "1").strip() in ("0", "false", "no", "off"):
        logger.debug("Build-memory preflight disabled (PI_MEMORY_PREFLIGHT=0).")
        return True

    required = _MIB_PER_JOB_PGO if _python_optimize_enabled() else _MIB_PER_JOB_PLAIN
    available = read_available_memory_mib(runtime)
    if available is None:
        logger.debug("Could not determine available build memory; proceeding.")
        return True
    if available >= required:
        logger.info(f"Build memory: {available} MiB available (need ~{required} MiB to compile Python).")
        return True

    log = logger.error if fatal else logger.warning
    log(
        f"Only {available} MiB of memory is available where images are built, but compiling "
        f"Python needs ~{required} MiB for a single compile job. "
        f"{'Not starting the build' if fatal else 'Continuing anyway'} — it would be OOM-killed "
        f"(gcc: fatal error: Killed signal terminated program cc1)."
    )
    log(
        "Fix one of:\n"
        "  * give the VM more memory:  podman machine stop && podman machine set --memory 4096 && podman machine start\n"
        "  * free memory in the VM:    stop containers you are not using (podman ps)\n"
        "  * skip the PGO build:       PYTHON_OPTIMIZE=0 ./build.sh  (~10-20% slower Python)\n"
        "  * already-cached Python:    PI_MEMORY_PREFLIGHT=0 ./build.sh  (skips this check)"
    )
    return not fatal


def _python_build_args() -> list[str]:
    """Generate ``--build-arg`` flags passing ``PYTHON_OPTIMIZE`` settings to container build."""
    value = os.environ.get("PYTHON_OPTIMIZE", "").strip()
    return ["--build-arg", f"PYTHON_OPTIMIZE={'1' if _python_optimize_enabled() else '0'}"] if value else []


_NODE_SOURCES = ("prebuilt", "build")


def _node_build_args() -> list[str]:
    """Generate ``--build-arg`` flags specifying the Node.js installation source."""
    value = os.environ.get("NODE_SOURCE", "").strip().lower()
    if not value:
        return []
    if value not in _NODE_SOURCES:
        raise EnvironmentError(f"NODE_SOURCE must be one of {', '.join(_NODE_SOURCES)} (got '{value}').")
    return ["--build-arg", f"NODE_SOURCE={value}"]


def build_builder(runtime: str) -> None:
    """Build the base builder image containing CPython, Node.js, and container tools."""
    from datetime import UTC, datetime

    build_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info(f"Building toolchain image ({runtime}): {BUILDER_IMAGE_TAG}")
    if os.environ.get("NODE_SOURCE", "").strip().lower() == "build":
        logger.info(
            "NODE_SOURCE=build: Node is compiled from source, which takes roughly an hour "
            "on a 9-core/8 GB podman machine (and longer on 4 GB, where it is limited to "
            "one compile job). Unset NODE_SOURCE to stage the official tarball instead."
        )
    else:
        logger.info("This compiles CPython, podman and netavark from source; expect several minutes.")
    _run_command_with_logging(
        [
            runtime,
            "build",
            *_python_build_args(),
            *_node_build_args(),
            "--label",
            f"pi-container.build.time={build_ts}",
            "--label",
            "pi-container.type=shared",
            "--tag",
            BUILDER_IMAGE_TAG,
            "--file",
            str(REPO_ROOT / "pi-coding-agent-builder" / "Containerfile"),
            str(REPO_ROOT),
        ],
    )


def build_proxy(runtime: str) -> None:
    """Build the mitmproxy container image for network egress inspection and rewriting."""
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
    """Build the shared default agent container image."""
    logger.info(f"Building agent image ({runtime}): {PI_IMAGE_TAG}")
    _run_command_with_logging(
        [
            runtime,
            "build",
            "--build-context",
            f"root_commands_path={DEFAULT_ROOT_COMMANDS.parent}",
            "--build-arg",
            f"ROOT_COMMANDS_PATH={DEFAULT_ROOT_COMMANDS.name}",
            "--label",
            "pi-container.type=shared",
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
        # Reject a bad NODE_SOURCE before any build starts. build_builder() would raise
        # on it too, but only after the memory preflight has run and podman has begun
        # resolving the build context — and a typo should cost nothing at all.
        _node_build_args()
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)

    # Notify BEFORE launching a build that cannot finish: the toolchain image
    # compiles CPython, and an undersized/busy builder OOM-kills the compiler
    # minutes in.
    if not check_build_memory(runtime):
        sys.exit(1)

    try:
        # Strict dependency order, not a preference: the proxy image COPYs its CPython
        # and uv from the toolchain image, and the agent image COPYs from both. The
        # proxy used to be built first (it is the quick one, and a failure there was
        # cheaper), but that stopped being possible once it stopped carrying its own
        # interpreter.
        build_builder(runtime)
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

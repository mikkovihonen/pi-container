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
    """Whether the PGO CPython build is on (``PYTHON_OPTIMIZE``, default yes)."""
    return os.environ.get("PYTHON_OPTIMIZE", "1").strip() not in ("0", "false", "no", "off")


def read_available_memory_mib(runtime: str) -> int | None:
    """Available memory (MiB) where the image is actually built, or None if unknown.

    Reads ``MemAvailable`` rather than ``MemFree``: reclaimable page cache counts,
    and the two differ by an order of magnitude on a warm builder (measured on
    this host: free 43 MiB vs available 295 MiB).

    Native Linux builds in this kernel, so ``/proc/meminfo`` is the right source.
    On macOS/Windows podman builds inside its VM, so the VM is asked instead —
    the host's own free memory is irrelevant there.
    """
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
    """Report whether the builder has room to compile CPython.

    Returns True when the build should go ahead. When there is not enough memory,
    logs what to do about it and — if ``fatal`` — returns False so the caller can
    stop before launching a build that cannot finish.

    Skipped entirely by ``PI_MEMORY_PREFLIGHT=0``, which is the escape hatch when
    the Python layer is already in the build cache (no compile, so no memory
    needed) and this check would only get in the way.
    """
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
    """``--build-arg`` pair forwarding ``PYTHON_OPTIMIZE`` to the builder image.

    Only emitted when the env var is set, so the Containerfile's own default (PGO
    on) stays the single source of truth otherwise.
    """
    value = os.environ.get("PYTHON_OPTIMIZE", "").strip()
    return ["--build-arg", f"PYTHON_OPTIMIZE={'1' if _python_optimize_enabled() else '0'}"] if value else []


_NODE_SOURCES = ("prebuilt", "build")


def _node_build_args() -> list[str]:
    """``--build-arg`` pair forwarding ``NODE_SOURCE`` to the builder image.

    ``prebuilt`` (the Containerfile's default) stages the official nodejs.org tarball;
    ``build`` compiles Node from source, which costs about an hour. Only emitted when
    the env var is set, so the Containerfile stays the single source of truth otherwise.

    An unrecognised value is rejected here rather than passed through: the build script
    would fail on it anyway, but only after the proxy image had already been rebuilt.
    """
    value = os.environ.get("NODE_SOURCE", "").strip().lower()
    if not value:
        return []
    if value not in _NODE_SOURCES:
        raise EnvironmentError(f"NODE_SOURCE must be one of {', '.join(_NODE_SOURCES)} (got '{value}').")
    return ["--build-arg", f"NODE_SOURCE={value}"]


def build_builder(runtime: str) -> None:
    """Build the toolchain image the agent images copy their toolchain from.

    The slow one: it compiles CPython, podman and the netavark/aardvark-dns pair from
    source, and Node too when ``NODE_SOURCE=build`` (see
    pi-coding-agent-builder/Containerfile for why each is not simply apt-installed). It
    is built only by build.sh — a project-specific image rebuild reuses the tag as-is.

    Carries the same ``pi-container.build.time`` label as the proxy image, because
    run.py compares project-image timestamps against both: a project image built
    before this one holds an older toolchain.
    """
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
    """Build the shared base agent image.

    Labelled ``pi-container.type=shared``, not ``project``: this image belongs to no
    project, so it must stay out of run.py's project-image cleanup passes. Nothing
    reclaims ``type=shared`` images — they are rebuilt only by build.sh.
    """
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
        # Reject a bad NODE_SOURCE here, not where it is used. build_builder() would
        # raise on it too, but only after build_proxy() has already regenerated the
        # mitmproxy CA — which invalidates every project image and forces a round of
        # rebuilds the user did not ask for.
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
        # Proxy first: it is the quick one, and a failure there (or a missing
        # mitmproxy dependency) should not cost a full toolchain build. The agent
        # image COPYs from both of the images built before it.
        build_proxy(runtime)
        build_builder(runtime)
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

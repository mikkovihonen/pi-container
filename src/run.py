import sys

sys.dont_write_bytecode = True

import hashlib
import json
import logging
import re
import shutil
import signal
import subprocess
import threading
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from build import build_project_image
from config import (
    ADMIN_PASSWORD,
    BRIDGE_INTERFACE_ENV,
    IMAGE_TAG,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from config_schema import validate_config, validate_models, validate_project_yaml
from flow_export import export_mitmweb_flows, poll_agent_container_ips
from models import Model, ServerConfig
from network import (
    ContainerNetworkManager,
    read_agent_extras,
    read_flow_export_enabled,
    read_llama_config,
    read_nested_containers_config,
    read_network_config,
    read_resource_limits,
    resource_limit_args,
    scan_tmpfs_paths,
)
from runtimes import ContainerRuntime
from server import Server
from util import (
    EnvironmentError,
    extract_ipv4_from_ip_addr,
    get_sanitized_git_config_json,
    handle_signal,
    validate_environment,
)

logger = logging.getLogger(__name__)


# ─── Startup validation (deferred: only run when this is the entrypoint, not when imported by tests) ──


def _init_runtime() -> None:
    """Validate environment and create the runtime instance.

    Called only from ``if __name__ == \"__main__\"`` so that test imports of
    this module do not trigger subprocess calls or environment checks.
    """
    if not ADMIN_PASSWORD or ADMIN_PASSWORD == "CHANGEME":
        logger.error(
            "ERROR: ADMIN_PASSWORD must be set to a non-default value. Update .env with a strong password before running."
        )
        sys.exit(1)

    try:
        _CONTAINER_RUNTIME = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)

    global CONTAINER_RUNTIME, RUNTIME, BRIDGE_INTERFACE, PROXY_UPSTREAM_NETWORK
    RUNTIME = ContainerRuntime.create(
        _CONTAINER_RUNTIME,
        bridge_interface=BRIDGE_INTERFACE_ENV,
        upstream_network=PROXY_UPSTREAM_NETWORK_ENV,
    )
    CONTAINER_RUNTIME = _CONTAINER_RUNTIME
    BRIDGE_INTERFACE = RUNTIME.bridge_interface
    PROXY_UPSTREAM_NETWORK = RUNTIME.upstream_network


# ─── Per-project configuration ───────────────────────────────────────────────


# Project-level config seeded into ``{PROJECT_DIR}/.pi-container``. Each is
# per-project: ``config.yaml`` holds orchestration settings (resource limits,
# tmpfs paths, flow-export toggle, egress policy); the proxy mounts its own
# allowlist/token_replacer; and llama-server loads chat templates from the
# workspace's own copy (models.json flags reference ``.pi-container/chat-templates/...``
# relative to the launch dir). config.yaml's tmpfs list ships empty on purpose —
# seeding the repo's own paths would create those dirs in every foreign workspace.
_PROJECT_CONFIG_DIRS = ("agent", "chat-templates")
_PROJECT_CONFIG_FILES = ("config.yaml", "allowlist.yaml", "token_replacer.yaml")


def _project_key(project_dir: Path) -> str:
    """The 10-hex-char identity of a workspace: a hash of its absolute path.

    Every per-workspace resource name is derived from it — the proxy container,
    the isolated network, the project-specific image tag, and the nested-container
    image-store volume — so repeated (or concurrent) runs from the same workspace
    always resolve to the same set.
    """
    return hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:10]


def _project_scope(project_dir: Path) -> tuple[str, str]:
    """Return ``(proxy_name, network_name)`` unique to this workspace.

    Keyed by :func:`_project_key` so each workspace gets its own isolated network
    + proxy container, while repeated (or concurrent) runs from the same workspace
    resolve to the same pair and share it via refcount.
    """
    key = _project_key(project_dir)
    return f"pi-proxy-{key}", f"pi-isolated-net-{key}"


def _ensure_project_config() -> Path:
    """Seed the per-project ``.pi-container`` config from the repo template if absent.

    Seeds ``{PROJECT_DIR}/.pi-container`` from ``{REPO_ROOT}/pi-coding-agent/default``:
    the ``agent/`` and ``chat-templates/`` subtrees plus ``config.yaml`` and the proxy
    addon configs ``allowlist.yaml``/``token_replacer.yaml``. It also seeds
    ``entrypoint.sh`` into ``.pi-container/agent/`` so the container's entrypoint can
    invoke a user-customizable script before ``pi`` starts. Each item is only seeded
    when missing, so existing (user-edited) files are never overwritten and a
    partially-populated ``.pi-container`` is completed.

    Returns the agent launch-config dir (``{PROJECT_DIR}/.pi-container/agent``).
    """
    template_root = REPO_ROOT / "pi-coding-agent" / "default"
    if not template_root.is_dir():
        raise FileNotFoundError(f"Project config template not found: {template_root}")

    project_root = PROJECT_DIR / ".pi-container"

    for name in _PROJECT_CONFIG_DIRS:
        src, dst = template_root / name, project_root / name
        if src.is_dir() and not dst.exists():
            logger.info(f"Seeding {dst} from {src}.")
            shutil.copytree(src, dst)

    for name in _PROJECT_CONFIG_FILES:
        src, dst = template_root / name, project_root / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Seeding {dst} from {src}.")
            shutil.copy2(src, dst)

    # Seed entrypoint.sh into {PROJECT_DIR}/.pi-container/agent/ so it gets
    # bind-mounted to /home/pi/.pi/agent/ inside the container. The container's
    # own entrypoint.sh (built into the image) calls /home/pi/.pi/agent/entrypoint.sh
    # before launching pi; seeding the template gives users a working copy to
    # customize without editing the repo or losing changes across rebuilds.
    ep_src = template_root / "entrypoint.sh"
    ep_dst = project_root / "agent" / "entrypoint.sh"
    if ep_src.exists() and not ep_dst.exists():
        logger.info(f"Seeding {ep_dst} from {ep_src}.")
        shutil.copy2(ep_src, ep_dst)

    # Seed dependency definition files (root/commands.sh and pi/commands.sh) into
    # {PROJECT_DIR}/.pi-container/dependencies/. These define project-specific setup
    # that gets baked into the project-specific image at build time. Both files are
    # optional — if absent or empty, the workspace uses the shared base image.
    deps_template_root = template_root / "dependencies"
    deps_project_root = project_root / "dependencies"
    if deps_template_root.is_dir():
        for cmd_dir in ("root", "pi"):
            src_dir = deps_template_root / cmd_dir
            dst_dir = deps_project_root / cmd_dir
            if src_dir.is_dir() and not dst_dir.exists():
                logger.info(f"Seeding {dst_dir} from {src_dir}.")
                shutil.copytree(src_dir, dst_dir)

    return project_root / "agent"


# ─── Project-specific image resolution ─────────────────────────────────────

# Files under ``pi-coding-agent/`` that define the image itself. A change to any
# of them must invalidate every project-specific image, so they are always part
# of the content hash — unlike the per-workspace ``commands.sh`` files, which are
# only hashed when non-empty.
_IMAGE_DEFINITION_FILES = ("Containerfile", "entrypoint.sh")


def _compute_image_hash(project_dir: Path) -> str | None:
    """Compute a content hash of all files that affect the project-specific image.

    Returns a hex digest of the concatenated SHA-256 hashes of:
    - `pi-coding-agent/Containerfile` (at REPO_ROOT, to detect image definition changes)
    - `pi-coding-agent/entrypoint.sh` (at REPO_ROOT, to detect entrypoint changes)
    - `.pi-container/dependencies/root/commands.sh` (if it exists and is non-empty)
    - `.pi-container/dependencies/pi/commands.sh` (if it exists and is non-empty)

    The toolchain (`pi-coding-agent-builder/`) is deliberately NOT hashed. The
    project image consumes it as a built image, not as source, so what matters is
    when that image was last built — see ``_newest_shared_image_time()``.

    Returns None if no definition files exist (all dependency files are absent or
    empty, and the image definition files are absent at REPO_ROOT).
    """
    deps_root = project_dir / ".pi-container" / "dependencies"
    agent_dir = REPO_ROOT / "pi-coding-agent"
    files_to_hash = []

    # Image definition files (always included — their changes require a rebuild)
    for img_file in _IMAGE_DEFINITION_FILES:
        img_path = agent_dir / img_file
        if img_path.exists():
            files_to_hash.append(img_file)

    # Dependency files (optional — skip if absent or empty)
    for cmd_file in ("root/commands.sh", "pi/commands.sh"):
        cmd_path = deps_root / cmd_file
        if cmd_path.exists() and cmd_path.stat().st_size > 0:
            files_to_hash.append(cmd_file)

    if not files_to_hash:
        return None

    # Sort for deterministic ordering
    files_to_hash.sort()

    # Concatenate SHA-256 hashes of each file
    combined_hash = hashlib.sha256()
    for cmd_file in files_to_hash:
        file_path = agent_dir / cmd_file if cmd_file in _IMAGE_DEFINITION_FILES else deps_root / cmd_file
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        combined_hash.update(file_hash.encode())

    return combined_hash.hexdigest()[:16]  # Truncate to 16 hex chars


def _has_dependency_files(project_dir: Path) -> bool:
    """Check if the project has non-empty dependency definition files.

    Returns True if root/commands.sh or pi/commands.sh exists and is non-empty.
    """
    deps_root = project_dir / ".pi-container" / "dependencies"
    for cmd_file in ("root/commands.sh", "pi/commands.sh"):
        cmd_path = deps_root / cmd_file
        if cmd_path.exists() and cmd_path.stat().st_size > 0:
            return True
    return False


def _resolve_agent_image(project_dir: Path) -> tuple[str, bool]:
    """Resolve the agent image tag for this workspace.

    If dependency files exist and are non-empty, returns a project-specific image
    tag (e.g., "pi-container-project-<hash>-<hash>.local"). Otherwise, returns
    the shared image tag (IMAGE_TAG).

    The hash includes Containerfile and entrypoint.sh to detect image definition
    changes, but the decision to use a project-specific image is based only on
    whether dependency files exist.

    The tag includes the project's identity hash so images for different
    workspaces never collide, and so stale images can be discovered and removed
    per-project.

    Returns:
        Tuple of (image_tag, is_project_specific).
    """
    if not _has_dependency_files(project_dir):
        return IMAGE_TAG, False

    project_hash, _ = _project_scope(project_dir)
    # _project_scope returns "pi-proxy-<key>" — extract just the 10-char key.
    key = project_hash.split("pi-proxy-")[1]
    image_hash = _compute_image_hash(project_dir)
    project_image_tag = f"pi-container-project-{key}-{image_hash}.local"
    return project_image_tag, True


def _get_image_label(image_tag: str, label_key: str) -> str | None:
    """Read a label from a container image.

    Args:
        image_tag: Image tag to inspect.
        label_key: Label key to read (e.g., "pi-container.hash").

    Returns:
        The label value as a string, or None if the image/label doesn't exist.
    """
    try:
        # Try format string first (works for docker and podman)
        result = subprocess.run(
            [
                CONTAINER_RUNTIME,
                "image",
                "inspect",
                image_tag,
                "--format",
                f'{{{{index .Config.Labels "{label_key}"}}}}',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        # Fall back to JSON output and parse
        result = subprocess.run(
            [CONTAINER_RUNTIME, "image", "inspect", image_tag],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json

            data = json.loads(result.stdout)
            if data and isinstance(data, list) and len(data) > 0:
                labels = data[0].get("Config", {}).get("Labels", {})
                if labels and label_key in labels:
                    return labels[label_key]
    except Exception:
        pass

    return None


def _image_exists(image_tag: str) -> bool:
    """Return True if ``image_tag`` is present in the local image store.

    Used to distinguish "no image yet, build it" from "image is there but
    unreadable/unlabeled", which need different handling.
    """
    try:
        result = subprocess.run(
            [CONTAINER_RUNTIME, "image", "inspect", image_tag],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_image_build_time(image_tag: str) -> datetime | None:
    """Read the pi-container.build.time label from a container image.

    Returns:
        A timezone-aware UTC datetime, or None if the label is absent or the
        image could not be inspected.
    """
    try:
        ts_str = _get_image_label(image_tag, "pi-container.build.time")
    except Exception:
        logger.warning(f"Could not read build time from image {image_tag}")
        return None
    if ts_str is None:
        return None
    try:
        # ISO 8601 format: "2025-01-15T12:30:00Z"
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        logger.warning(f"Could not parse build time '{ts_str}' from image {image_tag}")
        return None


# Shared images a project-specific image copies content out of at build time:
#   the proxy      → the mitmproxy CA certificate baked into its trust store
#   the toolchain  → CPython, uv, podman-compose, podman, netavark, aardvark-dns
# A project image older than either of them is carrying a stale copy.
_SHARED_SOURCE_IMAGES = ("pi-coding-agent-proxy:local", "pi-coding-agent-builder:local")


def _newest_shared_image_time() -> tuple[str, datetime] | None:
    """Most recent build time among the images project images copy content from.

    Returns (image_tag, timestamp) for whichever is newest, or None if any of them
    cannot be dated — in which case a project image's contents cannot be judged
    and the caller should stop rather than run something stale.
    """
    newest: tuple[str, datetime] | None = None
    for tag in _SHARED_SOURCE_IMAGES:
        ts = _get_image_build_time(tag)
        if ts is None:
            logger.error(
                f"ERROR: Could not read build timestamp from shared image ({tag}). "
                f"The project image may hold a stale mitmproxy CA certificate or "
                f"toolchain. Rebuild with: build.sh"
            )
            return None
        if newest is None or ts > newest[1]:
            newest = (tag, ts)
    return newest


def _image_is_current(project_dir: Path, image_tag: str, current_hash: str | None) -> bool:
    """Check if the project-specific image is up-to-date by comparing labels.

    For project-specific images, compares the stored `pi-container.hash` label
    with the current content hash of the definition files.

    For the shared image, always returns True (it's rebuilt via build.sh).

    Returns:
        True if the image is current, False otherwise.
    """
    _, is_project_specific = _resolve_agent_image(project_dir)
    if not is_project_specific:
        return True  # Shared image — trust the tag

    # Check if the image exists
    stored_hash = _get_image_label(image_tag, "pi-container.hash")
    if stored_hash is None:
        return False  # Image doesn't exist or has no label — need to build

    # Compare hashes
    return stored_hash == current_hash


def _project_image_build_reason(
    project_dir: Path,
    image_tag: str,
    content_hash: str | None,
    shared_ts: datetime,
    shared_tag: str = _SHARED_SOURCE_IMAGES[0],
) -> str | None:
    """Decide whether the project-specific image needs to be (re)built.

    Returns a short reason string to build, or None when the cached image can be
    reused as-is.

    ``shared_ts``/``shared_tag`` identify the most recently built of the images this
    one copies content from (see ``_newest_shared_image_time()``). That comparison
    only applies to an image that is actually present: on the first run in a
    workspace — or right after stale images were pruned following a definition
    change — there is no image to date, and the correct outcome is a build, not a
    hard failure.

    An image that exists but carries no ``pi-container.build.time`` label was
    built by an older pi-container; its baked-in content cannot be dated, so it is
    rebuilt rather than trusted.
    """
    if not _image_exists(image_tag):
        return "image not built yet"

    project_ts = _get_image_build_time(image_tag)
    if project_ts is None:
        logger.warning(
            f"Project image ({image_tag}) has no build timestamp; the mitmproxy CA "
            f"certificate and toolchain it copied cannot be verified, so it will be rebuilt."
        )
        return "missing build timestamp"

    if shared_ts > project_ts:
        logger.warning(
            f"Shared image {shared_tag} has been rebuilt since the project image was built "
            f"({(shared_ts - project_ts).total_seconds():.0f}s ago). "
            f"The project image's copy of it (mitmproxy CA certificate, toolchain) is "
            f"stale and will be refreshed."
        )
        return "stale shared image"

    if not _image_is_current(project_dir, image_tag, content_hash):
        return "content hash mismatch"

    return None


# ─── Image lifecycle helpers ──────────────────────────────────────────────


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _remove_image(runtime: str, image_tag: str) -> bool:
    """Remove a container image by tag.

    Returns True on success, False if the image could not be removed.
    """
    try:
        result = subprocess.run(
            [runtime, "image", "rm", image_tag],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Removed image: {image_tag}")
            return True
        else:
            logger.warning(f"Could not remove image {image_tag}: {result.stderr.strip()}")
            return False
    except Exception as e:
        logger.warning(f"Could not remove image {image_tag}: {e}")
        return False


# Images that must never be reclaimed by a project-image cleanup pass, whatever
# their labels say. The shared base was stamped `pi-container.type=project` by
# older builds (the label was hardcoded in the Containerfile, which builds both the
# base and the per-project images); those images are still on disk until rebuilt,
# and deleting one would force a full base rebuild on every run.
_PROTECTED_IMAGE_TAGS = frozenset({IMAGE_TAG, *_SHARED_SOURCE_IMAGES})

# `{{.Repository}}:{{.Tag}}` renders as this literal once an image loses its tag,
# which happens to the previous image every time a rebuild moves a tag. It is not a
# valid image reference: inspecting or removing by it fails with
# `parsing reference "<none>:<none>": invalid reference format`. Enumerate by ID
# instead — valid for tagged and untagged images alike.
_UNTAGGED_NAME = "<none>:<none>"


def _is_protected_image(name: str) -> bool:
    """True if this image must never be reclaimed by a project-image cleanup pass.

    podman reports a locally-built image as ``localhost/pi-coding-agent:local`` while
    the configured tag is ``pi-coding-agent:local``, so both spellings have to match —
    an exact comparison silently lets the shared base through as a removal candidate.
    """
    return name in _PROTECTED_IMAGE_TAGS or name.removeprefix("localhost/") in _PROTECTED_IMAGE_TAGS


def _images_in_use(runtime: str) -> set[str]:
    """Image IDs pinned by an existing container, running or stopped.

    A cleanup pass must skip these. Removing one fails, and the failure is not a
    problem to report: concurrent sessions in a single workspace are supported, so a
    session started before a definition-file change legitimately keeps its now-stale
    image alive for as long as it runs. The image is reclaimed by a later run, once
    that session has exited.

    Returns short IDs, in the same 12-character form ``image ls --format {{.ID}}``
    reports. An empty set on failure just restores the previous behaviour — an
    attempted removal that the runtime refuses.
    """
    try:
        result = subprocess.run(
            [runtime, "ps", "--all", "--format", "{{.ImageID}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not list containers to check which images are in use: {e}")
        return set()

    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _enumerate_project_images(runtime: str) -> list[tuple[str, str]] | None:
    """Enumerate every image labelled ``pi-container.type=project``.

    Returns ``(image_id, display_name)`` pairs. ``image_id`` is what inspect and
    remove calls must use; ``display_name`` is for log messages only and is never a
    valid reference for untagged images. Returns None if the runtime could not be
    queried (distinct from an empty list, which means "no such images").
    """
    try:
        result = subprocess.run(
            [
                runtime,
                "image",
                "ls",
                "--format",
                "{{.ID}}\t{{.Repository}}:{{.Tag}}",
                "--filter",
                "label=pi-container.type=project",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not list project images: {e}")
        return None

    images: list[tuple[str, str]] = []
    for line in result.stdout.strip().splitlines():
        image_id, _, name = line.strip().partition("\t")
        if not image_id:
            continue
        name = name.strip()
        images.append((image_id, name if name and name != _UNTAGGED_NAME else f"{image_id} (untagged)"))
    return images


def _list_project_images(runtime: str) -> list[tuple[str, str, str]]:
    """List every project image together with its content hash.

    Returns a list of ``(image_id, display_name, content_hash)`` tuples. Images
    without labels are returned with an empty content_hash. Filtering by project
    is the caller's job — :func:`_cleanup_stale_project_images` does it against the
    ``pi-container.project.hash`` label.

    Raises warnings (but does not raise exceptions) if the container runtime
    is unavailable.
    """
    listed = _enumerate_project_images(runtime)
    if listed is None:
        return []
    return [(image_id, name, _get_image_label(image_id, "pi-container.hash") or "") for image_id, name in listed]


def _cleanup_stale_project_images(
    runtime: str,
    project_dir: Path,
    project_hash: str,
    new_hash: str,
) -> list[str]:
    """Find and remove project-specific images that are stale for this project.

    Strategy:
      1. Enumerate all images with label ``pi-container.type=project``.
      2. Filter to those whose ``pi-container.project.hash`` label matches
         ``project_hash`` (the current project).
      3. Among those, remove any whose ``pi-container.hash`` != ``new_hash``
         (the new content hash).
      4. If an image already matches ``new_hash``, no build is needed (cache
         hit — caller checks this separately).

    Returns the display names of the removed images.

    Images belonging to *other* projects (different ``project_hash``) are
    never touched, nor is any image a container still holds open — a concurrent
    session in this workspace pins the image it started on (see
    :func:`_images_in_use`).
    """
    removed: list[str] = []
    try:
        all_images = _list_project_images(runtime)
    except Exception as e:
        logger.warning(f"Failed to list project images during cleanup: {e}")
        return removed

    in_use = _images_in_use(runtime)
    for image_id, name, stored_hash in all_images:
        if _is_protected_image(name):
            continue
        # Skip images that already match the new hash (cache hit).
        if stored_hash == new_hash:
            continue
        # Skip images that don't have a pi-container.project.hash label
        # (they may be from a previous version — leave them for a future
        # migration pass).
        stored_project_hash = _get_image_label(image_id, "pi-container.project.hash")
        if stored_project_hash is None:
            continue
        if stored_project_hash != project_hash:
            continue
        # This image is stale — but a concurrent session in this workspace may still
        # be running on it, in which case the removal would fail. Reclaim it on a
        # later run, once that session has exited.
        if image_id in in_use:
            logger.info(f"Stale project image still in use by a container, keeping for now: {name}")
            continue
        if _remove_image(runtime, image_id):
            removed.append(name)
    return removed


def _cleanup_orphaned_project_images(runtime: str) -> list[str]:
    """Remove project images whose source project no longer exists.

    An image is considered orphaned if:
    - Its `pi-container.project.path` label is missing or blank (older images,
      unverifiable), OR
    - Its `pi-container.project.path` label points to a path that doesn't exist.

    Only images with a path label pointing to an **existing** directory are kept,
    plus the shared images in ``_PROTECTED_IMAGE_TAGS``, which are never candidates
    whatever their labels say, and any image a container still holds open (see
    :func:`_images_in_use`).

    Returns the display names of the removed images.
    """
    removed: list[str] = []
    # List ALL project images (regardless of project hash), by ID: an untagged
    # image has no usable name to inspect or remove by.
    listed = _enumerate_project_images(runtime)
    if listed is None:
        return removed

    in_use = _images_in_use(runtime)
    for image_id, name in listed:
        if _is_protected_image(name):
            continue

        stored_path = _get_image_label(image_id, "pi-container.project.path")

        # A blank label is as unverifiable as a missing one, and must be handled
        # explicitly: `Path("")` is `PosixPath(".")`, which always exists, so
        # falling through to the path check would silently keep the image.
        if stored_path is None or not stored_path.strip():
            reason = "no path label"
        elif not Path(stored_path).exists():
            reason = f"path gone: {stored_path}"
        else:
            continue  # Live project — not a candidate.

        # Decided it is an orphan, so say so — but a container may still be running
        # on it, in which case the removal would fail. Reclaim it on a later run.
        if image_id in in_use:
            logger.info(f"Orphaned project image still in use by a container, keeping for now: {name} ({reason})")
            continue

        logger.info(f"Orphaned project image ({reason}): {name}")
        if _remove_image(runtime, image_id):
            removed.append(name)

    return removed


# ─── Nested-container image store (a per-project named volume) ─────────────
#
# Nested image layers cannot live on the agent's default paths: /home/pi is
# tmpfs (a multi-GB pull would land in RAM) and the container rootfs is overlayfs
# (overlay-on-overlay is unsupported, so podman would fall back to the vfs driver
# and copy every layer in full). A named volume mounted at
# ~/.local/share/containers gives the nested podman a real filesystem, on which
# it resolves to the native `overlay` driver.
#
# Persisting it across runs is a deliberate exception to the ephemeral-home rule:
# without it every session re-pulls every base image through mitmproxy. The volume
# carries the same project labels as project-specific images, so it is reclaimed
# by the same path-no-longer-exists rule (see _cleanup_orphaned_nested_volumes).
#
# Concurrent runs in one workspace share the volume. That is intentional — it is
# what makes the layer cache shared — and safe because containers/storage keeps
# its layer/image lockfiles *inside* the graph root, i.e. on the shared volume, so
# the two nested podmans do interlock. Only the run root (podman's pid/lock dir)
# is per-run, under each container's own XDG_RUNTIME_DIR.


def _volume_exists(runtime: str, volume_name: str) -> bool:
    """Return True if the named volume is present in the local volume store."""
    try:
        result = subprocess.run(
            [runtime, "volume", "inspect", volume_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_volume_label(runtime: str, volume_name: str, label_key: str) -> str | None:
    """Read a label from a named volume, or None if absent/unreadable."""
    try:
        result = subprocess.run(
            [runtime, "volume", "inspect", volume_name, "--format", f'{{{{index .Labels "{label_key}"}}}}'],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        value = result.stdout.strip()
        # podman renders a missing key as the empty string (or "<no value>").
        if result.returncode == 0 and value and value != "<no value>":
            return value
    except Exception:
        pass
    return None


def _ensure_nested_volume(runtime: str, volume_name: str, project_hash: str, project_path: str) -> bool:
    """Create the nested-container image-store volume if it does not exist yet.

    The volume is labelled like project-specific images
    (``pi-container.type=nested-storage`` plus the project hash and path) so the
    orphan-cleanup pass can reclaim it with the same rule.

    Returns True if the volume is available for mounting.
    """
    if _volume_exists(runtime, volume_name):
        return True

    logger.info(f"Creating nested-container image store volume: {volume_name}")
    result = subprocess.run(
        [
            runtime,
            "volume",
            "create",
            "--label",
            "pi-container.type=nested-storage",
            "--label",
            f"pi-container.project.hash={project_hash}",
            "--label",
            f"pi-container.project.path={project_path}",
            volume_name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        logger.error(
            f"Could not create nested-container volume {volume_name}: {result.stderr.strip()}. "
            f"Set nested_containers.storage: tmpfs, or disable nested_containers."
        )
        return False
    return True


def _unused_volumes(runtime: str) -> set[str] | None:
    """Names of volumes no container references — the ones that can be removed.

    This is the volume counterpart of :func:`_images_in_use`, but inverted, because
    that is the form podman answers directly: ``ps --format {{.Mounts}}`` reports
    mount *destinations*, which cannot be mapped back to a volume name, while
    ``volume ls --filter dangling=true`` lists exactly the volumes nothing
    references. A merely created (never started) container is enough to keep a volume
    off this list, which matches the ``ps --all`` semantics used for images.

    Returns None if the query fails, which the caller treats as "unknown" and falls
    back to attempting the removal.
    """
    try:
        result = subprocess.run(
            [runtime, "volume", "ls", "--filter", "dangling=true", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not list unused volumes to check which are in use: {e}")
        return None

    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _cleanup_orphaned_nested_volumes(runtime: str) -> list[str]:
    """Remove nested-storage volumes whose source project no longer exists.

    Mirrors :func:`_cleanup_orphaned_project_images`: a volume is orphaned when
    its ``pi-container.project.path`` label points at a path that is gone, or when
    it has no path label at all (unverifiable). A volume a container still holds
    open is skipped — the removal would fail — and reclaimed by a later run, once
    that container is gone (see :func:`_unused_volumes`).
    """
    removed: list[str] = []
    try:
        result = subprocess.run(
            [
                runtime,
                "volume",
                "ls",
                "--format",
                "{{.Name}}",
                "--filter",
                "label=pi-container.type=nested-storage",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not list nested-storage volumes for orphan cleanup: {e}")
        return removed

    unused = _unused_volumes(runtime)
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue

        stored_path = _get_volume_label(runtime, name, "pi-container.project.path")

        # A blank label is as unverifiable as a missing one: `Path("")` is
        # `PosixPath(".")`, which always exists, so it would fall through the check
        # below and keep the volume forever.
        if stored_path is None or not stored_path.strip():
            reason = "no path label"
        elif not Path(stored_path).exists():
            reason = f"path gone: {stored_path}"
        else:
            continue  # Live project — not a candidate.

        # Decided it is an orphan, but a container may still hold it open, in which
        # case the removal would fail. Reclaim it on a later run.
        if unused is not None and name not in unused:
            logger.info(
                f"Orphaned nested-storage volume still in use by a container, keeping for now: {name} ({reason})"
            )
            continue

        logger.info(f"Orphaned nested-storage volume ({reason}): {name}")
        if _remove_volume(runtime, name):
            removed.append(name)

    return removed


def _remove_volume(runtime: str, volume_name: str) -> bool:
    """Remove a named volume. Returns True on success."""
    try:
        result = subprocess.run(
            [runtime, "volume", "rm", volume_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Removed nested-storage volume: {volume_name}")
            return True
        logger.warning(f"Could not remove volume {volume_name}: {result.stderr.strip()}")
        return False
    except Exception as e:
        logger.warning(f"Could not remove volume {volume_name}: {e}")
        return False


# ─── Registry allowlist preflight ──────────────────────────────────────────

# Registry hostname fragment → the hosts its layer blobs are 307-redirected to.
# Allowing the registry alone is not enough: the token and manifest requests
# succeed, then the redirect to the CDN falls through to default_action: block
# and the pull dies part-way through the first layer with a 403.
#
# Each entry is (sample_host, suggested_pattern) — the sample is a concrete host
# observed from the live registry (matched against the allowlist), the suggestion
# is what to actually add, widened where the real host is region- or
# account-dependent. Verified 2026-08-06; re-check when a registry moves its CDN,
# as Docker Hub did (Cloudflare → CloudFront).
_REGISTRY_BLOB_HOSTS: dict[str, tuple[tuple[str, str], ...]] = {
    "docker.io": (("production.cloudfront.docker.com", "*.cloudfront.docker.com"),),
    "ghcr.io": (("pkg-containers.githubusercontent.com", "pkg-containers.githubusercontent.com"),),
    "quay.io": (("cdn01.quay.io", "*.quay.io"),),
    "gcr.io": (),  # serves blobs inline, no redirect
    "registry.k8s.io": (("europe-north1-docker.pkg.dev", "*.pkg.dev"),),  # region-dependent
    "public.ecr.aws": (("d2glxqk2uabbnd.cloudfront.net", "*.cloudfront.net"),),  # opaque distribution id
    "mcr.microsoft.com": (("westeurope.data.mcr.microsoft.com", "*.data.mcr.microsoft.com"),),  # region-dependent
}

_REGEX_METACHARS = r"^$+{}[]()|"


def _hostname_allowed(host: str, patterns: list[str]) -> bool:
    """Would the allowlist addon let ``host`` through, given these allow patterns?

    Mirrors ``_matches_hostname`` in the proxy's allowlist addon: a pattern
    containing a regex metacharacter is treated as a regex, anything else as a
    glob. Duplicated rather than imported because the addon pulls in mitmproxy,
    which is not a dependency of the launcher.
    """
    for pattern in patterns:
        if any(c in pattern for c in _REGEX_METACHARS):
            try:
                if re.search(pattern, host, re.IGNORECASE):
                    return True
            except re.error:
                continue
        elif re.match("^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$", host, re.IGNORECASE):
            return True
    return False


def _warn_about_registry_allowlist(config_dir: Path) -> None:
    """Warn when nesting is on but the allowlist cannot actually carry an image pull.

    Two distinct gaps, both surfacing mid-session as an opaque mitmproxy 403:
    no registry is allowed at all, or a registry is allowed without the CDN host
    its blobs redirect to.

    Best-effort: an unreadable or malformed allowlist is the allowlist addon's
    problem to report, not this preflight's.
    """
    allowlist_path = config_dir / "allowlist.yaml"
    try:
        from yaml_strict import load_yaml_file

        data = load_yaml_file(allowlist_path) or {}
    except Exception:
        return

    patterns = [
        str(host)
        for rule in (data.get("global") or {}).get("rules", []) or []
        if str((rule or {}).get("mode", "allow")) == "allow"
        for host in (rule or {}).get("hostnames", []) or []
    ]

    allowed = [registry for registry in _REGISTRY_BLOB_HOSTS if any(registry in pattern for pattern in patterns)]
    if not allowed:
        logger.warning(
            f"nested_containers.enabled is true but no container registry hostname is allowed in "
            f"{allowlist_path}; image pulls from nested containers will fail with mitmproxy's 403. "
            f"Uncomment the 'container-registries-allow' rule (or add the registries you use)."
        )
        return

    for registry in allowed:
        missing = [
            suggestion
            for sample, suggestion in _REGISTRY_BLOB_HOSTS[registry]
            if not _hostname_allowed(sample, patterns)
        ]
        if missing:
            logger.warning(
                f"{registry} is allowed in {allowlist_path}, but the host its layer blobs redirect to "
                f"is not ({', '.join(missing)}); pulls will resolve the manifest and then fail on the "
                f"first layer with mitmproxy's 403. Add those hostnames to the rule."
            )


def _unavailable_host_ports(publish: list[tuple[int, int]], expose: str) -> list[int]:
    """Which of the requested host ports are already taken. Fail fast, not mid-run.

    ``podman run`` rejects a conflicting ``-p`` too, but only after the images are
    built and the proxy is up, and its error names the port without saying what
    holds it. Probing here lets the launch abort before any of that with a message
    that points at the config line to change.

    Probes the same address the port will actually be bound to — a free
    ``127.0.0.1:3000`` says nothing about ``0.0.0.0:3000`` under ``expose: lan``.
    ``SO_REUSEADDR`` is deliberately NOT set: it would let the probe bind a port
    still held in TIME-WAIT that podman would then refuse.
    """
    import socket

    host = "127.0.0.1" if expose != "lan" else "0.0.0.0"  # noqa: S104 — mirrors the bind podman will make
    taken: list[int] = []
    for host_port, _ in publish:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, host_port))
            except OSError:
                taken.append(host_port)
    return taken


# ─── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    agent_config_dir = _ensure_project_config()
    config_path: Path = agent_config_dir / "models.json"
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    # Validate per-project config schema and version compatibility.
    pi_container_dir = PROJECT_DIR / ".pi-container"
    config_yaml_path = pi_container_dir / "config.yaml"
    is_valid, errors, _ = validate_config(config_yaml_path)
    if not is_valid:
        logger.error("Configuration incompatible with this version of pi-container:")
        for error in errors:
            logger.error(error)
        logger.error(
            "\nFix: delete .pi-container in this workspace and re-run to re-seed, "
            "or update schema_version in .pi-container/config.yaml to match the "
            "current pi-container version (see latest git tag)."
        )
        sys.exit(1)

    # Duplicate mapping keys in any per-project YAML file. Separate from the
    # schema check above because a duplicate key parses clean and passes every
    # schema rule — the losing value is already gone by the time validation
    # sees the data. Caught here, before anything is built or started.
    yaml_errors = validate_project_yaml(pi_container_dir)
    if yaml_errors:
        logger.error("Configuration has duplicate YAML keys:")
        for error in yaml_errors:
            logger.error(error)
        logger.error(
            "\nYAML keeps only the last occurrence of a repeated key, so the earlier "
            "value was silently discarded. Delete the duplicate line and re-run."
        )
        sys.exit(1)

    # Validate models.json schema.
    models_path = pi_container_dir / "agent" / "models.json"
    models_valid, models_errors = validate_models(models_path)
    if not models_valid:
        logger.error("Models configuration invalid:")
        for error in models_errors:
            logger.error(error)
        logger.error("\nFix: update .pi-container/agent/models.json to match the expected schema.")
        sys.exit(1)
    flow_export_enabled = read_flow_export_enabled(pi_container_dir)
    ipv6_enabled = read_network_config(pi_container_dir)["ipv6"]
    llama_cfg = read_llama_config(pi_container_dir)
    agent_extras = read_agent_extras(pi_container_dir)
    nested_cfg = read_nested_containers_config(pi_container_dir)
    nested_ports = nested_cfg["ports"]
    if nested_cfg["enabled"]:
        logger.info(
            f"Nested containers enabled (storage={nested_cfg['storage']}, security={nested_cfg['security']}): "
            f"the agent can run its own rootless podman. Its traffic still egresses through the proxy, "
            f"but the agent container's SELinux confinement is relaxed."
        )
        _warn_about_registry_allowlist(pi_container_dir)
        if nested_ports["publish"]:
            # Abort before building anything if a port is already taken — podman
            # would only reject it after the images are built and the proxy is up.
            taken = _unavailable_host_ports(nested_ports["publish"], nested_ports["expose"])
            if taken:
                logger.error(
                    f"Host port(s) already in use: {', '.join(str(p) for p in taken)}. "
                    f"Free them, or change nested_containers.ports.publish in "
                    f"{config_yaml_path} (an entry may be written 'HOSTPORT:AGENTPORT' to remap)."
                )
                sys.exit(1)
            bind = "127.0.0.1" if nested_ports["expose"] != "lan" else "all interfaces"
            logger.info(
                "Publishing nested-container ports on "
                + f"{bind}: "
                + ", ".join(f"{host}→agent {agent}" for host, agent in nested_ports["publish"])
                + ". A nested container must publish the agent-side port itself "
                + "(e.g. `podman run -p 3000:3000`) for the host port to serve anything."
            )
    elif nested_ports["publish"]:
        logger.warning(
            f"nested_containers.ports.publish lists port(s) "
            f"{', '.join(str(host) for host, _ in nested_ports['publish'])} but "
            f"nested_containers.enabled is false; nothing is published."
        )

    with config_path.open("r") as file:
        data = json.load(file)
        server_configs = []
        for name, val in data["providers"].items():
            if isinstance(val, dict) and "serverCustomParameters" in val:
                server_config = ServerConfig.from_dict(val["serverCustomParameters"])
                server_configs.append({"name": name, "config": server_config, "baseUrl": val.get("baseUrl")})

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        with ExitStack() as stack:
            servers: list[Server] = []
            for item in server_configs:
                base_url = item["baseUrl"]
                container_port = urlparse(base_url).port if base_url else None

                server = Server(
                    config=item["config"],
                    models_dir=MODELS_DIR,
                    llama_bin=LLAMA_BIN,
                    lock_dir=LLAMA_SERVER_LOCK_DIR,
                    repo_root=REPO_ROOT,
                    server_id=item["name"],
                    container_port=container_port,
                    startup_timeout=llama_cfg["startup_timeout"],
                    startup_attempts=llama_cfg["startup_attempts"],
                )
                stack.enter_context(server)
                servers.append(server)

            portconfig = json.dumps([{"cp": server.container_port, "hp": server.port} for server in servers])

            # Unique name for this run's agent container. The proxy's flow_export
            # addon partitions captured traffic by client IP; naming the agent
            # container lets run.py look up its isolated-net IP (below) to find
            # the matching flows-<ip>.jsonl file.
            run_id = uuid.uuid4().hex[:12]
            agent_container_name = f"pi-coding-agent-{run_id}"

            # Per-project isolation: each workspace gets its own isolated network
            # and proxy container (auto-assigned mitmweb port, project-sourced
            # allowlist/token_replacer). Concurrent runs in the same workspace
            # resolve to the same names and share the proxy via refcount.
            proxy_name, network_name = _project_scope(PROJECT_DIR)

            with ContainerNetworkManager(
                RUNTIME,
                network_name,
                "pi-coding-agent-proxy:local",
                proxy_name=proxy_name,
                config_dir=pi_container_dir,
                llama_ports=portconfig,
                ipv6=ipv6_enabled,
            ) as netmgr:
                mitmweb_url = netmgr.mitmweb_url()
                if mitmweb_url:
                    logger.info(f"mitmweb UI for this project: {mitmweb_url}")
                proxy_isolated_ip: str | None = None
                proxy_isolated_ip6: str | None = None
                try:
                    result_ip = subprocess.run(
                        [CONTAINER_RUNTIME, "exec", proxy_name, "ip", "addr", "show", RUNTIME.proxy_isolated_interface],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    )
                    proxy_isolated_ip = extract_ipv4_from_ip_addr(result_ip.stdout)
                    if proxy_isolated_ip:
                        logger.info(f"Found proxy {RUNTIME.proxy_isolated_interface} IP address: {proxy_isolated_ip}")
                    if ipv6_enabled:
                        # Global-scope v6 address only (skip fe80:: link-local).
                        match_ip6 = re.search(r"inet6\s+([0-9a-fA-F:]+)/\d+\s+scope global", result_ip.stdout)
                        if match_ip6:
                            proxy_isolated_ip6 = match_ip6.group(1)
                            logger.info(
                                f"Found proxy {RUNTIME.proxy_isolated_interface} IPv6 address: {proxy_isolated_ip6}"
                            )
                        else:
                            logger.warning(
                                f"network.ipv6 is enabled but no global IPv6 address found on proxy "
                                f"{RUNTIME.proxy_isolated_interface}; the agent will have no IPv6 default route."
                            )
                except Exception as e:
                    logger.warning(f"Could not retrieve proxy network info: {e}")

                if not proxy_isolated_ip:
                    raise RuntimeError(
                        f"Could not determine proxy's {RUNTIME.proxy_isolated_interface} IP; "
                        f"the agent cannot be routed through the proxy."
                    )

                if ipv6_enabled:
                    netmgr.warn_if_proxy_lacks_ipv6_egress()

                # ─── Resolve agent image (shared vs. project-specific) ──────
                # If definition files exist and are non-empty, build a
                # project-specific image with baked-in command scripts.
                # Otherwise, use the shared IMAGE_TAG.
                agent_image_tag, is_project_specific = _resolve_agent_image(PROJECT_DIR)
                if is_project_specific:
                    label_hash = _compute_image_hash(PROJECT_DIR)
                    project_hash, _ = _project_scope(PROJECT_DIR)
                    project_path = str(PROJECT_DIR.resolve())

                    # 1. Cleanup orphaned images from deleted projects.
                    #    Reads pi-container.project.path from each image;
                    #    removes images whose stored path no longer exists.
                    orphaned = _cleanup_orphaned_project_images(CONTAINER_RUNTIME)
                    if orphaned:
                        logger.info(f"Removed {len(orphaned)} orphaned project image(s): {', '.join(orphaned)}")

                    # 2. Cleanup stale images for this project BEFORE deciding to build.
                    #    This removes old project-specific images whose content hash
                    #    no longer matches the current definition files, preventing
                    #    disk-space leaks from orphaned images.
                    removed = _cleanup_stale_project_images(
                        CONTAINER_RUNTIME,
                        PROJECT_DIR,
                        project_hash,
                        label_hash,
                    )
                    if removed:
                        logger.info(f"Removed {len(removed)} stale project image(s): {', '.join(removed)}")

                    # 3. Check whether either shared image has been rebuilt more
                    #    recently than the project-specific image. The project
                    #    image copies the mitmproxy CA certificate out of the proxy
                    #    image and its whole toolchain out of the builder image at
                    #    build time; if either is newer, that copy is stale and
                    #    must be refreshed.
                    #    A project image that is absent (first run here, or just
                    #    pruned by step 2 after a definition change) has no
                    #    timestamp to compare and is simply built.
                    newest_shared = _newest_shared_image_time()
                    if newest_shared is None:
                        sys.exit(1)
                    shared_tag, shared_ts = newest_shared

                    reason = _project_image_build_reason(
                        PROJECT_DIR, agent_image_tag, label_hash, shared_ts, shared_tag
                    )
                    if reason is not None:
                        logger.info(f"Building project-specific agent image: {agent_image_tag} ({reason})")
                        root_commands_path = str(
                            PROJECT_DIR / ".pi-container" / "dependencies" / "root" / "commands.sh"
                        )
                        pi_commands_path = str(PROJECT_DIR / ".pi-container" / "dependencies" / "pi" / "commands.sh")
                        build_project_image(
                            CONTAINER_RUNTIME,
                            root_commands_path,
                            pi_commands_path,
                            agent_image_tag,
                            label_hash,
                            project_hash=project_hash,
                            project_path=project_path,
                            build_timestamp=now_iso(),
                        )
                    else:
                        logger.info(f"Using cached project-specific image: {agent_image_tag}")
                else:
                    logger.info(f"Using shared image: {agent_image_tag}")

                # ─── Nested-container image store ───────────────────────────
                # Reclaim stores from deleted/moved workspaces first (same rule
                # as project images), then make sure this workspace's own store
                # exists. If it cannot be created, fall back to a tmpfs store
                # rather than failing the launch — the agent still runs, it just
                # re-pulls base images and needs the RAM.
                nested_args: list[str] = []
                if nested_cfg["enabled"]:
                    project_key = _project_key(PROJECT_DIR)
                    if nested_cfg["storage"] == "volume":
                        orphaned_volumes = _cleanup_orphaned_nested_volumes(CONTAINER_RUNTIME)
                        if orphaned_volumes:
                            logger.info(
                                f"Removed {len(orphaned_volumes)} orphaned nested-storage volume(s): "
                                f"{', '.join(orphaned_volumes)}"
                            )
                        if not _ensure_nested_volume(
                            CONTAINER_RUNTIME,
                            RUNTIME.nested_volume_name(project_key),
                            project_hash=proxy_name,
                            project_path=str(PROJECT_DIR.resolve()),
                        ):
                            logger.warning("Falling back to a tmpfs nested image store for this run.")
                            nested_cfg = {**nested_cfg, "storage": "tmpfs"}
                    nested_args = RUNTIME.nested_container_args(nested_cfg, project_key)

                # Transient tmpfs paths (config.yaml tmpfs.paths) — all under
                # /workspace (the mounted PROJECT_DIR), so the config that declares
                # them is per-project. Reading the repo's list for a foreign
                # workspace would mkdir this repo's mountpoints (e.g.
                # pi-coding-agent-proxy/*) inside that workspace.
                tmpfs_paths = scan_tmpfs_paths(pi_container_dir)

                pi_container_cmd = [
                    CONTAINER_RUNTIME,
                    "run",
                    "--rm",
                    "--name",
                    agent_container_name,
                    "--interactive",
                    "--tty",
                    *RUNTIME.agent_network_args(network_name, proxy_isolated_ip),
                    # IPv6 policy for the agent: --sysctl toggle (VM runtimes) +
                    # env flag its entrypoint reads. When enabled, DEFAULT_ROUTE6
                    # points the v6 default route at the proxy's eth1 v6 address.
                    *RUNTIME.ipv6_run_args(ipv6_enabled),
                    "--env",
                    f"IPV6_ENABLED={str(ipv6_enabled).lower()}",
                    *(["--env", f"DEFAULT_ROUTE6={proxy_isolated_ip6}"] if proxy_isolated_ip6 else []),
                    *RUNTIME.tmpfs_args("/home/pi/"),
                    "--volume",
                    f"{agent_config_dir}:/home/pi/.pi/agent",
                    "--volume",
                    f"{PROJECT_DIR}:/workspace",
                    *RUNTIME.tmpfs_args("/home/pi/.pi/agent/bin"),
                    *RUNTIME.tmpfs_args("/workspace/.pi-container/exports"),
                    # Nested-container support (config.yaml nested_containers):
                    # devices, SELinux label, image store, XDG_RUNTIME_DIR. Empty
                    # when disabled. Placed after the /home/pi tmpfs because the
                    # image store mounts *underneath* it.
                    *nested_args,
                    # Host ports for UIs served by nested containers. A nested
                    # container's own -p binds only inside this container's netns,
                    # so the agent republishes it; the ports are fixed here because
                    # podman cannot add one to a running container.
                    *RUNTIME.nested_port_args(nested_cfg),
                    "--workdir",
                    "/workspace",
                    # Transient tmpfs mounts for build artifacts, caches, etc.
                    *[flag for path in tmpfs_paths for flag in RUNTIME.tmpfs_args(path)],
                    "--env",
                    f"LLAMA_PORTS={portconfig}",
                    "--env",
                    f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                    # Extra agent env vars + bind mounts + capabilities + devices (config.yaml agent.env/mounts/capabilities/devices).
                    *[flag for k, v in agent_extras["env"].items() for flag in ("--env", f"{k}={v}")],
                    *[flag for m in agent_extras["mounts"] for flag in ("--volume", m)],
                    *[flag for c in agent_extras["capabilities"] for flag in ("--cap-add", c)],
                    *[flag for d in agent_extras["devices"] for flag in ("--device", d)],
                    # Resource limits for this project's agent (config.yaml resources.agent).
                    *resource_limit_args(read_resource_limits(pi_container_dir, "agent")),
                    agent_image_tag,
                    *sys.argv[1:],
                ]

                # Discover the agent's isolated-net IPs (IPv4 and/or IPv6) in the
                # background so we can attribute its captured flows (partitioned by
                # client IP in the proxy) after it exits. A daemon thread keeps the
                # interactive TTY handoff to subprocess.run untouched; the exec
                # probes it runs use their own pipes, not the agent's terminal.
                ip_holder: dict[str, list[str]] = {}
                ip_stop = threading.Event()
                ip_thread: threading.Thread | None = None
                if flow_export_enabled:

                    def _discover_agent_ips() -> None:
                        ips = poll_agent_container_ips(CONTAINER_RUNTIME, agent_container_name, ip_stop)
                        if ips:
                            ip_holder["ips"] = ips

                    ip_thread = threading.Thread(target=_discover_agent_ips, daemon=True)
                    ip_thread.start()

                result = subprocess.run(pi_container_cmd)

                # Export mitmweb flow history for this session. The flow_export
                # addon appends per-client-IP files as flows complete; run.py reads
                # this agent's file(s) here (after it exits, before the
                # ContainerNetworkManager context exits and stops the proxy).
                if flow_export_enabled:
                    ip_stop.set()
                    if ip_thread is not None:
                        ip_thread.join(timeout=2)
                    export_mitmweb_flows(
                        sessions_dir=agent_config_dir / "sessions",
                        client_ips=ip_holder.get("ips"),
                        exports_dir=pi_container_dir / "exports",
                    )

            if result.returncode != 0:
                sys.exit(result.returncode)
    except Exception:
        logger.exception("An error occurred")
        sys.exit(1)
    finally:
        Model.cleanup_download_lock_dir(LLAMA_SERVER_LOCK_DIR / "model_download")


if __name__ == "__main__":
    _init_runtime()
    main()

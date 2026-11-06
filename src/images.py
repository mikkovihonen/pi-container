import hashlib
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.dont_write_bytecode = True

import project
from config import IMAGE_TAG, PROXY_IMAGE_TAG, REPO_ROOT
from yaml_strict import load_yaml_strict

logger = logging.getLogger(__name__)

_IMAGE_DEFINITION_FILES = (
    "Containerfile",
    "entrypoint.sh",
    "t3-advertise-host.cjs",
    "t3-seed-settings.cjs",
    "t3-policy.json",
)
_PROXY_IMAGE_DEFINITION_FILES = ("Containerfile", "entrypoint.sh")
_SHARED_SOURCE_IMAGES = (PROXY_IMAGE_TAG, "pi-coding-agent-builder:local")
_PROTECTED_IMAGE_TAGS = frozenset({IMAGE_TAG, *_SHARED_SOURCE_IMAGES})
_UNTAGGED_NAME = "<none>:<none>"


def _get_runtime(runtime: str | None = None) -> str:
    """Return the runtime binary name, falling back to environment."""
    if runtime:
        return runtime
    return os.environ.get("CONTAINER_RUNTIME", "podman")


def now_iso() -> str:
    """Return current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_project_tools(project_dir: Path) -> list[str]:
    """Read the list of tools enabled in .pi-container/config.yaml."""
    cfg_path = project_dir / ".pi-container" / "config.yaml"
    if not cfg_path.exists():
        return []
    try:
        data = load_yaml_strict(cfg_path.read_text()) or {}
        tools_sec = data.get("tools")
        if isinstance(tools_sec, dict):
            include = tools_sec.get("include")
            if isinstance(include, list):
                return sorted([t for t in include if isinstance(t, str)])
    except Exception:
        pass
    return []


def compute_image_hash(project_dir: Path, repo_root: Path | None = None) -> str | None:
    """Compute content hash for files affecting the project-specific container image."""
    r_root = repo_root or REPO_ROOT
    deps_root = project_dir / ".pi-container" / "dependencies"
    agent_dir = r_root / "pi-coding-agent"
    files_to_hash = []

    for img_file in _IMAGE_DEFINITION_FILES:
        img_path = agent_dir / img_file
        if img_path.exists():
            files_to_hash.append(img_file)

    for cmd_file in ("root/commands.sh", "pi/commands.sh"):
        cmd_path = deps_root / cmd_file
        if cmd_path.exists() and cmd_path.stat().st_size > 0:
            files_to_hash.append(cmd_file)

    tools = read_project_tools(project_dir)
    if not files_to_hash and not tools:
        return None

    files_to_hash.sort()

    combined_hash = hashlib.sha256()
    for cmd_file in files_to_hash:
        file_path = agent_dir / cmd_file if cmd_file in _IMAGE_DEFINITION_FILES else deps_root / cmd_file
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        combined_hash.update(file_hash.encode())

    if tools:
        combined_hash.update(f"tools:{','.join(tools)}".encode())
        from toolchains import ON_DEMAND_TOOLS, compute_tool_hash

        for t in tools:
            if t in ON_DEMAND_TOOLS:
                tool_hash = compute_tool_hash(t, r_root)
                combined_hash.update(f"tool:{t}:{tool_hash}".encode())

    return combined_hash.hexdigest()[:16]


def compute_proxy_image_hash(repo_root: Path | str | None = None) -> str:
    """Compute content hash for files affecting the proxy container image."""
    r_root = Path(repo_root) if repo_root else REPO_ROOT
    proxy_dir = r_root / "pi-coding-agent-proxy"
    files_to_hash: list[Path] = []

    for img_file in _PROXY_IMAGE_DEFINITION_FILES:
        img_path = proxy_dir / img_file
        if img_path.exists():
            files_to_hash.append(img_path)

    addons_dir = proxy_dir / "addons"
    if addons_dir.is_dir():
        for path in addons_dir.rglob("*"):
            if path.is_file() and not path.name.startswith(".") and "__pycache__" not in path.parts:
                files_to_hash.append(path)

    files_to_hash.sort()

    combined_hash = hashlib.sha256()
    for file_path in files_to_hash:
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        rel_path = file_path.relative_to(proxy_dir)
        combined_hash.update(f"{rel_path}:{file_hash}".encode())

    return combined_hash.hexdigest()[:16]


def proxy_image_is_current(repo_root: Path | str | None = None, runtime: str | None = None) -> bool:
    """Check if the proxy image exists and matches current definition content hashes."""
    if not image_exists(PROXY_IMAGE_TAG, runtime=runtime):
        return False

    stored_hash = get_image_label(PROXY_IMAGE_TAG, "pi-container.hash", runtime=runtime)
    if stored_hash is None:
        return False

    current_hash = compute_proxy_image_hash(repo_root=repo_root)
    return stored_hash == current_hash


def tool_image_is_current(tool: str, repo_root: Path | str | None = None, runtime: str | None = None) -> bool:
    """Check if the on-demand tool image exists and matches current recipe hashes."""
    from toolchains import compute_tool_hash, tool_to_image_tag

    r_root = Path(repo_root) if repo_root else REPO_ROOT
    tag = tool_to_image_tag(tool)
    if not image_exists(tag, runtime=runtime):
        return False

    stored_hash = get_image_label(tag, "pi-container.hash", runtime=runtime)
    if stored_hash is None:
        return False

    current_hash = compute_tool_hash(tool, r_root)
    return stored_hash == current_hash


def ensure_tool_images(
    tools: list[str] | set[str] | frozenset[str],
    runtime: str,
    repo_root: Path | str | None = None,
) -> None:
    """Ensure all required on-demand tool images are built and current."""
    from build import build_tool
    from toolchains import ON_DEMAND_TOOLS

    r_root = Path(repo_root) if repo_root else REPO_ROOT
    for tool in sorted(tools):
        if tool in ON_DEMAND_TOOLS and not tool_image_is_current(tool, repo_root=r_root, runtime=runtime):
            logger.info(f"Tool image for '{tool}' is missing or stale; building...")
            build_tool(runtime, tool)


def has_dependency_files(project_dir: Path) -> bool:
    """Check if the project has non-empty dependency definition command files."""
    deps_root = project_dir / ".pi-container" / "dependencies"
    for cmd_file in ("root/commands.sh", "pi/commands.sh"):
        cmd_path = deps_root / cmd_file
        if cmd_path.exists() and cmd_path.stat().st_size > 0:
            return True
    return False


def resolve_agent_image(project_dir: Path, repo_root: Path | None = None) -> tuple[str, bool]:
    """Resolve the agent container image tag for this workspace."""
    has_deps = has_dependency_files(project_dir)
    tools = read_project_tools(project_dir)
    if not has_deps and not tools:
        return IMAGE_TAG, False

    project_hash, _ = project.project_scope(project_dir)
    key = project_hash.split("pi-proxy-")[1]
    image_hash = compute_image_hash(project_dir, repo_root=repo_root)
    project_image_tag = f"pi-container-project-{key}-{image_hash}.local"
    return project_image_tag, True


def get_image_label(image_tag: str, label_key: str, runtime: str | None = None) -> str | None:
    """Read a specific label value from a container image."""
    rt = _get_runtime(runtime)
    try:
        result = subprocess.run(
            [
                rt,
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
        value = result.stdout.strip()
        if result.returncode == 0 and value and value != "<no value>":
            return value
    except Exception:
        pass

    return None


def image_exists(image_tag: str, runtime: str | None = None) -> bool:
    """Check if a container image exists in the local image storage."""
    rt = _get_runtime(runtime)
    try:
        result = subprocess.run(
            [rt, "image", "inspect", image_tag],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_image_build_time(image_tag: str, runtime: str | None = None) -> datetime | None:
    """Read and parse the ``pi-container.build.time`` label from a container image."""
    try:
        ts_str = get_image_label(image_tag, "pi-container.build.time", runtime=runtime)
    except Exception:
        logger.warning(f"Could not read build time from image {image_tag}")
        return None
    if ts_str is None:
        return None
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        logger.warning(f"Could not parse build time '{ts_str}' from image {image_tag}")
        return None


def newest_shared_image_time(runtime: str | None = None) -> tuple[str, datetime] | None:
    """Find the most recent build timestamp among shared base images."""
    newest: tuple[str, datetime] | None = None
    for tag in _SHARED_SOURCE_IMAGES:
        ts = get_image_build_time(tag, runtime=runtime)
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


def image_is_current(project_dir: Path, image_tag: str, current_hash: str | None, runtime: str | None = None) -> bool:
    """Check if a project-specific image matches current definition content hashes."""
    _, is_project_specific = resolve_agent_image(project_dir)
    if not is_project_specific:
        return True

    stored_hash = get_image_label(image_tag, "pi-container.hash", runtime=runtime)
    if stored_hash is None:
        return False

    return stored_hash == current_hash


def project_image_build_reason(
    project_dir: Path,
    image_tag: str,
    content_hash: str | None,
    shared_ts: datetime,
    shared_tag: str = _SHARED_SOURCE_IMAGES[0],
    runtime: str | None = None,
) -> str | None:
    """Determine why a project image needs rebuilding, or return None if current."""
    if not image_exists(image_tag, runtime=runtime):
        return "image not built yet"

    project_ts = get_image_build_time(image_tag, runtime=runtime)
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

    if not image_is_current(project_dir, image_tag, content_hash, runtime=runtime):
        return "content hash mismatch"

    return None


def remove_image(runtime: str, image_tag: str) -> bool:
    """Delete an image by tag or ID."""
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


def is_protected_image(name: str) -> bool:
    """Check if an image name is protected from automated cleanup."""
    return name in _PROTECTED_IMAGE_TAGS or name.removeprefix("localhost/") in _PROTECTED_IMAGE_TAGS


def images_in_use(runtime: str) -> set[str]:
    """Return the set of image IDs currently in use by any container."""
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


def enumerate_project_images(runtime: str) -> list[tuple[str, str]] | None:
    """List all project images labelled ``pi-container.type=project``."""
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


def list_project_images(runtime: str) -> list[tuple[str, str, str]]:
    """List all project images with their content hashes by inspecting image labels."""
    listed = enumerate_project_images(runtime)
    if listed is None:
        return []

    result = []
    for image_id, name in listed:
        lbl = get_image_label(image_id, "pi-container.hash", runtime=runtime)
        result.append((image_id, name, lbl or ""))
    return result


def cleanup_stale_project_images(
    runtime: str,
    project_hash: str,
    new_hash: str,
) -> list[str]:
    """Remove project images whose label hash no longer matches current configuration."""
    removed: list[str] = []
    try:
        all_images = list_project_images(runtime)
    except Exception as e:
        logger.warning(f"Failed to list project images during cleanup: {e}")
        return removed

    in_use = images_in_use(runtime)

    for image_id, name, stored_hash in all_images:
        if is_protected_image(name):
            continue
        if stored_hash == new_hash:
            continue
        stored_project_hash = get_image_label(image_id, "pi-container.project.hash", runtime=runtime)

        if stored_project_hash is None:
            continue
        if stored_project_hash != project_hash:
            continue
        if image_id in in_use:
            logger.info(f"Stale project image still in use by a container, keeping for now: {name}")
            continue
        if remove_image(runtime, image_id):
            removed.append(name)
    return removed


def cleanup_orphaned_project_images(runtime: str) -> list[str]:
    """Remove project images whose source project directory no longer exists on the host."""
    removed: list[str] = []
    listed = enumerate_project_images(runtime)
    if listed is None:
        return removed

    in_use = images_in_use(runtime)

    for image_id, name in listed:
        if is_protected_image(name):
            continue

        stored_path = get_image_label(image_id, "pi-container.project.path", runtime=runtime)

        if stored_path is None or not stored_path.strip():
            reason = "no path label"
        elif not Path(stored_path).exists():
            reason = f"path gone: {stored_path}"
        else:
            continue

        if image_id in in_use:
            logger.info(f"Orphaned project image still in use by a container, keeping for now: {name} ({reason})")
            continue

        logger.info(f"Orphaned project image ({reason}): {name}")
        if remove_image(runtime, image_id):
            removed.append(name)

    return removed

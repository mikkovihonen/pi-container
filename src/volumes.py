import hashlib
import logging
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

logger = logging.getLogger(__name__)


def volume_exists(runtime: str, volume_name: str) -> bool:
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


def get_volume_label(runtime: str, volume_name: str, label_key: str) -> str | None:
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
        if result.returncode == 0 and value and value != "<no value>":
            return value
    except Exception:
        pass
    return None


def remove_volume(runtime: str, volume_name: str, label_type: str = "volume") -> bool:
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
            logger.info(f"Removed {label_type}: {volume_name}")
            return True
        logger.warning(f"Could not remove {label_type} {volume_name}: {result.stderr.strip()}")
        return False
    except Exception as e:
        logger.warning(f"Could not remove {label_type} {volume_name}: {e}")
        return False


def unused_volumes(runtime: str) -> set[str] | None:
    """Return the set of volume names that are not in use by any container."""
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


def ensure_nested_volume(runtime: str, volume_name: str, project_hash: str, project_path: str) -> bool:
    """Create the nested-container image-store volume if it does not exist yet."""
    if volume_exists(runtime, volume_name):
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


def cleanup_orphaned_nested_volumes(runtime: str) -> list[str]:
    """Remove nested-storage volumes whose source project directory no longer exists."""
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

    unused = unused_volumes(runtime)

    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue

        stored_path = get_volume_label(runtime, name, "pi-container.project.path")

        if stored_path is None or not stored_path.strip():
            reason = "no path label"
        elif not Path(stored_path).exists():
            reason = f"path gone: {stored_path}"
        else:
            continue

        if unused is not None and name not in unused:
            logger.info(
                f"Orphaned nested-storage volume still in use by a container, keeping for now: {name} ({reason})"
            )
            continue

        logger.info(f"Orphaned nested-storage volume ({reason}): {name}")
        if remove_volume(runtime, name):
            removed.append(name)

    return removed


def project_volume_name(project_key: str, dest_path: str) -> str:
    """Derive a deterministic named volume name for a project's shadow mount path."""
    dest_hash = hashlib.sha256(dest_path.strip().encode()).hexdigest()[:8]
    return f"pi-vol-{project_key}-{dest_hash}"


def ensure_project_volume(
    runtime: str,
    volume_name: str,
    dest_path: str,
    project_hash: str,
    project_path: str,
) -> bool:
    """Create a project shadow volume if it does not exist yet."""
    if volume_exists(runtime, volume_name):
        return True

    logger.info(f"Creating project shadow volume: {volume_name} for {dest_path}")
    result = subprocess.run(
        [
            runtime,
            "volume",
            "create",
            "--label",
            "pi-container.type=project-volume",
            "--label",
            f"pi-container.project.hash={project_hash}",
            "--label",
            f"pi-container.project.path={project_path}",
            "--label",
            f"pi-container.volume.dest={dest_path}",
            volume_name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        logger.error(f"Could not create project volume {volume_name}: {result.stderr.strip()}")
        return False
    return True


def cleanup_stale_project_volumes(
    runtime: str,
    project_hash: str,
    active_volume_names: set[str],
) -> list[str]:
    """Find and remove shadow volumes that are no longer in this project's config.yaml."""
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
                "label=pi-container.type=project-volume",
                "--filter",
                f"label=pi-container.project.hash={project_hash}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not list project volumes for stale cleanup: {e}")
        return removed

    unused = unused_volumes(runtime)

    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name or name in active_volume_names:
            continue

        dest = get_volume_label(runtime, name, "pi-container.volume.dest") or "unknown dest"

        if unused is not None and name not in unused:
            logger.info(f"Stale project volume still in use by a container, keeping for now: {name} ({dest})")
            continue

        logger.info(f"Stale project volume (no longer in config.yaml): {name} ({dest})")
        if remove_volume(runtime, name, label_type="project volume"):
            removed.append(name)

    return removed


def cleanup_orphaned_project_volumes(runtime: str) -> list[str]:
    """Remove project shadow volumes whose source project directory no longer exists on the host."""
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
                "label=pi-container.type=project-volume",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Could not list project volumes for orphan cleanup: {e}")
        return removed

    unused = unused_volumes(runtime)

    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue

        stored_path = get_volume_label(runtime, name, "pi-container.project.path")

        if stored_path is None or not stored_path.strip():
            reason = "no path label"
        elif not Path(stored_path).exists():
            reason = f"path gone: {stored_path}"
        else:
            continue

        if unused is not None and name not in unused:
            logger.info(f"Orphaned project volume still in use by a container, keeping for now: {name} ({reason})")
            continue

        logger.info(f"Orphaned project volume ({reason}): {name}")
        if remove_volume(runtime, name, label_type="orphaned project volume"):
            removed.append(name)

    return removed

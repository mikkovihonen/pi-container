import hashlib
import logging
import shutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

from config import PROJECT_DIR, REPO_ROOT
from config_schema import SCHEMA_VERSION_MISMATCH

logger = logging.getLogger(__name__)

_PROJECT_CONFIG_DIRS = ("agent", "chat-templates")
_PROJECT_CONFIG_FILES = ("config.yaml", "allowlist.yaml", "token_replacer.yaml")


def project_key(project_dir: Path) -> str:
    """Return a 10-character sha256 hash identifying a workspace path."""
    return hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:10]


def project_scope(project_dir: Path) -> tuple[str, str]:
    """Return ``(proxy_name, network_name)`` unique to this workspace."""
    key = project_key(project_dir)
    return f"pi-proxy-{key}", f"pi-isolated-net-{key}"


def ensure_project_config(project_dir: Path | None = None, repo_root: Path | None = None) -> Path:
    """Seed the per-project ``.pi-container`` config from the repo template if absent.

    Seeds ``{project_dir}/.pi-container`` from ``{repo_root}/pi-coding-agent/default``:
    the ``agent/`` and ``chat-templates/`` subtrees plus ``config.yaml`` and the proxy
    addon configs ``allowlist.yaml``/``token_replacer.yaml``. Also seeds ``entrypoint.sh``
    into ``.pi-container/agent/`` and dependency command templates if present.

    Returns the agent launch-config dir (``{project_dir}/.pi-container/agent``).
    """
    p_dir = project_dir if project_dir is not None else PROJECT_DIR
    r_root = repo_root if repo_root is not None else REPO_ROOT
    template_root = r_root / "pi-coding-agent" / "default"
    if not template_root.is_dir():
        raise FileNotFoundError(f"Project config template not found: {template_root}")

    project_root = p_dir / ".pi-container"

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

    ep_src = template_root / "entrypoint.sh"
    ep_dst = project_root / "agent" / "entrypoint.sh"
    if ep_src.exists() and not ep_dst.exists():
        logger.info(f"Seeding {ep_dst} from {ep_src}.")
        shutil.copy2(ep_src, ep_dst)

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


def config_fix_hint(errors: list[str], config_path: Path) -> list[str]:
    """Return targeted instructions for correcting schema validation errors in config.yaml."""
    version_only = bool(errors) and all(SCHEMA_VERSION_MISMATCH in e for e in errors)
    if version_only:
        return [
            f"\nFix: set schema_version in {config_path} to the version named above.",
            "  The rest of the file already validates, so nothing else has to change and your settings are kept.",
        ]
    return [
        f"\nFix: re-seed the one file whose shape changed — {config_path.name} —",
        f"  by deleting it and re-running:    rm {config_path}",
        "  Only that file is rewritten. allowlist.yaml, token_replacer.yaml, models.json,",
        "  chat-templates/ and dependencies/ are left alone.",
        "  Your own edits to it are NOT merged, so note them first. Editing schema_version alone will not help:",
        "  the fields above are missing from the file, not mislabelled.",
    ]

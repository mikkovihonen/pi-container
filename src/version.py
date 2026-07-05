import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def get_git_tag_version(repo_root: Path) -> str | None:
    """Return the version from the latest ``v*`` git tag on the current branch.

    Returns ``None`` if no tags exist (pre-release — validation is skipped).
    """
    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        tags = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
        if not tags:
            return None
        return tags[0].lstrip("v")
    # fmt: off
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None
    # fmt: on

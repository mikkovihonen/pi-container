from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

"""Chat template file path resolution and existence validation."""


def resolve_chat_template_path(
    template_path: str,
    repo_root: Path,
) -> Path:
    """Resolve a `--chat-template-file` flag path relative to repo_root if not absolute."""
    p = Path(template_path)
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    return p


def check_chat_template_paths(
    flags: list,
    repo_root: Path,
) -> list[str]:
    """Validate that `--chat-template-file` paths specified in server flags exist on disk."""
    errors: list[str] = []
    for i, flag in enumerate(flags):
        if flag == "--chat-template-file":
            if i + 1 >= len(flags):
                errors.append(f"flags[{i}]: --chat-template-file has no following path")
                continue

            template_path = flags[i + 1]
            if not isinstance(template_path, str):
                errors.append(
                    f"flags[{i}]: --chat-template-file path is not a string (got {type(template_path).__name__})"
                )
                continue

            resolved = resolve_chat_template_path(template_path, repo_root)
            if not resolved.exists():
                errors.append(f"flags[{i}]: --chat-template-file path does not exist: {resolved}")
    return errors

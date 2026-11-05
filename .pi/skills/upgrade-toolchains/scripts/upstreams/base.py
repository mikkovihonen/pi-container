import re
import sys
from dataclasses import dataclass, field

sys.dont_write_bytecode = True


@dataclass
class ToolReleaseInfo:
    """Resolved release information and Containerfile ARGs for a tool."""

    tool: str
    version: str
    args: dict[str, str] = field(default_factory=dict)
    category: str = "tools"
    release_url: str | None = None


def parse_version_tuple(v: str) -> tuple[int, ...]:
    """Extract a numeric comparison tuple from a version tag string.

    Examples:
        'v2.36.0' -> (2, 36, 0)
        'llvmorg-19.1.7' -> (19, 1, 7)
        'gopls/v0.18.1' -> (0, 18, 1)
        '2025-02-24' -> (2025, 2, 24)
    """
    clean = re.sub(r"^(?:llvmorg-|gopls/|v)", "", v.strip())
    parts = re.findall(r"\d+", clean)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)

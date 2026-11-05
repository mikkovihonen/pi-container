import hashlib
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

logger = logging.getLogger(__name__)

# ─── Tool Definitions ────────────────────────────────────────────────────────

#: Core build tools shipped inside the builder image under /go and /rust roots.
CORE_TOOLS: frozenset[str] = frozenset({"go", "rust"})

#: On-demand tools compiled by the builder only when explicitly requested.
ON_DEMAND_TOOLS: frozenset[str] = frozenset(
    {
        "ripgrep",
        "fd",
        "ast-grep",
        "tree-sitter-cli",
        "colgrep",
        "qdrant",
        "sqlite-vec",
        "rust-analyzer",
        "gopls",
        "clangd",
        "zls",
        "mold",
        "git",
        "ruby",
    }
)

#: All available tools that can be declared in tools.include.
AVAILABLE_TOOLS: frozenset[str] = CORE_TOOLS | ON_DEMAND_TOOLS


# ─── Mapping Helpers ─────────────────────────────────────────────────────────


def tool_to_build_arg(tool: str) -> str:
    """Return the Containerfile build argument for a tool (e.g. 'WITH_RIPGREP')."""
    return f"WITH_{tool.replace('-', '_').upper()}"


def tool_to_context_name(tool: str) -> str:
    """Return the build context name for an on-demand tool (e.g. 'tool_tree_sitter_cli')."""
    return f"tool_{tool.replace('-', '_').lower()}"


def tool_to_image_tag(tool: str) -> str:
    """Return the local container image tag for an on-demand tool."""
    return f"pi-coding-agent-tool-{tool}:local"


def tool_to_stage_target(tool: str) -> str:
    """Return the Containerfile export target stage for an on-demand tool."""
    return f"stage-{tool}"


def tool_recipe_path(tool: str, repo_root: Path) -> Path:
    """Return the path to the tool's build script."""
    if tool in CORE_TOOLS:
        return repo_root / "pi-coding-agent-builder" / f"build-{tool}.sh"
    return repo_root / "pi-coding-agent-builder" / "tools" / tool / "build.sh"


def compute_tool_hash(tool: str, repo_root: Path) -> str:
    """Compute content hash for a tool recipe including its script and Containerfile pin."""
    hasher = hashlib.sha256()
    hasher.update(tool.encode())

    recipe = tool_recipe_path(tool, repo_root)
    if recipe.exists():
        hasher.update(recipe.read_bytes())

    containerfile = repo_root / "pi-coding-agent-builder" / "Containerfile"
    if containerfile.exists():
        cf_content = containerfile.read_text()
        in_stage = False
        stage_lines: list[str] = []
        for line in cf_content.splitlines():
            if line.startswith(f"FROM deps AS {tool}"):
                in_stage = True
            elif in_stage and line.startswith("FROM "):
                in_stage = False
            if in_stage:
                stage_lines.append(line)
        if stage_lines:
            hasher.update("\n".join(stage_lines).encode())

    return hasher.hexdigest()[:16]

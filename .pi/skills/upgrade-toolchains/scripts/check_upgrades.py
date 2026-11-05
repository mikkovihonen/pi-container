#!/usr/bin/env python3
"""Toolchain upgrade and audit automation for pi-coding-agent-builder."""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.dont_write_bytecode = True

# Add scripts directory to path to import upstreams
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
BUILDER_DIR = REPO_ROOT / "pi-coding-agent-builder"
CONTAINERFILE = BUILDER_DIR / "Containerfile"

sys.path.insert(0, str(SCRIPT_DIR))

from upstreams import RESOLVERS

if TYPE_CHECKING:
    from upstreams.base import ToolReleaseInfo

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("upgrade_toolchains")

GROUPS: dict[str, list[str]] = {
    "compilers": ["mold", "go", "rust"],
    "search": ["ripgrep", "fd", "ast-grep", "tree-sitter-cli", "colgrep"],
    "vector": ["qdrant", "sqlite-vec"],
    "lsp": ["rust-analyzer", "gopls", "clangd", "zls"],
    "vcs": ["git", "ruby"],
    "tools": ["t3"],
    "core": [
        "go",
        "rust",
        "node",
        "podman",
        "netavark",
        "aardvark-dns",
        "uv",
        "podman-compose",
        "python-dotenv",
        "pyyaml",
    ],
}


def read_containerfile_args(path: Path) -> dict[str, str]:
    """Parse all ARG declarations from Containerfile into a dictionary."""
    content = path.read_text()
    args: dict[str, str] = {}
    # Match ARG NAME=VALUE or multi-line quoted values
    pattern = re.compile(r"^ARG\s+([A-Za-z0-9_]+)=(.*)$", re.MULTILINE)
    for m in pattern.finditer(content):
        name = m.group(1).strip()
        val = m.group(2).strip()
        args[name] = val
    return args


def get_current_tool_version(tool: str, args: dict[str, str]) -> str:
    """Retrieve the current pinned version string for a tool."""
    if tool == "tree-sitter-cli":
        return args.get("TREE_SITTER_VERSION", "").strip('"').strip("'")
    if tool == "aardvark-dns":
        return args.get("AARDVARK_VERSION", "").strip('"').strip("'")
    if tool == "colgrep":
        return args.get("COLGREP_VERSION", "v0.1.0").strip('"').strip("'")
    key = f"{tool.replace('-', '_').upper()}_VERSION"
    if key in args:
        return args[key].strip('"').strip("'")
    if tool == "python":
        return args.get("PYTHON_VERSION", "")
    return ""


def update_containerfile_arg(content: str, arg_name: str, new_value: str) -> str:
    """Update a specific ARG declaration in Containerfile content."""
    # Handle single-line and multi-line replacement
    # 1. Check if existing ARG is multi-line (ends with backslash)
    pattern_multi = re.compile(
        rf"^(ARG\s+{re.escape(arg_name)}=\"?[^\n]*?(?:\\\n[^\n]*?)*\"?)$",
        re.MULTILINE,
    )
    m = pattern_multi.search(content)
    if m:
        replacement = f"ARG {arg_name}={new_value}"
        return content[: m.start()] + replacement + content[m.end() :]

    # Simple single-line replacement
    pattern_single = re.compile(rf"^ARG\s+{re.escape(arg_name)}=.*$", re.MULTILINE)
    return pattern_single.sub(f"ARG {arg_name}={new_value}", content)


def check_tool(tool: str, current_args: dict[str, str]) -> dict:
    """Check single tool against upstream and return status dictionary."""
    resolver = RESOLVERS.get(tool)
    if not resolver:
        return {
            "tool": tool,
            "category": "unknown",
            "current": "unknown",
            "latest": "unknown",
            "up_to_date": True,
            "error": "No resolver registered",
        }

    current_ver = get_current_tool_version(tool, current_args)
    try:
        release_info: ToolReleaseInfo = resolver()
        up_to_date = True
        # Check if version or any ARG differs
        if current_ver and current_ver != release_info.version:
            up_to_date = False
        else:
            for k, v in release_info.args.items():
                curr = current_args.get(k, "")
                if curr and curr.strip('"').strip("'") != v.strip('"').strip("'"):
                    up_to_date = False
                    break

        return {
            "tool": tool,
            "category": release_info.category,
            "current": current_ver or "pinned",
            "latest": release_info.version,
            "up_to_date": up_to_date,
            "release_info": release_info,
            "error": None,
        }
    except Exception as e:
        return {
            "tool": tool,
            "category": "error",
            "current": current_ver,
            "latest": "error",
            "up_to_date": True,
            "error": str(e),
        }


def format_table(results: list[dict]) -> str:
    """Format audit results into an aligned terminal table."""
    headers = ["Tool", "Category", "Current", "Upstream", "Status"]
    rows = []
    for r in results:
        status = "✓ Up to date" if r["up_to_date"] else "⚑ Upgrade available"
        if r.get("error"):
            status = f"✗ Error: {r['error']}"
        rows.append([r["tool"], r["category"], r["current"], r["latest"], status])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    def make_line(items: list[str]) -> str:
        return "│ " + " │ ".join(items[i].ljust(col_widths[i]) for i in range(len(items))) + " │"

    sep_top = "┌─" + "─┬─".join("─" * w for w in col_widths) + "─┐"
    sep_mid = "├─" + "─┼─".join("─" * w for w in col_widths) + "─┤"
    sep_bot = "└─" + "─┴─".join("─" * w for w in col_widths) + "─┘"

    lines = [sep_top, make_line(headers), sep_mid]
    for row in rows:
        lines.append(make_line(row))
    lines.append(sep_bot)
    return "\n".join(lines)


def run_verify(tool: str | None = None) -> bool:
    """Run pin declaration and contract tests."""
    logger.info("Running pin declaration validation (test_build.py)...")
    res = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "src/tests/test_build.py",
            "-k",
            "TestToolchainPinsAreDeclared",
        ],
        cwd=str(REPO_ROOT),
    )
    return res.returncode == 0


def upgrade_tool(tool: str, release_info: ToolReleaseInfo, containerfile_path: Path) -> None:
    """Apply updated ARGs to Containerfile for a tool."""
    content = containerfile_path.read_text()
    for arg_name, new_val in release_info.args.items():
        logger.info(f"Updating {arg_name}={new_val[:32]}...")
        content = update_containerfile_arg(content, arg_name, new_val)
    containerfile_path.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Toolchain upgrade manager for pi-container.")
    parser.add_argument("--check", action="store_true", help="Audit tools against upstream (read-only).")
    parser.add_argument("--upgrade", metavar="TOOL", help="Tool to upgrade, or 'all'.")
    parser.add_argument(
        "--upgrade-group",
        metavar="GROUP",
        choices=list(GROUPS.keys()),
        help=f"Group to upgrade ({', '.join(GROUPS.keys())}).",
    )
    parser.add_argument("--verify", action="store_true", help="Run pin validation tests after upgrade.")
    parser.add_argument("--commit", action="store_true", help="Create git commit on successful upgrade.")

    args = parser.parse_args()

    current_args = read_containerfile_args(CONTAINERFILE)

    # Determine targets
    if args.upgrade:
        targets = sorted(RESOLVERS.keys()) if args.upgrade == "all" else [t.strip() for t in args.upgrade.split(",")]
    elif args.upgrade_group:
        targets = GROUPS[args.upgrade_group]
    else:
        # Default to check all
        targets = sorted(RESOLVERS.keys())

    if args.check or (not args.upgrade and not args.upgrade_group):
        logger.info(f"Auditing {len(targets)} toolchain(s) against upstreams...")
        results = [check_tool(t, current_args) for t in targets]
        print(format_table(results))
        return

    # Upgrades requested
    upgraded: list[str] = []
    for tool in targets:
        if tool not in RESOLVERS:
            logger.error(f"Unknown tool '{tool}'. Available: {', '.join(sorted(RESOLVERS.keys()))}")
            continue

        res = check_tool(tool, current_args)
        if res.get("error"):
            logger.error(f"Skipping {tool}: {res['error']}")
            continue

        if res["up_to_date"]:
            logger.info(f"{tool} is already up to date ({res['current']}).")
            continue

        release_info: ToolReleaseInfo = res["release_info"]
        logger.info(f"Upgrading {tool}: {res['current']} -> {release_info.version}")
        upgrade_tool(tool, release_info, CONTAINERFILE)
        upgraded.append(f"{tool} ({release_info.version})")

    if not upgraded:
        logger.info("No toolchains needed upgrading.")
        return

    # Verify if requested
    if args.verify:
        if not run_verify():
            logger.error("Pin validation failed! Reverting Containerfile...")
            subprocess.run(["git", "checkout", str(CONTAINERFILE)], check=True)
            sys.exit(1)
        logger.info("Pin validation passed successfully.")

    # Commit if requested
    if args.commit:
        msg = f"chore(toolchains): bump {', '.join(upgraded)}"
        subprocess.run(["git", "add", str(CONTAINERFILE)], cwd=str(REPO_ROOT), check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=str(REPO_ROOT), check=True)
        logger.info(f"Committed changes: {msg}")


if __name__ == "__main__":
    main()

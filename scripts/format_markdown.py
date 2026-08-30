#!/usr/bin/env python3
"""Format Markdown files to ensure 4-space list indentation for Zensical."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def format_markdown_text(text: str) -> str:
    """Format markdown text to ensure 4-space list indentation."""
    lines = text.splitlines(keepends=True)
    formatted_lines: list[str] = []
    in_code_fence = False
    fence_char = ""
    in_frontmatter = False

    bullet_pattern = re.compile(r"^( *)([-*+]|\d+[.)])\s+(.*)$")

    for i, line in enumerate(lines):
        line_no_ending = line.rstrip("\r\n")
        ending = line[len(line_no_ending) :]
        stripped = line_no_ending.strip()

        # Frontmatter check at top of file
        if i == 0 and stripped == "---":
            in_frontmatter = True
            formatted_lines.append(line)
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            formatted_lines.append(line)
            continue

        # Code fence toggle
        if stripped.startswith("```") or stripped.startswith("~~~"):
            char = stripped[:3]
            if not in_code_fence:
                in_code_fence = True
                fence_char = char
            elif char == fence_char:
                in_code_fence = False
            formatted_lines.append(line)
            continue

        if in_code_fence:
            formatted_lines.append(line)
            continue

        if not stripped:
            formatted_lines.append(ending)
            continue

        m = bullet_pattern.match(line_no_ending)
        if m:
            leading_spaces, bullet, content = m.groups()
            old_indent = len(leading_spaces)

            if old_indent == 0:
                new_indent = 0
            else:
                # Map indents to multiples of 4: 1-3 -> 4, 4 -> 4, 5-7 -> 8, etc.
                if old_indent % 4 != 0:
                    level = (old_indent + 2) // 4
                    if level == 0:
                        level = 1
                    new_indent = level * 4
                else:
                    new_indent = old_indent

            formatted_line = f"{' ' * new_indent}{bullet} {content.rstrip()}{ending}"
            formatted_lines.append(formatted_line)
        else:
            formatted_lines.append(f"{line_no_ending.rstrip()}{ending}")

    res = "".join(formatted_lines)
    if res and not res.endswith("\n"):
        res += "\n"
    return res


def format_file(file_path: Path, check_only: bool = False) -> bool:
    """Format a single markdown file. Returns True if file was/would be changed."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return False

    formatted = format_markdown_text(content)
    if content == formatted:
        return False

    if not check_only:
        file_path.write_text(formatted, encoding="utf-8")
        print(f"Formatted {file_path}")
    else:
        print(f"Would reformat {file_path}")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Format markdown files to enforce 4-space list indentation.")
    parser.add_argument("paths", nargs="*", type=Path, help="Files or directories to format.")
    parser.add_argument("--check", action="store_true", help="Check formatting without modifying files.")
    args = parser.parse_args()

    paths = args.paths
    if not paths:
        root = Path(__file__).resolve().parent.parent
        paths = list(root.glob("docs/**/*.md")) + [root / "README.md", root / "CHANGELOG.md"]

    files_to_format: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() in {".md", ".markdown"}:
            files_to_format.append(p)
        elif p.is_dir():
            files_to_format.extend([f for f in p.rglob("*.md") if ".venv" not in f.parts and "site" not in f.parts])

    modified_count = 0
    for file_path in sorted(set(files_to_format)):
        if format_file(file_path, check_only=args.check):
            modified_count += 1

    if args.check and modified_count > 0:
        print(f"\n{modified_count} file(s) require formatting. Run without --check to fix.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

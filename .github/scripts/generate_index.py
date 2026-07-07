#!/usr/bin/env python3
"""Generate docs/index.md from README.md for MkDocs site."""

import re
from pathlib import Path

REPO_URL = "https://github.com/mikkovihonen/pi-container/blob/main"
README_PATH = Path("README.md")
INDEX_PATH = Path("docs/index.md")

FRONTMATTER = """---
title: pi-container
description: A containerized environment for running the pi-coding-agent with local LLM inference and full auditability
---

"""


def fix_links(content: str) -> str:
    """Convert README.md links to work within MkDocs docs/ directory."""
    # Convert docs/assets/... to assets/...
    content = re.sub(r"docs/assets/", "assets/", content)

    # Convert docs/XXX.md to XXX.md (MkDocs will resolve to XXX/index.html)
    # Handles anchors like docs/development.md#coverage
    content = re.sub(r"\(docs/([^)]+\.md)(#[^)]*)?\)", r"(\1\2)", content)
    content = re.sub(r"!\([^)]*\(docs/([^)]+)", r"!\1", content)

    # Convert ../README.md and ../../README.md to index.md
    content = re.sub(r"\.\./README\.md", "index.md", content)
    content = re.sub(r"\.\./\.\./README\.md", "index.md", content)

    # Convert ../src/... to GitHub URLs
    content = re.sub(r"\.\./src/", f"{REPO_URL}/src/", content)

    # Convert ../docs/assets/.gitignore.example to GitHub URL
    content = re.sub(
        r"\.\./docs/assets/\.gitignore\.example",
        f"{REPO_URL}/docs/assets/.gitignore.example",
        content,
    )

    # Convert ../../.pi-container/... to GitHub URLs
    content = re.sub(
        r"\.\./\.\./\.pi-container/",
        f"{REPO_URL}/.pi-container/",
        content,
    )

    # Convert ../../pi-coding-agent-proxy/... to GitHub URLs
    content = re.sub(
        r"\.\./\.\./pi-coding-agent-proxy/",
        f"{REPO_URL}/pi-coding-agent-proxy/",
        content,
    )

    # Convert root-level file links to GitHub URLs (LICENSE, etc.)
    # Handles both [text](LICENSE) and [![img](...)](LICENSE) patterns
    content = re.sub(
        r"\]\((?P<file>LICENSE|\.env\.example|build\.sh|run\.sh)\)",
        lambda m: f"]({REPO_URL}/{m.group('file')})",
        content,
    )

    return content


def generate_index() -> None:
    """Generate docs/index.md from README.md."""
    readme_content = README_PATH.read_text()
    fixed_content = fix_links(readme_content)

    index_content = FRONTMATTER + fixed_content
    INDEX_PATH.write_text(index_content)
    print(f"Generated {INDEX_PATH}")


if __name__ == "__main__":
    generate_index()

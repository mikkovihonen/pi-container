# AI Agent Guidelines

## Markdown & Documentation Guidelines

### List Indentation (Zensical / Python-Markdown)
The documentation site is built using **Zensical** (which uses Python-Markdown). Python-Markdown requires **4-space indentation** for nested list items. Using 2 spaces will fail to nest sub-bullets and break the documentation rendering.

When creating or modifying any Markdown (`*.md`) files:
- **Top-level items**: 0 leading spaces (`- item` or `* item` or `1. item`).
- **First-level sub-items**: 4 leading spaces (`    - sub-item`).
- **Second-level sub-items**: 8 leading spaces (`        - sub-sub-item`).
- Multiples of 4 spaces must be used for all subsequent nesting levels.

#### Example
```markdown
- Top-level item
    - Sub-item level 1 (4 spaces)
    - Another sub-item
        - Sub-item level 2 (8 spaces)
```

### Pre-commit & Tooling
- All Python tools and lint hooks must be managed through `uv`.
- Before submitting documentation changes, run `uv run pre-commit run --all-files` or `uv run python3 scripts/format_markdown.py`.

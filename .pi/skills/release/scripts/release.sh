#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.2.0"
    exit 1
fi

VERSION="$1"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "=== Release v$VERSION ==="
echo ""

# 1. Bump pyproject.toml
echo "✓ Bumping pyproject.toml version to $VERSION"
sed -i "s/^version = .*/version = \"$VERSION\"/" pyproject.toml

# 2. Bump schema_version in both config files
echo "✓ Bumping schema_version in pi-coding-agent/default/config.yaml"
sed -i "s/^schema_version: .*/schema_version: \"$VERSION\"/" pi-coding-agent/default/config.yaml

echo "✓ Bumping schema_version in .pi-container/config.yaml"
sed -i "s/^schema_version: .*/schema_version: \"$VERSION\"/" .pi-container/config.yaml

# 3. Regenerate uv.lock
echo "✓ Regenerating uv.lock"
uv lock

# 4. Validate (pass --new-version so the script compares against the target
#    version instead of the still-old git tag — the new tag isn't created yet)
echo ""
echo "=== Validating ==="
uv run python3 .github/workflows/scripts/validate_versions.py --new-version "$VERSION"

# 5. Run lint
echo ""
echo "=== Running lint ==="
uv run pre-commit run --all-files --show-diff-on-failure || {
    echo "✗ Lint failed. Fix before releasing."
    exit 1
}

# 6. Enforce CHANGELOG reverse chronological order
echo ""
echo "=== Checking CHANGELOG order ==="
if [ -f CHANGELOG.md ]; then
    # Find the first ## heading after [Unreleased]
    first_section=$(grep -n '^## \[' CHANGELOG.md | head -1 | cut -d: -f1)
    unreleased_line=$(grep -n '^## \[Unreleased\]' CHANGELOG.md | head -1 | cut -d: -f1)
    if [ -n "$unreleased_line" ] && [ "$unreleased_line" != "$first_section" ]; then
        echo "✗ CHANGELOG.md has [Unreleased] at line $unreleased_line, not first."
        echo "  Reverse chronological order required: [Unreleased] must be on top."
        exit 1
    fi
    echo "  ✓ CHANGELOG.md has [Unreleased] at top."
else
    echo "⚠ No CHANGELOG.md found — skipping order check."
fi

# 7. Run tests
echo ""
echo "=== Running tests ==="
uv run pytest --cov || {
    echo "✗ Tests failed. Fix before releasing."
    exit 1
}

echo ""
echo "=== All checks passed ==="
echo ""
echo "Next steps:"
echo "  1. Update CHANGELOG.md (move [Unreleased] → [$VERSION] - $(date +%Y-%m-%d))"
echo "  2. git add -A && git commit -m \"release: v$VERSION\""
echo "  3. git tag -a v$VERSION -m \"Release v$VERSION\""
echo "  4. git push origin main && git push origin v$VERSION"

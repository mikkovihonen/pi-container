#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# ─── Helpers ──────────────────────────────────────────────────────────────

# BSD sed (macOS) requires an argument to -i, GNU sed (Linux) forbids one.
# Writing through a temp file is the one form both accept.
substitute() {  # substitute <sed-expr> <file>
    local expr="$1" file="$2"
    sed "$expr" "$file" > "$file.tmp" && mv "$file.tmp" "$file"
}

# The changelog uses "## Unreleased" for the open section and
# "## [0.4.1] - 2026-08-06" for released ones. Either spelling of the
# Unreleased heading is accepted; it just has to come first.
check_changelog_order() {
    if [ ! -f CHANGELOG.md ]; then
        echo "⚠ No CHANGELOG.md found — skipping order check."
        return 0
    fi

    local headings first
    headings=$(grep -n '^## ' CHANGELOG.md || true)
    if [ -z "$headings" ]; then
        echo "✗ CHANGELOG.md has no '## ' section headings."
        return 1
    fi

    first=$(printf '%s\n' "$headings" | head -1)
    case "$first" in
        *'## Unreleased'* | *'## [Unreleased]'*)
            echo "  ✓ CHANGELOG.md has Unreleased at top."
            ;;
        *)
            echo "✗ CHANGELOG.md does not start with the Unreleased section."
            echo "  First heading is line ${first%%:*}: ${first#*:}"
            echo "  Reverse chronological order required: Unreleased on top,"
            echo "  then the newest version, then older ones."
            return 1
            ;;
    esac
}

# ─── Modes ────────────────────────────────────────────────────────────────

# Called by the release skill *after* the CHANGELOG has been rewritten — the
# ordering mistake this catches is made during that edit, not before it.
if [ "${1:-}" = "--check-changelog" ]; then
    echo "=== Checking CHANGELOG order ==="
    check_changelog_order
    exit 0
fi

if [ $# -ne 1 ]; then
    echo "Usage: $0 <version>"
    echo "       $0 --check-changelog"
    echo "Example: $0 0.2.0"
    exit 1
fi

VERSION="$1"

echo "=== Release v$VERSION ==="
echo ""

# ─── 1. Preflight ─────────────────────────────────────────────────────────
# Everything that can be checked without touching the tree is checked here,
# so a rejected release leaves no half-bumped files behind.

echo "=== Preflight ==="

if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'; then
    echo "✗ '$VERSION' is not a semantic version (expected e.g. 0.2.0)."
    exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
    echo "✗ On branch '$BRANCH'. Releases are tagged on 'main'."
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "✗ Working tree has uncommitted changes. Commit or stash them first —"
    echo "  the release commit must contain only the version bump and CHANGELOG."
    git status --short
    exit 1
fi

if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
    echo "✗ Tag v$VERSION already exists."
    exit 1
fi

check_changelog_order
echo "  ✓ On main, tree clean, tag v$VERSION is free."

# ─── 2. Bump versions ─────────────────────────────────────────────────────

echo ""
echo "✓ Bumping pyproject.toml version to $VERSION"
substitute "s/^version = .*/version = \"$VERSION\"/" pyproject.toml

echo "✓ Bumping schema_version in pi-coding-agent/default/config.yaml"
substitute "s/^schema_version: .*/schema_version: \"$VERSION\"/" pi-coding-agent/default/config.yaml

echo "✓ Bumping schema_version in .pi-container/config.yaml"
substitute "s/^schema_version: .*/schema_version: \"$VERSION\"/" .pi-container/config.yaml

# 3. Regenerate uv.lock
echo "✓ Regenerating uv.lock"
uv lock

# ─── 4. Commit version bumps ──────────────────────────────────────────────
# The CHANGELOG edit amends this commit, so it must exist. A no-op commit
# means the bumps did not land (already at this version, or sed matched
# nothing) — amending then would rewrite an unrelated commit on main.

echo "✓ Committing version bumps"
git add pyproject.toml pi-coding-agent/default/config.yaml .pi-container/config.yaml uv.lock
if git diff --cached --quiet; then
    echo "✗ Nothing to commit — the version bumps produced no changes."
    echo "  Is the repo already at $VERSION? Release aborted, since the next"
    echo "  step would amend an unrelated commit."
    exit 1
fi
git commit -m "chore: bump to $VERSION"

# Anything failing past this point leaves the bump commit in place.
UNDO="  Version bumps are committed as $(git rev-parse --short HEAD) — 'git reset --hard HEAD~1' to undo."

# 5. Validate (pass --new-version so the script compares against the target
#    version instead of the still-old git tag — the new tag isn't created yet)
echo ""
echo "=== Validating ==="
uv run python3 .github/workflows/scripts/validate_versions.py --new-version "$VERSION" || {
    echo "✗ Validation failed. Fix before releasing."
    echo "$UNDO"
    exit 1
}

# 6. Run lint. The pytest hook is skipped here and run once below with
#    coverage instead, rather than running the suite twice.
echo ""
echo "=== Running lint ==="
SKIP=pytest uv run pre-commit run --all-files --show-diff-on-failure || {
    echo "✗ Lint failed. Fix before releasing."
    echo "$UNDO"
    exit 1
}

# 7. Run tests
echo ""
echo "=== Running tests ==="
uv run --group src --group proxy pytest --cov || {
    echo "✗ Tests failed. Fix before releasing."
    echo "$UNDO"
    exit 1
}

echo ""
echo "=== All checks passed ==="
echo ""
echo "Next steps:"
echo "  1. Update CHANGELOG.md (move Unreleased → [$VERSION] - $(date +%Y-%m-%d))"
echo "  2. $0 --check-changelog"
echo "  3. git add CHANGELOG.md && git commit --amend -m \"release: v$VERSION\""
echo "  4. git tag -a v$VERSION -m \"Release v$VERSION\""
echo "  5. git push origin main && git push origin v$VERSION"

---
name: release
description: Creates a new pi-container release. Bumps versions across all files,
  updates CHANGELOG, regenerates uv.lock, validates consistency, and creates
  the git tag. Use when the user says "release vX.Y.Z" or "make a new release".
---

# Release pi-container

## When to Use

The user wants to publish a new version. Examples:
- "Release 0.2.0"
- "Make a new release"
- "Bump the version"

## Prerequisites

- `main` is green (CI passing)
- `CHANGELOG.md` has `[Unreleased]` entries ready to promote
- All changes for this release are committed to `main`

## Steps

### 1. Determine version

Ask the user which version, or suggest based on changes:
- **Patch** (0.1.1 → 0.1.2): Bug fixes, no new features
- **Minor** (0.1.2 → 0.2.0): New features, backwards-compatible
- **Major** (0.1.2 → 1.0.0): Breaking changes

If unsure, ask: "Should this be a patch, minor, or major release?"

### 2. Run the release script

```bash
./.pi/skills/release/scripts/release.sh <version>
```

The script will:
- Bump `pyproject.toml` version
- Bump `schema_version` in `pi-coding-agent/default/config.yaml`
- Bump `schema_version` in `.pi-container/config.yaml`
- Run `uv lock` to regenerate the lockfile
- Validate version consistency with `validate_versions.py`
- Run lint and tests
- Report success/failure

### 3. Update CHANGELOG

If the script succeeds, update `CHANGELOG.md`:
- Move `[Unreleased]` entries to the new version block
- Add today's date
- Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format

### 4. Commit and tag

```bash
git add -A
git commit -m "release: v<version>"
git tag -a v<version> -m "Release v<version>"
```

### 5. Push

```bash
git push origin main
git push origin v<version>
```

CI will create the GitHub Release automatically.

## Error Handling

- **Validation fails:** Fix the error before proceeding. Common issues:
  - Version mismatch between files
  - Missing required fields in config
  - Schema validation errors
- **Tests fail:** Don't release. Fix the underlying issue first.
- **CHANGELOG update needed:** The script doesn't modify CHANGELOG — do it manually or use `towncrier` if configured.

## Notes

- The `validate_versions.py` hook runs in CI only (not pre-commit) because the git tag doesn't exist until after the commit.
- `.pi-container/config.yaml` must be updated separately from the template — seeding is copy-once.
- If the user wants to skip pushing, the script commits and tags locally but doesn't push.

## Reference

See `docs/releases.md` for the full release documentation.

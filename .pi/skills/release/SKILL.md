---
name: release
description: Creates a new pi-container release. Bumps versions across all files,
  regenerates uv.lock, validates consistency, then guides the CHANGELOG update,
  release commit, and git tag. Use when the user says "release vX.Y.Z" or
  "make a new release".
---

# Release pi-container

## When to Use

The user wants to publish a new version. Examples:
- "Release 0.2.0"
- "Make a new release"
- "Bump the version"

## Prerequisites

- `main` is green (CI passing)
- `CHANGELOG.md` has `Unreleased` entries ready to promote
- All changes for this release are committed to `main` and the working tree is
  clean — the script refuses to start otherwise

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
- Preflight: semver format, on `main`, clean working tree, tag not already
  taken, CHANGELOG already in order. It refuses before touching any file.
- Bump `pyproject.toml` version
- Bump `schema_version` in `pi-coding-agent/default/config.yaml`
- Bump `schema_version` in `.pi-container/config.yaml`
- Run `uv lock` to regenerate the lockfile
- Commit the bumps as `chore: bump to <version>`
- Validate version consistency with `validate_versions.py`
- Run lint and tests
- Report success/failure

If a step after the commit fails, the script prints the commit SHA and the
`git reset --hard HEAD~1` needed to undo it.

### 3. Update CHANGELOG and amend the release commit

If the script succeeds, update `CHANGELOG.md`:
- Move the `Unreleased` entries to the new version block
- Add today's date
- Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format
- **Enforce reverse chronological order**: `Unreleased` must be at the top,
  followed by newest version, then older ones. If out of order, rewrite the
  file with correct ordering before committing.
- **After editing, verify the ordering** — it's easy to place the new version
  block above `Unreleased`:
  ```bash
  ./.pi/skills/release/scripts/release.sh --check-changelog
  ```
- **Amend the release commit** (the script already committed version bumps):
  ```bash
  git add CHANGELOG.md && git commit --amend -m "release: v<version>"
  ```
  Stage only `CHANGELOG.md` — `git add -A` would sweep unrelated working-tree
  changes into the release commit.

  **Never re-run the release script** after editing CHANGELOG — that would
  re-bump versions and create a second tag.

### 4. Tag

```bash
git tag -a v<version> -m "Release v<version>"
```

### 5. Push

```bash
git push origin main
git push origin v<version>
```

CI will create the GitHub Release automatically.

## Error Handling

- **Preflight fails:** Nothing has been modified — fix the reported condition
  and re-run the script with the same version.
- **Validation fails:** Fix the error before proceeding. Common issues:
    - Version mismatch between files
    - Missing required fields in config
    - Schema validation errors
- **Tests fail:** Don't release. Fix the underlying issue first. The bump
  commit is already in place; either fix and amend it, or
  `git reset --hard HEAD~1` and start over.
- **"Nothing to commit":** The bumps produced no changes, so there is no
  release commit to amend. Check whether the repo is already at this version.

## Notes

- The script never touches `CHANGELOG.md` or creates the tag — those are steps
  3 and 4 above.
- The `validate_versions.py` hook runs in CI only (not pre-commit) because the git tag doesn't exist until after the commit.
- `.pi-container/config.yaml` must be updated separately from the template — seeding is copy-once.
- The lint step runs with `SKIP=pytest`; the suite runs once afterwards with
  `--cov`, rather than twice.
- If the user wants to skip pushing, everything through the tag stays local.

## Reference

See `docs/releases.md` for the full release documentation.

# Releases

This project is installed by cloning. Releases are **git tags** on `main`. The system builds container
images locally with `build.sh`. The system does not publish images to a registry.
The latest git tag determines the authoritative version, not `pyproject.toml`.

## Development model: trunk-based

Work on short-lived branches off `main`. Land changes via pull requests.

```
git checkout -b feat/entrypoint-seed main
# ... commit, push ...
# Open PR. CI runs lint + test.
# Squash-merge to main.
```

### Branch naming

| Prefix | Use for |
|--------|---------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `chore/` | Maintenance (deps, CI, docs) |

No `develop`, `release`, or `hotfix` branches — `main` is the only integration
branch. Hotfixes follow the same PR flow from `main`.

### Commit messages

Use the conventional-commit prefix pattern for clarity. A hook does not enforce it:

```
feat: seed entrypoint.sh into .pi-container/agent
fix: handle missing proxy IPv6 address gracefully
chore: bump pyyaml to 6.0.2
```

## Versioning: Semantic Versioning

```
vMAJOR.MINOR.PATCH
 ^      ^     ^
 |      |     └─ Bug fix (no new features, no breaking changes)
 |      └─────── New feature (backwards-compatible)
 └────────────── Breaking change
```

- **0.x.y** — Pre-release. Breaking changes are possible without a major bump.
  The team tries to avoid them.
- **1.x.y** — Stable. `MAJOR` bumps only for breaking changes.

The latest git tag (e.g., `git tag -l | head -1`) determines the authoritative version.
`pyproject.toml` is informational only. The launch reads the version from git.

## Schema version: config compatibility

Every pi-container release ships a template for the per-project configuration
at `pi-coding-agent/default/`. The system seeds this template into each workspace's
`.pi-container/` on first run.

The template `config.yaml` includes a `schema_version` field that matches the
pi-container version (e.g., `"0.1.0"`). At launch, the system checks the schema version in the
seeded config against the app version (from the latest git tag). A mismatch makes
the launch fail with a clear error message.

### When to bump the schema version

Bump the `schema_version` in `pi-coding-agent/default/config.yaml` whenever you:

- Add a new required field to the template
- Change the type of an existing field
- Remove a field that users might still have in their configs
- Add a new file to `pi-coding-agent/default/` (e.g., a new addon config)

The schema version is separate from the pi-container version. Keep them
in sync. The schema version triggers the compatibility check.

### User-facing behavior

When a user has an outdated config:

1. The launch fails with: "Configuration incompatible with this version of pi-container"
2. The error message lists the specific issues (missing fields, type mismatches, version mismatch)
3. The error message gives the remedy **for that specific failure**. The two kinds
   are not interchangeable

**Stale version, valid shape.** Only the `schema_version` string is behind. Every
field the new template requires is already present. Editing the string is
sufficient *and* lossless. It keeps whatever the user configured. Re-seeding here
would discard their settings to fix a string.

**A missing or mistyped field.** The template changed shape. No edit to
`schema_version` produces a key that is not in the file. Bumping the version alone
sends the user in a circle. They clear the version check and fail the field check
immediately after, one line further down. The file has to be re-seeded.

Re-seeding is **one file, not the directory**:

```
rm .pi-container/config.yaml && <re-run>
```

`_ensure_project_config()` only writes files that are absent. Deleting the one
file whose shape changed leaves `allowlist.yaml`, `token_replacer.yaml`,
`models.json`, `chat-templates/`, and `dependencies/` exactly as they were. Reach
for `rm -rf .pi-container` only when several of those are out of date at once.
it takes every hand-edited file in the workspace with it.

The user's own edits to `config.yaml` are not merged. The error tells them to
note their settings before deleting it.

> **During development, the version gate does not fire.** `schema_version` is
> bumped at release time. On an unreleased `main` the template still carries the
> *shipped* version. A workspace seeded at that same version therefore **passes**
> the version check and fails only on the field. This is why the remedy must
> never assume a version mismatch is what went wrong. This is the ordinary case for
> anyone developing pi-container in a workspace seeded before the field was added.

### Example: adding a new field

```yaml
# pi-coding-agent/default/config.yaml
schema_version: "0.2.0"  # Bumped from "0.1.0"

# ... existing fields ...

# New field in this release
custom:
  enabled: false
```

Users with `schema_version: "0.1.0"` in their local config see an error on
next launch. A *field* was added, so bumping their version string is not
enough. They must `rm .pi-container/config.yaml` and re-run to get the new field.
Everything else in `.pi-container/` is preserved.

## Release skill

The [release skill](https://github.com/mikkovihonen/pi-container/blob/main/.pi/skills/release/SKILL.md)
automates the version bump, changelog update, validation, and git tag steps
described in the next section. It is designed for use by pi, the coding agent:

```
pi> Release 0.2.0
```

### How it works

1. **Determine the version**. It asks the user for the version number, or
   suggests patch/minor/major based on the changes since the last tag.
2. **Run `release.sh`**. It checks the release preconditions (semver format, on
   `main`, clean tree, tag still free), bumps `pyproject.toml` and both
   `schema_version` fields, regenerates `uv.lock`, commits the bumps, then runs
   `validate_versions.py` + lint + tests.
3. **Update `CHANGELOG.md`**. It moves `Unreleased` entries into a new version
   block with today's date, then confirms the ordering with
   `release.sh --check-changelog`.
4. **Amend the release commit**. It adds the changelog update to the existing
   commit created by the script (never re-run the script, or versions get
   double-bumped).
5. **Tag and push**. It creates `v<version>` and pushes to `origin`.

CI triggers on the tag push and creates the GitHub Release automatically.

### When to use the skill vs. manual steps

| Scenario | Use |
|----------|-----|
| You're chatting with pi | Release skill |
| You need to do a release from a different machine | Manual steps in the next section |
| You need to inspect or customise each step | Manual steps |

The skill performs the same operations as the manual steps. It is a
convenience wrapper.

## Creating a release

The version is authoritative from the latest git tag. Three places must always
stay in sync:

| Source | Location |
|--------|----------|
| Git tag | `v<version>` (e.g. `v0.2.0`) |
| Python package version | `pyproject.toml` → `[project].version` |
| Config schema version | `pi-coding-agent/default/config.yaml` → `schema_version` |
| Runtime config version | `.pi-container/config.yaml` → `schema_version` |

The `validate_versions.py` script runs in CI (not pre-commit). git has
no `pre-tag` hook. The version cross-check can only pass once the tag exists.
That happens after the commit. The runtime config
(`.pi-container/config.yaml`) is checked at launch time by `src/config_schema.py`. If it does not match the latest git tag, the container refuses to start.

### Steps

1. **Make sure `main` is green**. CI must pass on the commit you want to
   release (`check` and `test` jobs).
2. **Update `CHANGELOG.md`**. Move the `[Unreleased]` entries into a new
   version block with the release date, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
3. **Bump the version** in `pyproject.toml` (`[project] version`).
4. **Regenerate `uv.lock`** with `uv lock` (the lockfile embeds the project
   version).
5. **Bump `schema_version` in the seed template** (`pi-coding-agent/default/config.yaml`). This is what new workspaces get on
   first run.
6. **Bump `schema_version` in the runtime config** (`.pi-container/config.yaml`). This is what the currently running container
   uses. Seeding is copy-once (missing-only), so updating the template alone
   will not update the runtime config.
7. **Validate locally** before pushing:

   ```bash
   uv run python3 .github/workflows/scripts/validate_versions.py
   pre-commit run --all-files --show-diff-on-failure
   uv run pytest --cov
   ```
8. **Commit, tag, and push**:

   ```bash
   git add CHANGELOG.md pyproject.toml \
       uv.lock \
       pi-coding-agent/default/config.yaml \
       .pi-container/config.yaml
   git commit -m "chore: release v0.2.0"
   git push origin main
   git tag -a v0.2.0 -m "Release v0.2.0"
   git push origin v0.2.0
   ```

### What CI does

The `ci.yml` workflow triggers on `push` to `refs/tags/v*`:

- **`check`** — runs lint, tests, and `validate_versions.py`.
- **`test`** — runs the full test suite with coverage, updates the coverage
  badge on `main`.
- **`release`** — if both jobs pass, creates a GitHub Release via
  `softprops/action-gh-release` with auto-generated release notes from merged
  commits since the last tag.

### Example

```bash
# 1. Update CHANGELOG.md: move [Unreleased] → [0.2.0] - 2026-07-04
# 2. Bump pyproject.toml version to "0.2.0"
# 3. Bump schema_version in pi-coding-agent/default/config.yaml to "0.2.0"
# 4. Bump schema_version in .pi-container/config.yaml to "0.2.0"
# 5. Regenerate uv.lock: uv lock
# 6. Validate and commit:
git add CHANGELOG.md pyproject.toml \
    uv.lock \
    pi-coding-agent/default/config.yaml \
    .pi-container/config.yaml
git commit -m "chore: release v0.2.0"
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin main
git push origin v0.2.0
```

### After the release

Users clone the repo, check out the tag, and run `build.sh` to build the container
images for their local runtime (`podman`).

If a workspace's `.pi-container/config.yaml` is outdated (e.g. a user skipped
step 5 above), the launch fails with:

> schema_version mismatch: config has '0.1.1', the latest pi-container
> version is '0.2.0'.

If that is the **only** error, the shape still validates. Editing
`schema_version` fixes it without losing any settings. If the release also added
or changed a field, the error list says so. The fix is
`rm .pi-container/config.yaml` plus a re-run (see
[User-facing behavior](#user-facing-behavior) for why the two are not
interchangeable).

## Rolling back

To revert a release, revert the commit on `main` and create a new patch release
(e.g., `v1.0.1`). Do not delete tags. They are historical record.

## Environment variables for the release job

The CI release job needs no extra secrets beyond what CI already provides.
`GITHUB_TOKEN` is used automatically by the GitHub Release action.

# Releases

[← Documentation index](../README.md) · [Getting Started](getting-started.md) · [Architecture](architecture.md) · [Configuration](configuration.md) · [Development](development.md)

This project is installed by cloning. Releases are **git tags** on `main`. Docker
images are built locally with `build.sh` — no image registry publishing. The
version is authoritative from the latest git tag (not `pyproject.toml`).

## Development model: trunk-based

Everyone works on short-lived branches off `main`. Changes land via pull requests.

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

Use the conventional-commit prefix pattern for clarity (not enforced by a hook):

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

- **0.x.y** — Pre-release. Breaking changes are possible without a major bump,
  but we try to avoid them.
- **1.x.y** — Stable. `MAJOR` bumps only for breaking changes.

The version is authoritative from the latest git tag (e.g., `git tag -l | head -1`).
`pyproject.toml` is informational only — the launch reads the version from git.

## Schema version: config compatibility

Every pi-container release includes a template for the per-project configuration
at `pi-coding-agent/default/`. This template is seeded into each workspace's
`.pi-container/` on first run.

The template `config.yaml` includes a `schema_version` field that matches the
pi-container version (e.g., `"0.1.0"`). At launch, the schema version in the
seeded config is checked against the app version (from the latest git tag). If
they don't match, the launch fails with a clear error message.

### When to bump the schema version

Bump the `schema_version` in `pi-coding-agent/default/config.yaml` whenever you:

- Add a new required field to the template
- Change the type of an existing field
- Remove a field that users might still have in their configs
- Add a new file to `pi-coding-agent/default/` (e.g., a new addon config)

The schema version is separate from the pi-container version — they should be
kept in sync, but the schema version is what triggers the compatibility check.

### User-facing behavior

When a user has an outdated config:

1. The launch fails with: "Configuration incompatible with this version of pi-container"
2. The error message lists the specific issues (missing fields, type mismatches, version mismatch)
3. The error message suggests: "delete .pi-container and re-run to re-seed"

Users can also manually update `schema_version` in their local `config.yaml` to
match the new version, but this is not recommended — they should re-seed to get
the latest defaults and new fields.

### Example: adding a new field

```yaml
# pi-coding-agent/default/config.yaml
schema_version: "0.2.0"  # Bumped from "0.1.0"

# ... existing fields ...

# New field in this release
custom:
  enabled: false
```

Users with `schema_version: "0.1.0"` in their local config will see an error on
next launch. They must `rm -rf .pi-container` and re-run to get the new field.

## Creating a release

1. **Make sure `main` is green** — CI must pass on the commit you want to release.
2. **Update the schema version** in `pi-coding-agent/default/config.yaml` if the
   template changed (new fields, changed types, new files).
3. **Update `CHANGELOG.md`** — Move `[Unreleased]` to the new version, add a date.
4. **Commit** with message `chore: release v1.2.3`.
5. **Tag** with `git tag -s v1.2.3 -m "Release v1.2.3"`.
6. **Push** the tag: `git push origin main --tags`.

CI detects the tag push and automatically:
- Runs the lint and test jobs (they must both pass).
- Creates a GitHub Release with the changelog notes.

Users clone the repo, check out the tag, and run `build.sh` to build the Docker
images for their local runtime (Apple `container`, `podman`, or `docker`).

### Example

```bash
# Bump schema_version in pi-coding-agent/default/config.yaml to "1.0.0"
# Update CHANGELOG.md: move [Unreleased] → [1.0.0] - 2026-07-04

git add pi-coding-agent/default/config.yaml CHANGELOG.md
git commit -m "chore: release v1.0.0"
git tag -s v1.0.0 -m "Release v1.0.0"
git push origin main --tags
```

## Creating a release

1. **Make sure `main` is green** — CI must pass on the commit you want to release.
2. **Update the version** in `pyproject.toml`.
3. **Update `CHANGELOG.md`** — Move `[Unreleased]` to the new version, add a date.
4. **Commit** with message `chore: release v1.2.3`.
5. **Tag** with `git tag -s v1.2.3 -m "Release v1.2.3"`.
6. **Push** the tag: `git push origin main --tags`.

CI detects the tag push and automatically:
- Runs the lint and test jobs (they must both pass).
- Creates a GitHub Release with the changelog notes.

Users clone the repo, check out the tag, and run `build.sh` to build the Docker
images for their local runtime (Apple `container`, `podman`, or `docker`).



## Rolling back

To revert a release, revert the commit on `main` and create a new patch release
(e.g., `v1.0.1`). Do not delete tags — they are historical record.

## Environment variables for the release job

The CI release job needs no extra secrets beyond what CI already provides.
`GITHUB_TOKEN` is used automatically by the GitHub Release action.

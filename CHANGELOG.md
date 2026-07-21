# Changelog

All notable changes to this project will be documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Breaking Changes
- Replaced `packages.txt` with two definition files: `.pi-container/dependencies/root/commands.sh` (runs at build time) and `.pi-container/dependencies/pi/commands.sh` (runs at runtime)
- Removed bind-mounted `.pi-container/agent/entrypoint.sh` hook — replaced by baked-in script execution
- Removed runtime `apt-get update && apt-get install` from `entrypoint.sh` — moved to build time
- **Dropped Apple `container` support** — `--build-context` flag not supported by Apple `container`, requires `docker` or `podman`
- Migration: move apt installs from `packages.txt` to `root/commands.sh` using `apt-get update && apt-get install -y <package>` syntax

### Added
- Project-specific agent images with content-addressed tags (`<project>-pi-agent-<sha256>.local`)
- Image label storage for cache invalidation (stores content hash in `pi-container.hash` label)
- Shared base image with common packages (bash, git, ripgrep, node, npm, pi, mitmproxy CA cert)
- Dependency definition file seeding from `pi-coding-agent/default/dependencies/` templates
- Build-time root commands execution (`root/commands.sh` for system-wide setup)
- Runtime pi commands execution (`pi/commands.sh` for workspace-local setup)
- Cross-workspace image sharing: identical definition files reuse the same image

### Changed
- Updated `Containerfile` to use `--build-context` for copying definition files
- Updated `build.py` to pass definition file paths and content hash to container builds
- Updated `run.py` to resolve project-specific image tags and check cache via image labels
- Updated `entrypoint.sh` to run pi commands at runtime if baked into the image
- Removed `pi/commands.sh` from image hash calculation (runs at entrypoint, not baked into image)
- Removed Apple `container` runtime support: dropped `AppleContainerRuntime` class, removed socat code (only needed for Apple container), updated tests, cleaned up references
- Updated documentation: `configuration.md`, `getting-started.md`, `AGENTS.md`, `project-specific-containers.md`

### Performance
- Eliminated redundant `apt-get update` and package installation at every container startup
- Cached project-specific images: subsequent runs skip build entirely (save 30-120 seconds)
- Rebuild only when definition files, Containerfile, or entrypoint.sh changes

## [0.2.1] - 2026-07-08

Documentation updates and uv dependency management.

## [0.2.0] - 2026-07-06

### Changed
- Bumped schema_version and project version from `0.1.9` to `0.2.0`.

## [0.1.9] - 2026-07-06

### Refactored
- Extracted shared schema validation logic into `src/schema_common.py`, deduplicating validators (`_validate_field`, `_validate_schema`, `_validate_models_schema`, `_validate_models_flags`, `_validate_hf_models`) between `config_schema.py` and CI's `validate_versions.py`.
- Extracted chat template path resolution into `src/template_paths.py` (`_resolve_chat_template_path`, `_check_chat_template_paths`).
- Extracted git tag version lookup into `src/version.py` (`get_git_tag_version`).
- Moved `_find_free_port` from `network.py` to `util.py` as `get_free_port`.
- Extracted IPv4 address parsing from `run.py` and `server.py` into `util.py` as `extract_ipv4_from_ip_addr`.

### Changed
- Bumped `pyyaml` to `>=6.0.3`.
- Added `docs` dependency group with `mkdocs-material>=9.7.6`.
- Updated dev dependencies: `pre-commit>=4.6.0`, `pytest>=9.1.1`, `pytest-cov>=7.1.0`, `ruff>=0.15.20`.
- Added `override-dependencies` for `msgpack>=1.2.1` and `tornado>=6.5.6`.
- Bumped schema_version and project version from `0.1.8` to `0.1.9`.

## [0.1.8] - 2026-07-05

### Added
- Documentation site powered by MkDocs Material, published to GitHub Pages on every push to `main`. The site is generated from `README.md` and live at <https://mikkovihonen.github.io/pi-container/>.

## [0.1.7] - 2026-07-05

### Changed
- Switched proxy container to use `uv` for dependency management instead of `pip` with pinned requirements.
- Split project dependencies into groups (`src`, `proxy`, `dev`) for cleaner isolation.

## [0.1.6] - 2026-07-05

### Changed
- Updated `requires-python` from `3.14.6` to `3.14` to improve dependabot compatibility.

## [0.1.5] - 2026-07-05

### Fixed
- Release script no longer fails on `_info: command not found` — CHANGELOG order check uses `echo` instead of the Python-only `_info` function.

## [0.1.4] - 2026-07-05

### Fixed
- Release script validation no longer fails when the new git tag has not yet been created (`validate_versions.py` now accepts `--new-version` to compare against the target version instead of the existing tag).
- Release script now uses `uv run pre-commit` so it works inside the project venv.

## [0.1.3] - 2026-07-05

### Added
- Seed `pi-coding-agent/default/entrypoint.sh` into `.pi-container/agent/` when
  missing, so users have a customizable entrypoint that runs before `pi` launches
  inside the container.

### Changed
- Flow export now copies raw `.jsonl` files as-is into the sessions directory
  instead of parsing, merging, and re-serializing them as `.json`.
- Flow export volume mount and proxy addon are now gated by
  `flow_export.enabled` in `.pi-container/config.yaml`; when disabled, no raw
  `flows-*.jsonl` files are created.

## [0.1.2] - 2026-07-04

### Changed
- Relaxed `.pi-container` shadowing: dependency manifest (`dependencies/`) is no longer bind-mounted inside the container; only the `exports` tmpfs is kept.

### Removed
- `deps_dir` variable and conditional bind mount for the dependency manifest (unused).

## [0.1.0] - 2026-07-04

Initial release.

### Added
- Containerized agent with transparent mitmproxy proxy for HTTP/HTTPS/DNS auditing.
- Per-workspace isolation: each workspace gets its own proxy, isolated network,
  mitmweb port, and seeded config.
- Configurable resource limits, tmpfs paths, flow export, and network settings
  via `.pi-container/config.yaml`.
- Agent environment variables and bind mounts via config.
- IPv6 support (off by default, opt-in per workspace).
- Runtime-agnostic: works with Apple `container`, `podman`, and `docker`.
- Allowlist and token-replacer proxy addons.
- Flow export: per-project capture of intercepted traffic for audit.

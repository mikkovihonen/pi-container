# Changelog

All notable changes to this project will be documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.9] - 2026-07-06

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

# Changelog

All notable changes to this project will be documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added
- **Multi-provider support and dynamic hostname resolution.** Multiple local `llama-server` providers and external cloud providers can now be configured in `.pi-container/agent/models.json`. Each local provider launches its own host server instance and routes via a unique container port.
- **Provider `baseUrl` validation in `models.json`.** `validate_models()` now validates `baseUrl` for all local providers (`serverCustomParameters`), enforcing `http://` or `https://` URLs, requiring explicit ports, rejecting loopback addresses (`localhost`/`127.0.0.1`/`::1`) with actionable guidance, and rejecting duplicate container ports across local providers before containers start.
- **Dynamic proxy DNS mapping for provider hostnames.** `run.py` and `ContainerNetworkManager` collect provider names and custom hostnames from `models.json` and pass them via `LLAMA_HOSTNAMES` to `pi-coding-agent-proxy`, which dynamically adds them to `/etc/hosts` so mitmproxy DNS resolves them to the proxy's internal address (`ETH1_IP`).

### Fixed
- **Multi-provider `baseUrl`s unreachable from within container.** Previously, the proxy only resolved the literal hostname `llama`, causing custom hostnames and provider names to fail DNS lookup, while multiple providers sharing port 9999 suffered iptables DNAT collisions.
- **Stale proxy port routing detection.** `ContainerNetworkManager._is_existing_proxy_healthy()` now validates that an already-running proxy container's `LLAMA_PORTS` and `LLAMA_HOSTNAMES` match the current run. When `llama-server` restarts on newly allocated host ports, stale proxy containers holding obsolete DNAT rules are automatically cleaned up and restarted with fresh port mappings.

## [0.5.1] - 2026-08-06

### Fixed
- **The proxy no longer installs packages from PyPI every time it starts.** The entrypoint ran `uv run mitmweb`, and `uv run` re-syncs `.venv` against the project's *default* dependency groups before exec'ing anything — while the image built the venv with `--only-group proxy --no-dev`. Every start downloaded the mismatch (~15s warm, unbounded cold), tripping `run.py`'s 30s proxy health probe *after* the images were built. The entrypoint now execs `/home/mitmproxy/.venv/bin/mitmweb` directly: no `uv` and no network at run time. Probe answers in ~0.3s.
- **The venv the proxy actually runs is now complete.** That startup sync was load-bearing by accident: `--only-group proxy` excludes the project's own `dependencies`, so the built venv had no `pyyaml`, which `allowlist.py` and `token_replacer.py` import at module load. Cutting the sync alone would have shipped a proxy whose allowlist died on `ModuleNotFoundError` — fail-open on the component whose job is to fail closed. The build now runs `uv sync --frozen --no-default-groups --group proxy`, and a second smoke test starts `mitmdump` with all three addons and the baked configs, so a missing dependency or unparseable config is a build failure rather than a silent runtime one.

## [0.5.0] - 2026-08-06

### Breaking Changes
- **`NESTED_CONTAINERS` is renamed `PI_CONTAINER_NESTED`, with no alias.** It was the one variable the agent injected without a prefix. No fallback ships because none is reachable — `run.py` rebuilds the project image on every launch, so the runner and the entrypoint that reads the flag are always the same version. A project script reading `NESTED_CONTAINERS` directly must switch.
- **`nested_containers.ports`, exposing a nested container's UI to the host.** A nested container's own `-p 3000:3000` publishes into the *agent's* network namespace and dead-ends one layer below the host. `ports.publish` adds that outer hop as `-p` flags on the agent container (`publish: [3000, 5173, "18080:8080"]`), and `ports.expose` scopes the bind to `127.0.0.1` (default) or every interface. Ports are declared rather than discovered because a container's published ports are fixed at start. **The field is required, so it breaks every already-seeded workspace**: an existing `config.yaml` has no `nested_containers.ports` and the launch stops with `nested_containers.ports: required field missing`. Fix with `rm .pi-container/config.yaml` and a re-run — the seeder only writes absent files, so nothing else is touched. Editing `schema_version` does not help; the key is missing, not mislabelled.

### Added
- **A `PI_CONTAINER_*` environment contract that project scripts can depend on**, replacing inference from side effects: `PI_CONTAINER=1` (the marker), `PI_CONTAINER_VERSION`, `PI_CONTAINER_HOST_IP` (where the agent is reachable *from inside a nested container* — the one address projects need and cannot guess), and `PI_CONTAINER_NESTED`. The first and third are `ENV` in the image so they survive `podman exec` and any shell the agent spawns; the other two describe the run and are injected by `run.py`. None propagate into nested containers, which keeps `PI_CONTAINER` meaning "I am the agent" rather than "I am somewhere under it".
- **Host-port preflight.** A conflicting port aborts the launch immediately, naming the port and the config key, rather than being rejected by `podman run` after the images are built. The probe binds the address the port will actually use, so `expose: lan` is checked against `0.0.0.0`.
- **Duplicate YAML keys are now a launch error, with a line number.** PyYAML's `safe_load` lets a repeated key overwrite the earlier one and parses cleanly, which for hand-edited config is the worst outcome — the file looks right, validation passes, and the setting simply has no effect. `config.yaml`, `allowlist.yaml` and `token_replacer.yaml` now use a loader that rejects duplicates and reports file and line, all checked before anything is built. The allowlist matters most: a discarded rule is a security-relevant failure invisible from the host. Merge keys still work, and CI applies the same loader to the seed template.
- **Documented constraint: `host.docker.internal` does not reach the agent from inside a nested container.** It resolves to the podman machine, not the agent, and the isolated network has no route either, so it times out rather than refusing — presenting as a slow service instead of a wrong address. The address that works is `169.254.1.2`. Two fixes that look right are documented as *not* working because both fail silently: `--add-host`/`extra_hosts:` (the name resolves to two addresses and Go's RFC 6724 sorting prefers the wrong one, so busybox `wget` confirms a fix the Go client still times out on), and `host_containers_internal_ip` in `containers.conf` (ignored under both sections). What remains is per-project: the literal address in a compose **named overlay**, which is inert for anyone who does not pass `-f`.

### Changed
- **The proxy image now runs the same CPython and `uv` as the agent**, copied from the toolchain image instead of `python:3.14.6-slim-trixie` plus a separately pinned `uv` binary. That was a second interpreter free to drift from the agent's — worth one answer for the process that terminates TLS on every request the agent makes. Both are now pinned once in `pi-coding-agent-builder/Containerfile`, and the staged interpreter's extension modules are imported at build time so a missing runtime library fails the build rather than surfacing at interception time.
- **The staged CPython tree is trimmed**, 474 MB → 175 MB, now that both images carry it: the regression suite (157 MB), the static `libpython3.14.a` (134 MB, installed twice), and the dead Tk stack. `*.pyc` files are kept, unlike the official `python:` images, because `/usr/local` is not writable by the `pi` user and they would never be regenerated. Dropping the static library gives up static embedding but not native wheel builds, which the build now proves by compiling a C extension and creating a venv. Net: proxy 417 → 509 MB, agent 1.6 → 1.3 GB, builder 790 → 486 MB.
- **The toolchain image stages one tree per component** — `/python/`, `/node/`, `/podman/`, `/network/` — instead of a merged `/out/`, so the proxy can take `/python/` alone rather than carrying Node, podman and netavark into the network chokepoint.
- **Build order is now builder → proxy → agent.** The proxy went first when it was the quick one; that stopped being possible once it stopped carrying its own interpreter.
- **Nested containers now default to a netavark bridge, not podman 6's rootless default of `pasta`.** Measured: with the agent on the `--internal` network, a pasta-backed nested port completed the handshake and stalled — the connection arrives carrying the agent's own address, which is also the address pasta hands the guest. A *routed* network works, which is why the original nested-containers verification never hit it. Bridge is also what `docker compose` already got, so plain `podman run -p` now behaves like the compose path.
- Egress interception is unaffected and was re-verified: a nested container on a bridge still cannot reach HTTPS or a raw IP, and still gets the injected mitmproxy CA. Publishing a port is inbound only.

## [0.4.2] - 2026-08-06

### Added
- **Preflight gate in `release.sh`**, run before any file is touched: the version is semver, `HEAD` is on `main`, the working tree is clean, and `v<version>` is not already tagged. A rejected release now leaves nothing half-bumped to clean up by hand.
- **`release.sh --check-changelog`**, a standalone mode the release skill invokes *after* the changelog is rewritten. The ordering check previously ran before the edit, so it could only catch pre-existing disorder, never the mistake it exists to prevent.
- Every failure after the version-bump commit now prints that commit's SHA and the `git reset --hard HEAD~1` that undoes it.

### Changed
- The lint step runs with `SKIP=pytest` and the suite runs once afterwards with `--cov`. `pre-commit run --all-files` triggers the `pytest` hook, so every release ran the full suite twice.
- The release skill stages `CHANGELOG.md` alone when amending, not `git add -A`, which would sweep any unrelated working-tree change into the release commit.

### Fixed
- **`release.sh` no longer fails on the first bump under macOS.** All three `sed -i` calls were GNU-only; BSD sed reads the argument after `-i` as a backup suffix, so each invocation died with `undefined label` and left the file unchanged. Substitutions now write through a temp file, the one form both implementations accept. This is why the v0.4.1 tag ships `0.4.0` in `pyproject.toml` and both `schema_version` fields.
- **The changelog order check no longer aborts the release silently.** It grepped for `^## \[Unreleased\]`, but the heading here is unbracketed, and under `set -euo pipefail` the failed pipeline terminated the script — so every run exited non-zero before the tests with no error printed. Both spellings are now accepted, and a miss reports rather than aborts.
- **A no-op version bump is now fatal rather than a warning.** The bump commit ran with `|| echo "(nothing to commit)"`, so a re-run against an already-bumped tree continued with no release commit — and the skill's next `git commit --amend` would rewrite whatever unrelated commit was at the tip of `main`.
- Skill and docs corrected to describe what the script actually does: it neither updates the changelog nor creates the tag, its steps were misnumbered, its closing instructions contradicted its own amend flow, and it pointed at `towncrier`, which is not a dependency of this project.

## [0.4.1] - 2026-08-06

### Added
- `_images_in_use()` and `_unused_volumes()` helpers: which images and volumes a container still holds open, so cleanup can skip them instead of attempting a removal the runtime will refuse. `_unused_volumes()` returns `None` rather than an empty set when the query fails, so "cannot tell" falls back to attempting the removal instead of silently skipping every volume.
- `_enumerate_project_images()` and `_is_protected_image()` helpers, backing the two fixes below.

### Changed
- `_list_project_images()` returns `(image_id, display_name, content_hash)` triples and no longer takes a `project_hash` argument it never filtered on.
- `pi-container.type` is set per build with `--label` instead of by the agent `Containerfile`, so it lands only on the final image and a half-finished build cannot be mistaken for an orphan by a concurrent run.

### Fixed
- **Startup no longer warns `Could not remove image <none>:<none>`.** Both cleanup passes enumerated images as `{{.Repository}}:{{.Tag}}`, which podman renders as the literal `<none>:<none>` once a rebuild untags the previous image — not a valid reference, so untagged images could never be reclaimed and warned on every start. Images are now acted on by ID.
- **The shared base image is no longer labelled `pi-container.type=project`.** `pi-coding-agent/Containerfile` hardcoded that label but builds both the shared base and the per-project images, so the base was stamped as a project image with blank values for every other label and the orphan pass could not tell it apart from a real one. Each builder now sets its own label, and because pre-existing images still carry the old one, cleanup additionally refuses outright to remove the base, proxy or builder image whatever its labels say.
- **A blank `pi-container.project.path` label is now treated as unverifiable, like a missing one.** The orphan rule was `Path(stored_path).exists()`, and `Path("")` is `PosixPath(".")` — always true, so blank-labelled images and volumes were kept forever.
- **Cleanup no longer attempts to remove an image or volume that a container still holds open**, which failed with `image is in use by a container` and warned on every start for as long as that container lived — the ordinary case for two sessions in one workspace. Such items are skipped with an INFO line and reclaimed once the container is gone.

## [0.4.0] - 2026-08-05

### Added
- **Toolchain builder image** (`pi-coding-agent-builder/`, built by `build.sh`). It compiles **Node 26.6.0**, CPython 3.14.6 (PGO) + `uv` + `podman-compose`, **podman 6.0.2** and **netavark/aardvark-dns 2.0.0** from source and stages them for a single `COPY --from` in the agent image. Its own image rather than a stage of the agent's, for two reasons: a project-specific rebuild now provably compiles nothing (a cache miss on the Python layer used to mean a ~10-minute PGO compile), and the toolchains — Go, Rust, four source trees, several GB — cannot leak into the shipped image. Each component builds in its own stage so bumping one cannot invalidate the others, and the shipped image is `FROM scratch`.
- **Explicit podman build tags.** podman's Makefile *derives* `BUILDTAGS` by probing the build host for `-dev` headers, so a packaged binary's feature set is an accident of the packager's environment. Ours is chosen for rootless podman inside an unprivileged container: `seccomp` (load-bearing — without it nested containers run unfiltered), `libsqlite3`, `containers_image_openpgp`, `exclude_graphdriver_btrfs`, `grpcnotrace`; and deliberately **not** `systemd` (a journald-capable podman also defaults to journald, and there is no journal here), `libsubid` or `apparmor`. The build asserts the tags are present in the binary and that `libgpgme` is not linked.
- Source integrity: every git tag is pinned to the **commit** it must resolve to (a tag is a mutable pointer) and every tarball to a SHA-256. `cargo` builds offline from verified vendor trees, so no dependency is fetched unverified.
- **Every pin is declared in one file.** Versions, commits, tarball SHA-256s and pip hash lists are `ARG`s in `pi-coding-agent-builder/Containerfile`; the build scripts read them from the environment and define no fallbacks, failing the build by name rather than silently using a value baked into a script. Bumping a component is a one-file edit and any pin can be overridden per build.
- Compilers are capped by **memory** rather than core count — `MemAvailable / peak-RSS-per-job`, never more than `nproc` — so a 4 GB VM with 10 cores no longer OOM-kills the compiler.
- **Nested containers** (`nested_containers` in `.pi-container/config.yaml`, **off by default**): the agent can run `podman build`, `podman run`, `docker compose up` and testcontainers as rootless podman inside its own container. Nested containers are children in the agent's mount and network namespaces, so they bind-mount `/workspace` directly and their traffic is NAT'd into the agent's stack — egress still transits mitmproxy, and the proxy needs no changes. The host runtime socket is never mounted and no `--privileged` or `--userns` override is involved, but enabling it does relax `label=disable`, `unmask=ALL` and `--cap-add SYS_ADMIN` (namespaced) in the agent container — measured as hard requirements of the inner runtime, and the reason it is off by default. See `docs/design/nested-containers.md`.
- Agent image ships the nesting toolchain: podman 6.0.2, netavark and aardvark-dns 2.0.0 from source, plus `crun`, `conmon`, `catatonit`, `passt`, `fuse-overlayfs`, `uidmap`, `libseccomp2` and `nftables` from apt, a `docker` → `podman` shim, and `podman-compose`. Also nested subuid/subgid ranges for `pi`, file capabilities on `newuidmap`/`newgidmap` (setuid-root was measured *not* to work), and a `containers.conf` drop-in injecting the mitmproxy CA into every nested container.
- Per-project nested image store: a named volume `pi-nested-<project-hash>`, reclaimed by the same orphan-cleanup rule as project images. `storage: tmpfs` selects a volatile RAM-disk store instead.
- Startup preflight: with nesting enabled, `run.py` warns when no container-registry hostname is allowed in `allowlist.yaml`, which would otherwise fail image pulls mid-session with mitmproxy's 403. The template ships a commented-out rule.
- Proxy certificate staleness detection: `build_proxy()` now stamps `pi-container.build.time`, and a project image older than the proxy is rebuilt automatically rather than running a stale mitmproxy CA.
- Helpers: `read_nested_containers_config()`, `nested_container_args()`, `nested_volume_name()`, `_ensure_nested_volume()`, `_cleanup_orphaned_nested_volumes()`, `_get_image_build_time()`, `_image_exists()`, `_project_image_build_reason()`.

### Changed
- **The agent image's base is `debian:trixie-slim`, not `node:26.3.1-trixie-slim`.** Node was the one component versioned by a base-image tag rather than by this repository. `NODE_SOURCE` selects how it is staged: `prebuilt` (default) takes the official nodejs.org tarball — seconds, and byte-identical to what the `node:` image shipped, since that image is the same tarball on the same Debian; `build` compiles from source (~65 min), which buys a trixie-native build and nothing else. An invalid value is rejected before the first image is built. Node also moves to **26.6.0**, since `v26.5.1` was a security release the old pin predated, and parity (`node`, `npm`, `npx`, the `nodejs` alias, full ICU, `Temporal`) is asserted in the build.
  - `Temporal` is verified explicitly: Node 26 implements it in Rust and `./configure` downgrades a missing Rust toolchain to a *warning*, silently producing a Node without the global.
  - Node is built with plain `make`, not `--ninja`, whose default `nproc+2` ignores the memory cap and OOMs a 4 GB builder.
- **`build.sh` now builds three images**: proxy → toolchain → agent. The order is load-bearing — the agent `COPY --from`s both of the others.
- **podman is no longer installed from apt.** Trixie ships 5.4.2; podman 6 requires netavark/aardvark **2.0.0** against trixie's 1.14.0, and requires exactly what this image already provides — cgroups v2, nftables and `pasta` only.
- `/etc/containers/policy.json` and `registries.conf` are written by the Containerfile. They came from `golang-github-containers-common`, dropped along with `podman-docker` because both pull Debian's podman 5.4.2 alongside the 6.x built here. podman refuses to run anything without `policy.json`, and short names need a search registry (`docker.io`, one entry, so no ambiguous-name prompt can hang a non-interactive agent).
- A missing **project** build timestamp now triggers a rebuild, which is the actual remedy; a missing **proxy** timestamp remains a hard error, since build ordering cannot be verified without it.
- **Python, `uv` and `podman-compose` moved into the shared base image**, out of each workspace's `dependencies/root/commands.sh`. That fixes two things: a workspace leaving `root/commands.sh` at its no-op default had *no Python interpreter at all*, while `pi/commands.sh`'s template instructs `python -m venv`; and every project rebuild recompiled CPython, the slowest and most OOM-prone step in the build. Existing workspaces can delete the Python section from their own `root/commands.sh`.
- Project-image staleness is judged against **both** shared images it copies from — the proxy (the CA) and the builder (the toolchain).
- **A podman machine of at least 4 GB is now required** (macOS/Windows), up from podman's 2 GiB default, since one PGO compile job peaks near 900 MiB. Enforced by a pre-build check that reads `MemAvailable` inside the VM and lists the fixes, instead of dying minutes in at `gcc: fatal error: Killed signal terminated program cc1`. `PYTHON_OPTIMIZE=0` builds without PGO; `PI_MEMORY_PREFLIGHT=0` skips the check.

### Fixed
- Project-specific images now stay in sync with the proxy image's mitmproxy CA certificate after `build.sh` rebuilds the proxy.
- `run.py` no longer aborts with "Could not read build timestamp from project image ... Rebuild required." when that image does not exist. A first run in a workspace now builds it as intended.

### Removed
- **Docker support (breaking).** `DockerRuntime`, its registry entry, and the `proxy_secondary_connect_argv()` hook (which existed only because `docker run` attaches a single network). `CONTAINER_RUNTIME` now accepts only `podman`. Stock Docker gives the agent container no user namespace — container uid 0 *is* host uid 0 — so nesting's flag set would carry a materially different security guarantee under the same config; `DockerRuntime` was also documented as untested.

## [0.3.3] - 2026-08-03

### Added
- Project-specific image cleanup: images are tagged with `pi-container-project-<project-hash>-<image-hash>.local` and carry labels (`pi-container.hash`, `pi-container.project.hash`, `pi-container.project.path`, `pi-container.build.time`, `pi-container.type`) for discovery and lifecycle management.
- Orphan detection: on every run, images whose `pi-container.project.path` label points to a missing directory (or have no path label at all) are automatically removed, preventing disk-space leaks from deleted or moved workspaces.
- Pre-build stale image cleanup: before building a new project-specific image, old images for the same project with mismatched content hashes are removed.

### Changed
- Image tag format: `pi-coding-agent-<hash>.local` → `pi-container-project-<project-hash>-<image-hash>.local` (distinct prefix prevents accidental pruning).
- `build_project_image()` accepts `project_hash`, `project_path`, and `build_timestamp` parameters for label injection.
- `Containerfile` sets `pi-container.project.path`, `pi-container.project.hash`, `pi-container.build.time`, and `pi-container.type` labels.

### Fixed
- Fixed OOM errors when building project-specific agent containers with podman's default 2GB RAM limit (`e166a05`).

## [0.3.2] - 2026-08-03

### Added
- Agent container `capabilities` and `devices` configuration via config.yaml (forwarded as `--cap-add` and `--device` flags to the container runtime).
- Structured logging in `build.py`: replaced `print()` calls with `logging` module and added `_run_command_with_logging` helper that streams subprocess output line-by-line.
- SHA256 verification of Python source tarball in `root/commands.sh`.
- Hash-verified uv installation via pip in `root/commands.sh`.

### Changed
- `root/commands.sh`: added progress logging, quieter build output, and hash-verified uv installation.
- `pi/commands.sh`: wrapped setup commands in a subshell with output suppressed.
- `Containerfile`: removed shared base apt packages section (now project-specific only).
- `entrypoint.sh`: removed obsolete Apple `container` reference from comment.
- `AGENTS.md`: added instructions for uv dependency management and handling unmet system package dependencies.
- `build.py`: added logging configuration and `--build-context` flag for project-specific `root/commands.sh` to support standalone `build.sh` invocation.

### Fixed
- `build.py` tests updated to mock `subprocess.Popen` instead of `subprocess.run` to match new implementation.

## [0.3.1] - 2026-07-21

### Added
- Agent container `capabilities` and `devices` configuration: add Linux capabilities (`agent.capabilities`) and passthrough devices (`agent.devices`) via config.yaml, forwarded as `--cap-add` and `--device` flags to the container runtime.
- Structured logging in `build.py`: replaced `print()` calls with `logging` module and added `_run_command_with_logging` helper that streams subprocess output line-by-line.

### Changed
- `root/commands.sh`: added progress logging, SHA256 verification of Python source tarball, quieter build output, and hash-verified uv installation via pip.
- `pi/commands.sh`: wrapped setup commands in a subshell with output suppressed.
- `Containerfile`: removed shared base apt packages section (now project-specific only).
- `entrypoint.sh`: removed obsolete Apple `container` reference from comment.
- `AGENTS.md`: added instructions for uv dependency management and handling unmet system package dependencies.

### Fixed
- `build.py` tests updated to mock `subprocess.Popen` instead of `subprocess.run` to match new implementation.

## [0.3.0] - 2026-07-21

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

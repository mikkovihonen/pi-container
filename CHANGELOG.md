# Changelog

All notable changes to this project will be documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## [0.5.0] - 2026-08-06

### Breaking Changes
- **`NESTED_CONTAINERS` is renamed `PI_CONTAINER_NESTED`, with no alias.** It was the one variable the agent injected without a prefix, and it predates there being a prefix to use. No compatibility fallback ships because none is reachable: `run.py` rebuilds the project image from this repo on every launch, so the runner and the entrypoint that reads the flag are always the same version — there is no window in which an old image meets a new runner. A project script reading `NESTED_CONTAINERS` directly must switch to `PI_CONTAINER_NESTED`.
- **`nested_containers.ports`, exposing a nested container's UI to the host.** Nesting exists so the agent can run real workloads — a dev server, a compose stack, a database admin UI — and many of those are only useful if a human can look at them. That was impossible: a nested container's own `-p 3000:3000` publishes into the *agent's* network namespace, and the agent container publishes nothing onward, so the port dead-ended one layer below the host. `ports.publish` adds that outer hop as `-p` flags on the agent container (`publish: [3000, 5173, "18080:8080"]`), and `ports.expose` scopes the bind to `127.0.0.1` (default) or every interface, mirroring `proxy.expose_ui`. Ports are declared in config rather than discovered because a container's published ports are fixed at start — pi-container cannot add one to a running agent. **This is a required field, so it breaks every already-seeded workspace**: `.pi-container/` is written once and never edited again, so an existing `config.yaml` has no `nested_containers.ports` and the launch stops with `nested_containers.ports: required field missing`. Fix with `rm .pi-container/config.yaml` and a re-run — the seeder only writes files that are absent, so that one file is regenerated and `allowlist.yaml`, `models.json`, `chat-templates/` and `dependencies/` are left alone. Editing `schema_version` does not help: the key is missing from the file, not mislabelled.

### Added
- **A `PI_CONTAINER_*` environment contract that project scripts can depend on.** Projects need to know they are running inside the agent — to pick a compose overlay, skip a test that wants host networking, or reach something at an address that only exists here — and were left inferring it from side effects. Four variables now say it outright: `PI_CONTAINER=1` (the marker), `PI_CONTAINER_VERSION` (the running pi-container version, from the validated `schema_version`), `PI_CONTAINER_HOST_IP` (where the agent is reachable *from inside a nested container*, the one address projects routinely need and cannot guess), and `PI_CONTAINER_NESTED` (set only when nesting is enabled). The marker and the address are `ENV` in the image, because they are true of the image and so survive `podman exec` and any shell the agent spawns; the version and the nesting flag describe the run and are injected by `run.py`. None of them propagate into nested containers, which is what keeps `PI_CONTAINER` meaning "I am the agent" rather than "I am somewhere under it" — and it is deliberately not a general am-I-containerised test, since `/run/.containerenv` already answers that and is true one layer down too.
- **Host-port preflight.** A conflicting port aborts the launch immediately, naming the port and the config key, rather than being rejected by `podman run` after the images are built and the proxy is up. The probe binds the address the port will actually use, so `expose: lan` is checked against `0.0.0.0` and not just loopback.
- **Duplicate YAML keys are now a launch error, with a line number.** The YAML spec requires unique mapping keys but PyYAML's `safe_load` does not enforce it: a repeated key overwrites the earlier one and parsing succeeds. For hand-edited config that is the worst available outcome — the file looks right, schema validation passes (the losing value is already gone by the time a validator sees the data), the run starts, and the setting simply has no effect, with nothing to grep for. Found the hard way when `publish: ["18080:8080"]` was added *above* the seeded `publish: []` rather than replacing it, and the port silently never got published. `config.yaml`, `allowlist.yaml` and `token_replacer.yaml` are now parsed with a `SafeLoader` subclass that rejects duplicates and reports the file and line of the second occurrence; all three are checked before anything is built or started. The allowlist matters most here — a discarded rule is a security-relevant failure that is invisible from the host, since that file is consumed inside the proxy container. Merge keys still work: `<<` is skipped, and an explicit key overriding a merged one stays legal. CI applies the same loader to the seed template, where a duplicate would be copied into every new workspace.
- **Documented constraint: `host.docker.internal` does not reach the agent from inside a nested container.** The mirror image of `ports.publish` — a nested container talking *back* to a service the agent runs beside it (an exporter, a mock API, a language server). Inside a nested container both `host.docker.internal` and `host.containers.internal` resolve to the podman machine's gvproxy address, which is the host machine, not the agent; the service is not there, and the isolated network has no route to it either, so the connection times out rather than being refused and presents as a slow service instead of a wrong address. Found via a Prometheus target stuck at `context deadline exceeded` behind a dashboard that rendered perfectly and showed nothing. The address that works is `169.254.1.2` — podman's own `pasta --map-guest-addr` for the rootless netns, which also explains why the bridge gateway refuses (`--no-map-gw`). Two fixes that look right are documented as not working, because both fail silently: `--add-host` / compose `extra_hosts:` leaves the name resolving to *two* addresses, and Go's RFC 6724 sorting prefers the global-scope wrong one over the link-local right one, so busybox `wget` confirms the fix while the Go client still times out; and `host_containers_internal_ip` in the image's `containers.conf` drop-in is ignored under both `[containers]` and `[network]`, verified against an override file proven to be read, because the agent carries `/run/.containerenv` and the nested podman propagates the parent's `/etc/hosts` entry ahead of the config key. What remains is per-project: the literal address wherever the target is configured, carried in a compose **named overlay** rather than an untracked file — an explicit `-f` list replaces compose's auto-discovery, so `docker-compose.pi-container.yml` is inert for everyone who does not pass it, while `docker-compose.override.yml` would apply to all of them. Documented with the measurement table, and with the caveat that `169.254.1.2` is coupled to podman's rootless-netns layout rather than a promised interface.

### Changed
- **The proxy image now runs the same CPython and the same `uv` as the agent**, copied from the toolchain image instead of coming from `python:3.14.6-slim-trixie` plus a `uv` binary pulled from `ghcr.io/astral-sh/uv` by digest. That was a second interpreter and a second `uv` pin next to the agent's, bumped by hand in a different file and free to drift — the proxy could be running a CPython patch release, an OpenSSL, or a `uv` the agent had never seen, which for the process that terminates TLS on every request the agent makes is a question worth having one answer to. Both are now pinned once, in `pi-coding-agent-builder/Containerfile`. `UV_PYTHON` and `UV_PYTHON_DOWNLOADS=never` keep `uv sync` on the interpreter that was copied in rather than a managed download, and the staged CPython's extension modules are imported at build time (`ssl`, `sqlite3`, `lzma`, `bz2`, `readline`, ...) so a missing runtime library fails the build instead of surfacing at interception time.
- **The staged CPython tree is trimmed of what neither image can reach**, 474 MB → 175 MB, which matters now that both images carry it: CPython's own regression suite (`test/`, 157 MB), the static `libpython3.14.a` (134 MB — installed twice, as two independent copies rather than hardlinks), and the Tk stack (`idlelib`, `tkinter`, `turtledemo`, 10 MB, dead because `tk-dev` is not installed so `_tkinter` was never built). Same trim the official `python:` images make, minus their `*.pyc` deletion — `/usr/local` is not writable by the agent's `pi` user, so a removed `.pyc` would never be regenerated and every import would re-parse source for the life of the image. Dropping the static library gives up `python-config --embed` and static embedding; it does **not** affect building native wheels, which link nothing, and the build now proves that rather than assuming it — it compiles a C extension against the staged headers, imports it with the staged interpreter, and creates a `python -m venv`, failing the build if any of that stops working. Net: proxy 417 MB → 509 MB, agent 1.6 GB → 1.3 GB, builder 790 MB → 486 MB.
- **The toolchain image stages one tree per component** — `/python/`, `/node/`, `/podman/`, `/network/` — instead of a single merged `/out/`. `COPY --from=<image>` can address paths but not stages, so separate roots are what let the proxy take `/python/` alone rather than carrying Node, podman and netavark into the network chokepoint. The agent image copies all four; each tree already mirrors its final layout, so every `COPY` is still a plain overlay onto `/`. The alternative — a merged `/out` plus a second copy of the Python tree for the proxy — was rejected as ~380 MB of duplication to work around a layout choice.
- **Build order is now builder → proxy → agent** (it was proxy → builder → agent). The proxy was built first because it is the quick one and a failure there was cheap; that stopped being possible once it stopped carrying its own interpreter.
- **Nested containers now default to a netavark bridge (`[containers] netns = "bridge"` in the image's `containers.conf` drop-in), not podman 6's rootless default of `pasta`.** This is what makes the above work, and it was measured rather than assumed: with the agent on the `--internal` network and the same outer `-p` in both cases, a plain listener in the agent's netns published to the host fine while the pasta-backed nested port timed out. The agent's socket table showed the handshake completing and then stalling — the connection arrives carrying the agent's own address as its source, which is also the address pasta hands the nested guest. On a *routed* network the same setup works (the source is the bridge gateway instead), which is why the original nested-containers verification never hit it. Bridge is also what `docker compose` already got, since it creates a user-defined network per project, so a plain `podman run -p` now behaves like the compose path instead of differently from it.
- Egress interception is unaffected and was re-verified under the new default: with the agent on the isolated network, a nested container on a bridge still cannot reach HTTPS or a raw IP, and still gets the injected mitmproxy CA. Publishing a port is inbound only — it creates no route out.

## [0.4.2] - 2026-08-06

### Added
- **Preflight gate in `release.sh`**, run before any file is touched: the version is semver, `HEAD` is on `main`, the working tree is clean, and `v<version>` is not already tagged. A rejected release now leaves nothing half-bumped to clean up by hand.
- **`release.sh --check-changelog`**, a standalone mode the release skill invokes *after* the changelog has been rewritten. The ordering check previously ran inside the main script, before the edit — it could only ever catch pre-existing disorder, never the mistake it exists to prevent (placing the new version block above `Unreleased`).
- Every failure after the version-bump commit now prints that commit's SHA and the `git reset --hard HEAD~1` that undoes it, instead of leaving the release half-applied with no stated way back.

### Changed
- The lint step runs with `SKIP=pytest` and the suite runs once afterwards with `--cov`. `pre-commit run --all-files` triggers the `pytest` hook, so every release ran the full suite twice.
- The release skill stages `CHANGELOG.md` alone when amending, not `git add -A`, which would sweep any unrelated working-tree change into the release commit.

### Fixed
- **`release.sh` no longer fails on the first bump under macOS.** All three `sed -i` calls were GNU-only; BSD sed reads the argument after `-i` as a backup suffix, so each invocation died with `undefined label` and left the file unchanged. Substitutions now write through a temp file, the one form both implementations accept. This is why the v0.4.1 tag ships `0.4.0` in `pyproject.toml` and both `schema_version` fields.
- **The changelog order check no longer aborts the release silently.** It grepped for `^## \[Unreleased\]`, but the heading in this file is `## Unreleased` — unbracketed. The match never succeeded, and under `set -euo pipefail` a failed pipeline inside a command substitution terminates the script, so every run exited non-zero directly after `=== Checking CHANGELOG order ===` and before the tests, with no error printed. Both spellings are now accepted and a miss reports rather than aborts.
- **A no-op version bump is now fatal rather than a warning.** The bump commit was made with `|| echo "(nothing to commit)"`, so a re-run against an already-bumped tree continued with no release commit in place — and the skill's next instruction, `git commit --amend`, would then rewrite whatever unrelated commit happened to be at the tip of `main`.
- Skill and docs corrected to describe what the script actually does: `SKILL.md` claimed in its frontmatter to update the changelog and create the tag (it does neither), numbered its steps 1, 2, 3, 5 with the `git tag` block orphaned under step 3, printed closing instructions that contradicted its own amend flow by creating a second commit, and pointed at `towncrier`, which is not a dependency of this project.

## [0.4.1] - 2026-08-06

### Added
- `_images_in_use()` and `_unused_volumes()` helpers: which images and volumes a container still holds open, so a cleanup pass can skip them instead of attempting a removal the runtime will refuse. The volume query is inverted (`volume ls --filter dangling=true`) because that is the form podman answers directly — `ps --format {{.Mounts}}` reports mount *destinations*, which cannot be mapped back to a volume name. A merely created, never-started container counts in both, matching `ps --all`. `_unused_volumes()` returns `None` rather than an empty set when the query fails, so "cannot tell" falls back to attempting the removal instead of silently skipping every volume.
- `_enumerate_project_images()` and `_is_protected_image()` helpers, backing the two fixes below.

### Changed
- `_list_project_images()` returns `(image_id, display_name, content_hash)` triples and no longer takes a `project_hash` argument. It never filtered on it despite its docstring — `_cleanup_stale_project_images()` does that against the `pi-container.project.hash` label, and still does.
- `pi-container.type` is set per build with `--label` instead of by the agent `Containerfile`. See below for why; a side effect is that the label now lands only on the final image, never on build intermediates, so a half-finished build cannot be mistaken for an orphan by a concurrent run.

### Fixed
- **Startup no longer warns `Could not remove image <none>:<none>: Error: parsing reference "<none>:<none>": invalid reference format`.** Both cleanup passes enumerated images as `{{.Repository}}:{{.Tag}}`, which podman renders as the literal `<none>:<none>` once an image loses its tag — which happens to the previous image every time a rebuild moves a tag. That string is not a valid image reference, so both the inspect and the removal failed against it: untagged images could never be reclaimed, and warned on every start. Images are now enumerated as `{{.ID}}\t{{.Repository}}:{{.Tag}}` and acted on by ID, with the name kept for log messages only.
- **The shared base image is no longer labelled `pi-container.type=project`.** `pi-coding-agent/Containerfile` hardcoded that label, but it builds two different things: the shared base (`build_agent()`, which passes none of the label ARGs) and the per-project images (`build_project_image()`, which passes all of them). The base — and every untagged predecessor of it — was therefore stamped as a project image with blank values for every other label, and the orphan pass could not tell it apart from a real project's image. Each builder now sets the label itself. Because images built before this change are still on disk carrying the old label, `_cleanup_orphaned_project_images()` additionally refuses outright to remove the shared base, proxy or builder image whatever its labels say, comparing tags ignoring the `localhost/` prefix podman prepends to locally-built images.
- **A blank `pi-container.project.path` label is now treated as unverifiable, like a missing one.** The orphan rule was implemented as `Path(stored_path).exists()`, and `Path("")` is `PosixPath(".")`, which always exists — so blank-labelled project images and nested-storage volumes were kept forever rather than reclaimed.
- **Cleanup no longer attempts to remove an image or volume that a container still holds open**, which failed with `image is in use by a container` and warned on every start for as long as that container lived. This is the ordinary case for concurrent sessions in one workspace: change a definition file, start a second session, and the stale-image pass targets the image the first session is still running on. Such images and volumes are now skipped with an INFO line and reclaimed by a later run, once the container is gone. The check runs only after something has been established as a removal candidate, so images and volumes belonging to live projects stay silent.

## [0.4.0] - 2026-08-05

### Added
- **Toolchain builder image** (`pi-coding-agent-builder/`, tag `pi-coding-agent-builder:local`, built by `build.sh` between the proxy and the agent). It compiles **Node 26.6.0**, CPython 3.14.6 (PGO) + `uv` + `podman-compose`, **podman 6.0.2**, and **netavark/aardvark-dns 2.0.0** from source, stages them under `/out`, and the agent image picks them up with a single `COPY --from`. Two reasons this is its own image rather than a stage of the agent Containerfile: a project-specific image rebuild now provably compiles nothing (before, a cache miss on the Python layer turned it into a ~10-minute PGO compile), and the toolchains — Go, Rust, four source trees, several GB — cannot leak into the shipped image. Each component builds in its **own stage** so bumping one cannot invalidate the others (Node is on the order of an hour on a 4 GB machine; podman is minutes), and the shipped builder image is a `FROM scratch` holding nothing but the staged trees.
- **Explicit podman build tags.** podman's Makefile *derives* `BUILDTAGS` by probing the build host for installed `-dev` headers, so a packaged binary's feature set is an accident of the packager's environment. Ours is chosen for a rootless podman running inside an unprivileged container: `seccomp` (load-bearing — without it nested containers would run unfiltered), `libsqlite3`, `containers_image_openpgp` (Go-native OpenPGP instead of cgo `gpgme` + the separate `podman-sequoia` library), `exclude_graphdriver_btrfs`, `grpcnotrace`; and deliberately **not** `systemd` (a journald-capable podman also *defaults* to journald, and there is no journal in this container — the default is now `k8s-file`), **not** `libsubid`, **not** `apparmor`. The build asserts the tags are actually present in the binary (`go version -m`) and that `libgpgme` is not linked. Result links only `libsqlite3`, `libseccomp`, `libc`, `libm`.
- Source integrity: every git tag is pinned to the **commit** it must resolve to (a tag is a mutable pointer; `refs/tags/X^{}` is the real pin) and every tarball — CPython, Node, Go, Rust, and upstream's Rust vendor trees — to a SHA-256. `cargo` builds offline from those vendor trees, so no dependency is fetched unverified.
- **Every pin is declared in one file.** All versions, git commits, tarball SHA-256s and pip hash lists are `ARG`s in `pi-coding-agent-builder/Containerfile`, on the stage that consumes them; the build scripts read them from the environment (podman exports build args to `RUN`) and define no fallbacks. `require_env` fails the build naming the missing `ARG` rather than letting a renamed or dropped ARG silently build from a value baked into a script. So bumping a component is a one-file edit, `git log -p` on that file is the toolchain's version history, and any pin can be overridden per build (`--build-arg PODMAN_VERSION=... --build-arg PODMAN_COMMIT=...`). The Rust pin is declared before the first `FROM` and inherited by the two stages that re-declare it — netavark/aardvark-dns and Node's `Temporal` — so it stays one pin rather than two that can drift.
- Compilers are capped by **memory** rather than core count: `make -j`, `go build -p` and `cargo --jobs` each get `MemAvailable / peak-RSS-per-job`, never more than `nproc`. A 4 GB VM with 10 cores no longer OOM-kills the compiler.
- **Nested containers** (`nested_containers` in `.pi-container/config.yaml`, **off by default**): the agent can run its own containers — `podman build`, `podman run`, `docker compose up`, testcontainers — as rootless podman *inside* the agent container. Nested containers are children in the agent's mount and network namespaces, so they bind-mount its `/workspace` directly and their traffic is NAT'd (via `pasta`) into the agent's own stack: egress still transits mitmproxy and the proxy needs no changes. The host runtime socket is never mounted, and no `--privileged`, `seccomp=unconfined` or `--userns` override is involved. Enabling it does relax three things in the agent container — `label=disable`, `unmask=ALL` and `--cap-add SYS_ADMIN` (namespaced: container uid 0 is an unprivileged host user); the latter two were measured as hard requirements of the inner runtime. Off by default for exactly that reason. See `docs/design/nested-containers.md`.
- Agent image ships the nesting toolchain: **podman 6.0.2, netavark and aardvark-dns 2.0.0 built from source** (see below), plus `crun`, `conmon`, `catatonit`, `passt`, `fuse-overlayfs`, `uidmap`, `libseccomp2` and `nftables` (netavark shells out to `nft` for the per-project network `compose` creates) from apt, a one-line `docker` → `podman` shim, and `podman-compose` as the compose provider. Also nested subuid/subgid ranges for `pi`, `cap_setuid`/`cap_setgid` file capabilities on `newuidmap`/`newgidmap` (setuid-root was measured NOT to work), and a `containers.conf` drop-in that injects the mitmproxy CA into every nested container.
- Per-project nested image store: a named volume `pi-nested-<project-hash>` labelled `pi-container.type=nested-storage` (plus project hash/path), reclaimed by the same orphan-cleanup rule as project images. `storage: tmpfs` selects a volatile RAM-disk store instead.
- Startup preflight: with nesting enabled, `run.py` warns when no container-registry hostname is allowed in `allowlist.yaml` (image pulls would otherwise fail mid-session with mitmproxy's 403). `allowlist.yaml` ships a commented-out `container-registries-allow` rule.
- `read_nested_containers_config()`, `ContainerRuntime.nested_container_args()` / `nested_volume_name()`, `_ensure_nested_volume()`, `_cleanup_orphaned_nested_volumes()`.
- Proxy image build timestamp: `build_proxy()` now sets `pi-container.build.time` and `pi-container.type=shared` labels on the proxy image, enabling age comparison with project-specific images.
- Proxy certificate staleness detection: before building a project-specific image, `run.py` compares the proxy image's build time against the project image's build time. If the proxy is newer, the project image has a stale mitmproxy CA certificate and is automatically rebuilt.
- `_get_image_build_time()` helper: reads the `pi-container.build.time` label from any image and returns a UTC datetime, or `None` if absent.
- `_image_exists()` helper: reports whether an image is present in the local image store, distinguishing "not built yet" from "built but unreadable".
- `_project_image_build_reason()` helper: consolidates the build/reuse decision for project-specific images and returns the reason a build is needed (or `None` to reuse the cache).

### Changed
- **The agent image's base is `debian:trixie-slim`, not `node:26.3.1-trixie-slim`.** Node was the one component whose version was chosen by a base-image tag rather than by this repository; it now comes from the toolchain image like everything else. **`NODE_SOURCE`** selects how: `prebuilt` (default) stages the official nodejs.org tarball — seconds, and byte-identical to what the `node:` image shipped, since that image is this same tarball extracted into `/usr/local`; `build` compiles from source (~65 min), which buys a trixie-native build and nothing else. Both modes run the same parity checks. An invalid value is rejected before the first image is built, so a typo cannot cost a proxy rebuild (which would invalidate every project image). The `node:` images *are* `debian:trixie-slim` plus a Node tarball, so this is the same Debian with the tarball replaced by a build. Node also moves 26.3.1 → **26.6.0**, because `v26.5.1` was a security release the old pin predated. Parity with what that image provided (`node`, `npm`, `npx`, the `nodejs` alias, full ICU, `Temporal`) is asserted in the build; it shipped no `yarn` and no `corepack`, so neither is missing here.
  - The build verifies `Temporal` explicitly, twice: Node 26 implements it in Rust, and `./configure` **downgrades a missing Rust toolchain to a warning** and silently produces a Node without the global. The Rust pin is shared between this and netavark: it is the one `ARG` declared before the first `FROM`, which both stages inherit.
  - Node is built with plain `make`, not `--ninja`: node's Makefile does not forward `make -j` to ninja, which defaults to `nproc+2` and would ignore the memory cap and OOM a 4 GB builder.
- **`build.sh` now builds three images**: proxy → toolchain → agent. The order is load-bearing (the agent `COPY --from`s both of the others). Measured on a 9-core/8 GB applehv machine, the toolchain image is a few minutes with the default `NODE_SOURCE=prebuilt`, and ~65 minutes longer with `NODE_SOURCE=build`.
- **podman is no longer installed from apt.** Debian trixie ships 5.4.2 (March 2025); podman 6 requires exactly what this image already provides and drops what it does not — cgroups v2 only, nftables only, `pasta` only (no `slirp4netns`, no CNI) — and its release notes require netavark/aardvark **2.0.0** against trixie's 1.14.0.
- `/etc/containers/policy.json` and `registries.conf` are written by the Containerfile. They used to come from `golang-github-containers-common`, which is dropped along with `podman-docker` because both depend on Debian's podman and would install 5.4.2 alongside the 6.x built here. podman refuses to pull or run anything without `policy.json`, and short names need a search registry to resolve against (`docker.io`, one entry, so there is no ambiguous-name prompt to hang a non-interactive agent).
- A missing **proxy** build timestamp remains a hard error (`sys.exit(1)`) — build ordering cannot be verified without it. A missing **project** build timestamp now triggers a rebuild instead, which is the actual remedy.
- **Python, `uv` and `podman-compose` moved into the shared base image**, out of each workspace's `.pi-container/dependencies/root/commands.sh`. Two problems this fixes: a workspace that left `root/commands.sh` at its no-op default had **no Python interpreter at all** — while `pi/commands.sh`'s own template instructs `python -m venv` — and every project-specific image rebuild recompiled CPython, the slowest and most OOM-prone step in the build. It is now compiled once by `build.sh`, in the new toolchain image. Existing workspaces can delete the Python section from their own `root/commands.sh`.
- Project-image staleness is now judged against **both** shared images it copies content from — `pi-coding-agent-proxy:local` (the mitmproxy CA) and `pi-coding-agent-builder:local` (the toolchain) — via `_newest_shared_image_time()`. The toolchain is not part of `_compute_image_hash()`: the project image consumes it as a built image, not as source, so what matters is when that image was last built.
- **A podman machine of at least 4 GB is now required** (macOS/Windows), up from podman's 2 GiB default: the toolchain image compiles CPython, and one PGO compile job peaks near 900 MiB. Documented in the README and Getting Started; enforced by the pre-build check below.
- Pre-build memory check: `build.sh` reads `MemAvailable` inside the podman VM and refuses to start when there is not enough room to compile CPython (~900 MiB for one PGO job), listing the fixes — instead of dying minutes in at `gcc: fatal error: Killed signal terminated program cc1`. It applies to `build.sh` only — `run.py`'s project-image build compiles nothing now. `PYTHON_OPTIMIZE=0` builds without PGO (much lower memory, ~10-20% slower Python); `PI_MEMORY_PREFLIGHT=0` skips the check.

### Fixed
- Project-specific images now stay in sync with the proxy image's mitmproxy CA certificate after `build.sh` rebuilds the proxy.
- `run.py` no longer aborts with "Could not read build timestamp from project image ... Rebuild required." when the project-specific image does not exist. A first run in a workspace — or the run immediately after stale-image cleanup pruned the previous image following a `commands.sh`/`Containerfile` change — now builds the image as intended.

### Removed
- **Docker support (breaking).** `DockerRuntime`, its registry entry, and the `proxy_secondary_connect_argv()` hook (which existed only because `docker run` attaches a single network, so the post-run `network connect` step in `network.py` is gone too). `CONTAINER_RUNTIME` now accepts only `podman`. Stock Docker gives the agent container no user namespace — container uid 0 *is* host uid 0 — so nesting's flag set would carry a materially different security guarantee under the same config; `DockerRuntime` was also documented as untested.

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

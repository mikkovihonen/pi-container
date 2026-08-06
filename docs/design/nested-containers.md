# Nested Containers — Design

Enable the `pi-coding-agent` to run containers *inside* its own container — `podman
build`, `podman run`, `docker compose up`, testcontainers-style integration tests —
without opening a path around the mitmproxy interception layer or onto the host.

**Recommendation: rootless podman-in-podman inside the agent container, and drop
Docker as a supported host runtime.** Every claim below was measured on this host
(podman 6.0.2, `applehv` machine, macOS/arm64); the commands are in
[Appendix A](#appendix-a-verification-log).

---

## Problem

The agent container is a deliberate dead end. It attaches to a single `--internal`
network with no gateway, its default route and DNS are pointed at the proxy, and the
proxy's `FORWARD` chain is default-`DROP` so only HTTP/HTTPS/DNS (transparently
REDIRECTed into mitmproxy) can leave. It runs as unprivileged `pi`, with `/home/pi`
on tmpfs so nothing persists.

None of the tooling to start a container is present:

```
$ podman run --rm --entrypoint sh pi-coding-agent:local \
    -c 'command -v newuidmap fuse-overlayfs slirp4netns pasta podman || echo NONE-OF-THESE'
NONE-OF-THESE
```

So any agent task that needs a container — run Postgres for a test, build and smoke-test
an image, `docker compose up` a dev stack — fails outright. That is a large and growing
class of ordinary development work.

Three properties must survive whatever we add:

| Invariant | Why it matters |
|---|---|
| **No uninspected egress** | Every byte the agent's workload sends must still transit mitmproxy (or a port explicitly opened in `egress.allow`). |
| **No host reachability** | The agent must not gain any handle on the host or on the host's container runtime. |
| **Agent stays non-root** | The agent process runs as `pi`; nesting must not require giving it root. |

---

## Rejected approaches

### Mounting the host runtime socket (docker-outside-of-docker)

`--volume /var/run/docker.sock:/var/run/docker.sock` is the usual answer and is
**categorically unacceptable here**. That socket is a full-privilege API to the host's
runtime. An agent holding it can run:

```
podman run -v /:/host --privileged --network host <anything>
```

…which yields the host filesystem, host networking (bypassing the proxy entirely), and
effectively host root. It doesn't weaken pi-container's isolation model, it deletes it.
This must never be offered, not even as an opt-in.

### Docker-in-Docker (`--privileged` daemon)

Classic DinD needs `--privileged` on the agent container: all capabilities, all devices,
no seccomp, no SELinux. Same objection, slightly indirected. It also can't run rootless.

### A sibling "container engine" sidecar

Run a podman-service container on the isolated network and point the agent at it via
`DOCKER_HOST=tcp://…`. This keeps the agent unprivileged, but:

- The TCP socket is the same full-privilege API as above, just aimed at the sidecar
  instead of the host — the agent can escape *into the sidecar* and take whatever the
  sidecar can reach.
- Containers it starts are **siblings**, so bind mounts of `/workspace` paths resolve in
  the sidecar's filesystem, not the agent's. Every path-based workflow (`compose` with
  relative volumes, testcontainers copying fixtures) breaks or needs the workspace
  double-mounted.
- Published ports land in the sidecar's netns, not the agent's, so `localhost:5432`
  from the agent doesn't reach them.

More moving parts, worse ergonomics, and a weaker boundary than the accepted design.

---

## Verified host baseline

Measured before designing anything, because the whole approach hinges on it:

| Property | Measured value | Consequence |
|---|---|---|
| Podman | 6.0.2, rootless=`true`, cgroup v2, systemd manager | Rootless nesting is the supported path |
| VM user | `core`, uid 501 | — |
| `/etc/subuid` (VM) | `core:100000:1000000` | 1 M subordinate UIDs available |
| Container `uid_map` | `0→501 (1)`, `1→100000 (1000000)` | Container UIDs **1…1000000** are mapped and usable for a nested range |
| `/dev/fuse`, `/dev/net/tun` | present in VM, mode `0666` | Passable via `--device` |
| SELinux | **Enforcing** | Drives the security-option decision below |
| VM RAM | **2 GiB** | tmpfs image storage is untenable (see [Storage](#3-storage-a-per-project-volume-not-tmpfs)) |
| Nested userns as uid 1000 | `unshare -U -r id` → `uid=0(root)` | Works with no extra flags — no `--privileged`, no seccomp change |

That last row was originally read as the important one: creating a nested user namespace
needs nothing special, and everything else is plumbing. Half right. The *namespace* is
free, but **starting a container inside it is not** — podman's default seccomp profile
gates `mount`/`sethostname` on `CAP_SYS_ADMIN`, and its read-only `/proc` binds are
locked in the nested namespace. Both surfaced only when a real nested container was
started from the built agent image, and both are now in
[Host runtime flags](#1-host-runtime-flags) and [Appendix B](#appendix-b-implementation-time-verification-agent-image-not-the-reference-image).
The lesson: verifying a primitive does not verify the feature.

---

## Design: rootless podman inside the agent

The agent runs its own rootless podman as `pi`. Containers it starts are **children** —
in the agent's mount and network namespaces — so they inherit the agent's routing (hence
the proxy) and can bind-mount the agent's view of `/workspace` directly.

```mermaid
flowchart TB
    subgraph agent["<b>pi-coding-agent</b> (user pi, rootless)"]
        direction TB
        pi["pi agent process"]
        pod["podman (rootless, uid 1000)<br/>subuid 1:999 + 1001:64535"]
        subgraph inner["nested containers"]
            direction LR
            c1["postgres"]
            c2["compose svc"]
        end
        store["nested image store<br/>volume: pi-nested-&lt;project-hash&gt;<br/>driver: overlay"]
    end
    style agent fill:none,text-align:left
    style inner fill:none,text-align:left

    eth0["agent eth0<br/>isolated-net (no gateway)"]
    proxy["<b>pi-coding-agent-proxy</b><br/>REDIRECT 80/443 → mitmproxy :8080<br/>REDIRECT 53 → DNS :5353<br/>FORWARD policy DROP"]

    pi --> pod
    pod --> inner
    pod -.-> store
    inner -->|pasta userspace NAT<br/>into the agent's netns| eth0
    eth0 --> proxy
    proxy -->|verified: no bypass| internet["internet"]
```

### 1. Host runtime flags

Added to the agent `podman run` when nesting is enabled:

| Flag | Why |
|---|---|
| `--device /dev/fuse` | `fuse-overlayfs` fallback when native rootless overlay is unavailable |
| `--device /dev/net/tun` | `pasta` needs it to create the inner tap device |
| `--security-opt label=disable` | Required on an SELinux-enforcing host — see [SELinux](#5-selinux-the-one-real-concession) |
| `--security-opt unmask=ALL` | **Correction, measured during implementation.** Podman bind-mounts parts of `/proc` read-only and masks others in the agent container; those mounts are *locked* in the nested user namespace. Without this, the inner runtime dies with `crun: open /proc/sys/net/ipv4/ping_group_range: Read-only file system`. |
| `--cap-add SYS_ADMIN` | **Correction, measured during implementation.** Podman's default seccomp profile permits `mount`/`sethostname`/`umount2`/`pivot_root` only when `CAP_SYS_ADMIN` is in the container's capability set, and a seccomp filter cannot be relaxed from inside a nested userns. Without this, the inner runtime dies with `crun: sethostname: Operation not permitted`. |
| `--volume pi-nested-<project-hash>:/home/pi/.local/share/containers` | Persistent nested image store |
| `--env XDG_RUNTIME_DIR=/run/user/1000` | Rootless podman's lock/pid directory |
| `--env PI_CONTAINER_NESTED=true` | Entrypoint gate (see below) |

The two corrections above replace this design's original claim that nesting needs no
capability or seccomp relaxation. That claim came from testing only the *primitive*
(`unshare -U -r id`, which does work unaided); starting an actual nested container
needs both flags. Measured evidence, in the built agent image:

```
# inside podman's rootless userns as pi, the capability set IS full…
CapEff: 000001ffffffffff   uid_map: 0 1000 1 / 1 1 999 / 1000 1001 64535
# …yet the syscalls podman's seccomp profile gates on CAP_SYS_ADMIN are refused:
sethostname=EPERM  mount-proc=EPERM
# outer: label=disable                        → crun: sethostname: Operation not permitted
# outer: label=disable + SYS_ADMIN            → crun: ping_group_range: Read-only file system
# outer: label=disable + unmask=ALL           → crun: sethostname: Operation not permitted
# outer: label=disable + unmask=ALL + SYS_ADMIN → INNER-STARTED-OK, CA mounted, HTTPS OK
```

`CAP_SYS_ADMIN` is **namespaced** here: the agent container's userns maps container
uid 0 to an unprivileged host user, so the capability applies to what that namespace
owns, not to the host. Still notably **absent**: `--privileged`,
`--security-opt seccomp=unconfined` (the filter stays active for everything it does
not gate on `CAP_SYS_ADMIN`), `--userns` overrides, and any host socket. The agent
keeps its existing `NET_ADMIN`. This is a larger relaxation than this design
originally promised, and it is the main reason nesting must stay opt-in.

### 2. Image additions

Added to `pi-coding-agent/Containerfile`. Debian trixie supplies the parts it ships
at a version podman 6 accepts; `podman`, `netavark` and `aardvark-dns` are compiled
in `pi-coding-agent-builder/` and copied in (see
[Toolchain builder image](../architecture.md#toolchain-builder-image) — trixie's
podman is 5.4.2 and its netavark 1.14.0, which podman 6 does not accept):

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
      catatonit conmon crun fuse-overlayfs libcap2-bin libseccomp2 \
      nftables passt uidmap \
 && rm -rf /var/lib/apt/lists/*

# Node + CPython/uv/podman-compose + podman + netavark + aardvark-dns, prebuilt.
# One staged tree per component; the proxy image copies /python/ only.
COPY --from=pi-coding-agent-builder:local /python/ /
COPY --from=pi-coding-agent-builder:local /node/ /
COPY --from=pi-coding-agent-builder:local /podman/ /
COPY --from=pi-coding-agent-builder:local /network/ /

# Nested subordinate ID ranges for `pi` (uid 1000). These must fall INSIDE the UID
# range the outer user namespace maps (verified: container UIDs 1..1000000). The
# split around 1000 mirrors quay.io/podman/stable and stays valid even on hosts with
# only the conventional 65536-wide subuid range.
RUN printf 'pi:1:999\npi:1001:64535\n' > /etc/subuid \
 && cp /etc/subuid /etc/subgid \
 # newuidmap/newgidmap need CAP_SETUID/CAP_SETGID to write the nested namespace's
 # id maps. File capabilities, NOT setuid-root — see the note below.
 && chmod 0755 /usr/bin/newuidmap /usr/bin/newgidmap \
 && setcap cap_setuid+ep /usr/bin/newuidmap \
 && setcap cap_setgid+ep /usr/bin/newgidmap
```

Four corrections found while implementing this, all measured in the agent image
(see [Appendix A](#appendix-a-verification-log)):

- **`containers-common` does not exist on Debian.** The package is
  `golang-github-containers-common` (a hard dependency of Debian's `podman`).
  Moot in the end — see the last bullet — but `passt` and `uidmap` were only
  *Recommends* of `podman`, so they always had to be listed by name.
- **`chmod u+s` on `newuidmap` does not work; `setcap cap_setuid+ep` does.** A
  setuid-root `newuidmap` run by `pi` *does* acquire `CAP_SETUID` (measured:
  `CapEff: 00000000800405fb`, euid 0) and still fails the kernel's id-map
  permission check — `newuidmap: write to uid_map failed: Operation not permitted`
  — for both a single-extent and podman's full three-extent map. With
  `cap_setuid=ep` (and the setuid bit removed) the identical map succeeds and the
  nested `podman info` reports `rootless=true`, `overlay`. This is also what
  `quay.io/podman/stable` does upstream, so the original reasoning here had it
  backwards.
- **`nftables` is required for `compose`.** A plain nested `podman run` reaches the
  network through `pasta` and needs nothing extra, but `docker compose` creates a
  user-defined network per project and netavark sets that bridge up itself — without
  the `nft` binary the service fails to start with `netavark: nftables error: unable
  to execute nft`. Verified: with `nftables` installed, `docker compose up` inside the
  agent container runs a service, sees the injected CA at `/etc/pi-container-ca.crt`,
  and — on the `--internal` network — still cannot reach the internet.
- **A `docker` shim ships after all** (open question 5): several tools shell out to
  `docker` by name, and the shim is a one-line `exec podman "$@"` at
  `/usr/local/bin/docker`. A **compose provider** ships too: `podman-compose`,
  pip-installed onto the base image's CPython (the apt package would pull in Debian's
  separate `python3`). It is pinned via `[engine] compose_providers` because podman's
  default list tries `docker-compose` first and would report it missing instead of
  using the provider that is installed. This is what motivated moving the CPython
  build out of each workspace's `root/commands.sh` and into the shared base image.
- **Debian's podman is too old for this design, and its build tags are the wrong
  ones.** trixie ships 5.4.2 (March 2025). podman 6 requires precisely what this
  image already has and drops what it does not — cgroups v2 only, nftables only,
  `pasta` only (no slirp4netns, no CNI) — and its release notes require
  netavark/aardvark **2.0.0** against trixie's 1.14.0. Separately, podman's Makefile
  *derives* `BUILDTAGS` from whichever `-dev` headers the packager happened to have,
  which is how a distro build ends up with the `systemd` tag and therefore a journald
  log-driver default in a container that has no journal. All three are now built from
  source with an explicit tag set; `podman-docker` and
  `golang-github-containers-common` are dropped, because both depend on the 5.4.2
  package and would install it alongside. `/etc/containers/policy.json` and
  `registries.conf` — which that package used to provide, and without which podman
  refuses to run anything — are written by the Containerfile instead.

The whole toolchain is `~700 MB` on a `1.63 GB` image, of which the nesting-specific
part is `~150 MB`. It goes in the **shared base image**, gated at runtime by config,
rather than into a separate `-nested` image variant — one image, no build matrix, and
`_compute_image_hash()` already covers `Containerfile` so project images rebuild
automatically.

### 3. Storage: a per-project volume, not tmpfs

Nested image storage **cannot** live on the agent's default paths:

- `/home/pi` is tmpfs — a multi-GB image pull would land in RAM, and this VM has
  **2 GiB** total (while `config.yaml` nominally grants the agent `16g`).
- The container's own rootfs is overlayfs, and overlay-on-overlay is unsupported —
  podman would degrade to the `vfs` driver, which copies every layer in full.

So a named volume `pi-nested-<project-hash>` is mounted at
`/home/pi/.local/share/containers`. Verified: the volume mounts correctly *nested under*
the existing `--mount type=tmpfs,…,destination=/home/pi/`, and the nested podman
resolves to the native **`overlay`** driver on it — not `vfs`.

Persisting it across runs is a deliberate exception to the ephemeral-home rule: without
it every session re-pulls every base image through mitmproxy. The volume carries the same
labels as project images (`pi-container.type=nested-storage`,
`pi-container.project.hash`, `pi-container.project.path`) so the orphan-cleanup pass in
[`run.py`](../../src/run.py) can reclaim it with the same
path-no-longer-exists rule already used for images. A `storage: tmpfs` setting is offered
for workspaces that want no persistence and have the RAM.

### 4. Egress interception is preserved — verified

This is the load-bearing security claim, so it was tested rather than reasoned about.
With the outer container attached only to an `--internal` network (no default route),
a nested container cannot reach the network *at all* — not even a raw IP:

```
$ podman run --rm --network <internal-net> --device /dev/fuse --device /dev/net/tun \
    --security-opt label=disable --user podman -v <vol>:/home/podman/.local/share/containers \
    quay.io/podman/stable sh -c 'podman run --rm alpine:3.20 sh -c "
        wget -qO- -T 6 https://example.com || echo INNER-HTTPS-FAIL;
        wget -qO- -T 6 http://1.1.1.1     || echo INNER-RAW-IP-FAIL"'
  INNER-HTTPS-FAIL (no bypass)
wget: can't connect to remote host (1.1.1.1): Network unreachable
  INNER-RAW-IP-FAIL (no bypass)
```

`Network unreachable` comes from the kernel's routing layer in the *agent's* netns.
Rootless podman connects its containers through `pasta`, which performs
userspace NAT **into the parent namespace's stack** — so nested traffic is emitted from
the agent's own interface, with the agent's source address, subject to the agent's routes.
The proxy's existing `-i eth1` REDIRECT rules therefore match nested traffic with **no
proxy-side changes at all**.

For reference, on a normal (routed) network the same nested container gets a working
stack — its own bridge address, `aardvark-dns` at `169.254.1.1`, and successful HTTPS.
Inside pi-container that inner resolver forwards to the agent's `resolv.conf`, which
points at the proxy: DNS interception survives, one hop deeper.

Two honest consequences:

- **`/dev/net/tun` + `NET_ADMIN` does not create egress.** The agent can build tunnels in
  its own netns, but there is still exactly one way off the isolated network — the proxy —
  and the proxy's `FORWARD` policy is `DROP`.
- **Flow attribution coarsens.** `flow_export` partitions captured flows by client IP;
  nested-container flows carry the agent's IP, so they are indistinguishable from the
  agent's own. Documented, not fixed — distinguishing them would require per-inner-container
  addressing on the isolated net, which the sibling-sidecar design would need anyway.

### 5. SELinux: the one real concession

The host runs SELinux in **Enforcing** mode, and `/dev/net/tun` is inaccessible under the
default container label. The full matrix, measured:

| Agent label | Inner container flags | Result |
|---|---|---|
| default (`container_t`) | — | `/dev/net/tun`: **Permission denied** |
| `label=type:container_engine_t` | — | inner container fails: `crun: mount tmpfs to proc/acpi: Permission denied` |
| `label=type:container_engine_t` | `--security-opt label=disable` | same failure |
| `label=type:container_engine_t` | `--security-opt unmask=all` | **works** |
| `label=disable` | — | **works** |

`container_engine_t` is the SELinux type designed for running a container engine inside a
container, and it is the better posture — the agent stays confined. But it only works if
**every** nested container passes `--security-opt unmask=all`, and that cannot be made
transparent: `unmask` is not a `containers.conf` key (a drop-in setting it is silently
ignored — confirmed, while `env` in the same drop-in took effect). Requiring an explicit
flag on every inner run breaks exactly the workflows this feature exists for
(`docker compose up`, testcontainers, anything driving the API socket).

**Decision:** default to `label=disable`, and expose `security: engine_t` in config for
workspaces that accept passing `--security-opt unmask=all` to their inner containers.

What `label=disable` actually costs: the agent container loses SELinux *type* confinement.
It does **not** lose the user namespace (container uid 0 is still the unprivileged VM user
`core`), rootlessness, seccomp, the routing dead end, or the absence of any host socket.
SELinux here was defense-in-depth against a runtime/kernel escape; the primary boundaries
are all intact. Together with the two corrections above (`unmask=ALL` widens the agent's
view of `/proc`/`/sys`; `SYS_ADMIN` widens its namespaced capability set) this is a
genuine reduction and belongs in the docs as one — which is why nesting is **opt-in and
off by default**.

### 6. Trusting the mitmproxy CA inside nested containers

Two separate problems.

**The nested engine itself** talking to registries: podman reads the agent container's
system trust store, where `build.sh` already installs the mitmproxy CA. Works unmodified.

**Processes inside nested containers**: an arbitrary image (`alpine`, `postgres`) has no
idea about the mitmproxy CA, so every TLS call from inside it fails. Fixed with a
`containers.conf` drop-in that injects the CA into *every* nested container. Both
mechanisms verified working:

```toml
# /etc/containers/containers.conf.d/50-pi-container.conf
# Baked into the image at /etc (NOT under /home/pi — that path is tmpfs at runtime and
# would mask anything the image shipped there).
[containers]
mounts = [
  "type=bind,source=/etc/ssl/certs/ca-certificates.crt,destination=/etc/pi-container-ca.crt,ro=true",
]
env = [
  "SSL_CERT_FILE=/etc/pi-container-ca.crt",
  "NODE_EXTRA_CA_CERTS=/etc/pi-container-ca.crt",
  "REQUESTS_CA_BUNDLE=/etc/pi-container-ca.crt",
  "CURL_CA_BUNDLE=/etc/pi-container-ca.crt",
  "GIT_SSL_CAINFO=/etc/pi-container-ca.crt",
]

[engine]
# No cgroup delegation inside the agent container; systemd/dbus are absent.
cgroup_manager = "cgroupfs"
```

Note `mounts`, not `volumes` — `[containers] volumes` is silently ignored (wrong key;
verified). The mounted file is the agent's **full** bundle (system CAs *plus* the
mitmproxy CA), so pointing `SSL_CERT_FILE` at it is strictly additive.

Limitation worth documenting: this covers tools that honour `SSL_CERT_FILE` or those env
vars (OpenSSL, Go, Node, Python-requests, curl, git). A tool that only reads a hardcoded
`/etc/ssl/certs/…` path in its own image still fails, and the fix is per-image (`COPY` the
CA in a Dockerfile stage). Unavoidable without rewriting arbitrary images.

### 7. Registry allowlist

The proxy's allowlist is `default_action: block`, so nesting is useless until registry
hosts are permitted. Ship a **commented-out** block in the seeded
`allowlist.yaml` (commented so enabling nesting is a deliberate two-step, matching how
`egress.allow` ships all-false):

```yaml
    # Uncomment when nested_containers.enabled = true. Registry pulls are HTTPS and
    # are inspected by mitmproxy like any other traffic.
    # - name: "container-registries-allow"
    #   mode: "allow"
    #   hostnames:
    #     - "registry-1.docker.io"
    #     - "auth.docker.io"
    #     # Docker Hub 307-redirects layer blobs to a CDN on docker.com, which
    #     # "*.docker.io" does not cover. Without these, auth and manifest
    #     # resolve and the pull then dies on the first layer with a 403.
    #     - "production.cloudfront.docker.com"
    #     - "*.cloudfront.docker.com"
    #     - "production.cloudflare.docker.com"
    #     - "*.docker.io"
    #     - "ghcr.io"
    #     - "*.ghcr.io"
    #     - "pkg-containers.githubusercontent.com"
    #     - "quay.io"
    #     - "*.quay.io"
    #     - "gcr.io"
    #     - "*.gcr.io"
    #   ip_ranges: []
```

### 8. Resource limits

Nested containers are processes in the agent's cgroup, so `resources.agent.memory/cpus`
already bounds them in aggregate — a nested workload cannot exceed the agent's budget.

Per-inner-container limits (`podman run --memory=…` *inside* the agent) will **not** work:
rootless podman in a container has no delegated cgroup subtree. `cgroup_manager = "cgroupfs"`
above avoids a hard failure on the missing systemd/dbus; inner limit flags are then
best-effort. Documented as a known limitation.

One operational note surfaced by the baseline: the VM has 2 GiB of RAM while the default
`resources.agent.memory` is `16g`. That mismatch is pre-existing, but nested builds are the
workload most likely to expose it.

Since this design landed, **4 GB is the documented minimum** `podman machine` size — not for
nesting but for building the images at all, because the CPython compile moved into the
shared base image — and now into the toolchain image (`pi-coding-agent-builder/`), which also
compiles podman and netavark. `build.sh` checks available memory
in the VM before starting a build and refuses with the fix rather than OOM-killing the
compiler minutes in. Enabling nesting warrants more again.

### 9. Entrypoint changes

`pi-coding-agent/entrypoint.sh` runs as root before `gosu pi`, which is the right place for
the one setup step that needs it:

```bash
# ─── Nested container support (PI_CONTAINER_NESTED, injected by run.py) ───
# Rootless podman needs a private XDG_RUNTIME_DIR for its locks and pid files.
# Created here (as root, pre-gosu) because the agent runs as pi.
if [ "${PI_CONTAINER_NESTED}" = "true" ]; then
    mkdir -p /run/user/1000
    chown pi:pi /run/user/1000
    chmod 700 /run/user/1000
    # The volume mounted at ~/.local/share/containers may be freshly created and
    # root-owned; pi must own its own image store.
    chown pi:pi /home/pi/.local/share/containers 2>/dev/null || true
fi
```

### 10. Configuration surface

New top-level section in `.pi-container/config.yaml`:

```yaml
# Nested containers. When enabled, the agent can run its own containers (podman /
# docker compose / testcontainers) inside the agent container, as rootless podman.
#
# Traffic from nested containers still egresses through the proxy and is inspected
# by mitmproxy — nesting does NOT create a bypass. But enabling this DOES relax the
# agent container's SELinux confinement (see docs/design/nested-containers.md) and
# grants /dev/fuse + /dev/net/tun. Off by default.
#
# Registry hosts must also be allowed in allowlist.yaml, or image pulls will 403.
nested_containers:
  enabled: false
  # Where nested image layers live:
  #   volume → persistent per-project named volume (default; survives runs)
  #   tmpfs  → volatile RAM disk (needs a large podman machine; re-pulls each run)
  storage: volume
  # SELinux posture for the agent container:
  #   disable  → label=disable (default; nested containers need no special flags)
  #   engine_t → label=type:container_engine_t (agent stays confined, but every
  #              nested container must be run with --security-opt unmask=all)
  security: disable
```

`schema_common.py::SCHEMA` gains the matching entry and `schema_version` is bumped (the
template changed, so existing workspaces must re-seed — the existing failure message
already explains how).

### 11. Exposing nested ports to the host

Added after the sections above shipped. Nesting exists so the agent can run real
workloads — a dev server, a compose stack, a database admin UI — and a good share of
those are only useful if a human can *look* at them. As designed, they could not be:
the agent container publishes no ports, so a nested container's `-p 3000:3000` binds
in the agent's netns and dead-ends there. The edge-case table said as much
("never from the host") and treated it as a property rather than a gap.

Two measurements, agent on the `--internal` network:

```bash
# 1. Publishing works on an --internal network at all.
#    (It has no gateway, but `-p` forwards within the runtime's own namespace
#    and never needed one.)
podman run -d --network <internal-net> -p 127.0.0.1:38999:8000 alpine:3 <listener>
curl http://127.0.0.1:38999/            → INTERNAL-OK                        ✅

# 2. But the same outer -p in front of a NESTED container times out.
#    Isolated within one agent container publishing two ports:
#      :3001 plain listener in the agent netns  → host gets 200              ✅
#      :3000 nested container (pasta)           → host times out             ❌
#    …while a sibling on the isolated net reaches that same :3000 fine, so the
#    listener works. It is specifically the published-port path.
```

The cause, read off the agent's own socket table during a host `curl`:

```
ESTAB  78  0  10.89.3.8:3000   10.89.3.8:34282     ← handshake completed, 78 bytes stuck
pasta … --config-net -t 3000-3000:8000-8000 … --map-guest-addr 169.254.1.2
```

The connection arrives with **the agent's own address as its source** — which is
also the address `pasta` hands the nested guest. Source and guest address collide,
and the flow never resolves: pasta accepts, then stalls. On a *routed* network the
same setup works, because the source is the bridge gateway instead; that is why this
did not surface in the original verification, which used a routed network for
everything except the no-bypass proof.

The address collision is not a bug being worked around: assigning the guest the
host's own address is how pasta deliberately avoids NAT, and its documented
corollary is that the host is then not contactable at that address. The upstream
reports of that corollary are all the *outbound* direction (a container reaching a
host service, podman
[#22771](https://github.com/containers/podman/issues/22771) /
[#23782](https://github.com/containers/podman/issues/23782)); the inbound case —
a host reaching a **nested** container's published port — appears to be undocumented,
so the measurements above are the evidence for it. `pasta -a <addr>`, the documented
knob for giving the guest a distinct address, is what podman exposes as
`--network pasta:-a,<addr>`; it was tried first and the inner container failed to
start with it.

**Fix: `[containers] netns = "bridge"` in the image's `containers.conf` drop-in**,
overriding podman 6's rootless default of `pasta`. Then `rootlessport` binds a real
socket in the agent's netns and the outer `-p` publishes it. Measured, same agent,
same `--internal` network, no `--network` flag on the inner run:

```
host → 127.0.0.1:39020 → agent :3020 → nested container      → OK-web         ✅
nested container egress: NESTED-HTTPS-FAIL / NESTED-RAWIP-FAIL (no bypass)    ✅
nested container CA:     NESTED-CA-PRESENT, SSL_CERT_FILE set                 ✅
```

Bridge is also what `docker compose` already got — it creates a user-defined network
per project, which is why the compose path was never affected — so this makes a plain
`podman run -p` behave like the compose path instead of differently from it. The
rejected alternatives were per-nested-container pasta options (`-a` to give the guest
a distinct address: the container failed to start), and a userspace forwarder in the
agent netns in front of pasta (works, but needs a process per port and exists only to
work around a default we can simply change).

**Config surface**, under the existing section:

```yaml
nested_containers:
  ports:
    expose: localhost   # localhost → 127.0.0.1 only; lan → 0.0.0.0
    publish: []         # [3000, 5173, "18080:8080"]
```

Ports are declared rather than discovered because a container's published ports are
fixed at start — `run.py` cannot add one to a running agent. `_unavailable_host_ports()`
probes each host port (at the address it will actually bind, so `expose: lan` is
checked against `0.0.0.0`) and aborts the launch naming the conflict, instead of
letting podman reject it after the images are built and the proxy is up.

**Security.** This is inbound only and adds no egress — re-verified above. It does add
an inbound surface: something outside the agent can now reach a service the agent
controls, and an established TCP connection is bidirectional. `expose: localhost` is
the default and keeps that to host-local processes; `lan` binds `0.0.0.0` and is
documented as the sharper edge it is (mitmweb is at least password-gated, an arbitrary
dev server is not). `publish` ships empty, matching how `egress.allow` ships all-false.

---

### 12. The reverse hop: a nested container reaching a service on the agent

Section 11 is inbound — host reaching a nested container. The mirror case is a nested
container reaching a service in the *agent's* netns: a metrics exporter, a mock API, a
language server, anything a project runs beside its stack rather than inside it. The
conventional address for that is `host.docker.internal`, and inside the agent it is
wrong.

```
# /etc/hosts inside a nested container
192.168.127.254   host.containers.internal host.docker.internal
```

`192.168.127.254` is gvproxy — the **macOS host** as seen from the podman machine VM.
Two independent things are wrong with it: the agent-side service is not on the host, and
the agent's `--internal` network has no route to the host anyway. The second failure
masks the first: the connection times out instead of being refused, which presents as a
slow or hung service rather than a misdirected one. Prometheus reports it as
`context deadline exceeded` and marks the target down; the dashboard renders fine and
shows nothing, with no error anywhere in the UI.

The value is not computed for the nested netns — it is byte-identical to the agent's own
`/etc/hosts` entry. The agent carries `/run/.containerenv`, so the nested podman detects
that it is itself running inside a container and propagates the parent's entry.

**Measured**, listener on `0.0.0.0:9100` in the agent's netns, probed from a nested
container on a compose network:

```
192.168.127.254:9100   (host.docker.internal)   download timed out          ❌
10.89.1.1:9100         (nested bridge gateway)  connection refused          ❌
10.89.0.2:9100         (agent's bridge address) connection refused          ❌
169.254.1.2:9100                                # HELP … (serves)           ✅
```

`169.254.1.2` is podman's own, from the rootless-netns setup inside the agent:

```
/usr/bin/pasta --config-net … --no-map-gw --map-guest-addr 169.254.1.2
```

`--no-map-gw` is why the bridge gateway refuses, and `--map-guest-addr` is the address
that replaces it. Both are podman's choices for the rootless network namespace, so this
is stable within a podman generation and not an interface it promises.

**`--add-host` does not fix it, and fails convincingly.** The obvious remedy is
`--add-host host.docker.internal:169.254.1.2` (compose: `extra_hosts:`). Podman
accepts it and keeps its own entry too, so the name resolves to two addresses:

```
169.254.1.2      host.docker.internal          ← from --add-host
192.168.127.254  host.containers.internal host.docker.internal
```

busybox `wget` inside the container takes the first match and succeeds — which is
exactly the check an operator runs to confirm the fix, and it passes. Prometheus stayed
`down` anyway, with the same `context deadline exceeded` and a `lastScrapeDuration` of
exactly 1.0006s, the full `scrape_timeout`. Go's resolver sorts multi-address results by
RFC 6724 destination-address selection, which ranks global scope above link-local, so it
dials `192.168.127.254` first — a black hole that neither connects nor refuses — and the
scrape deadline expires before the second address is reached. Deleting the podman line
from `/etc/hosts` by hand flipped the target to `up` on the next scrape; that is the
evidence, and it is what rules out every other explanation for the timeout.

`--no-hosts` would suppress podman's entry but is mutually exclusive with `--add-host`,
and the image's `/etc/hosts` has no such name to begin with. So the name cannot be made
unambiguous, and the fix is to stop resolving it: put `169.254.1.2` literally wherever
the target is configured. For a compose stack that is a replacement config file mounted
at the same container path — compose merges volumes keyed on target, so the committed
mount is superseded and named volumes survive.

**No transparent fix exists in the drop-in either.** `host_containers_internal_ip` is the
documented `containers.conf` key for exactly this, and it does not take effect here —
tried under `[containers]` and under `[network]`, both ignored. The test controlled for
the obvious explanation: the same override file also set a marker `env` entry, which
*did* reach the container, so the file was read and only the key was disregarded. That
is consistent with the `/run/.containerenv` propagation above outranking it. Also
rejected: rewriting `/etc/hosts` in the agent image (the agent's own entry is correct
*for the agent* — it really can reach the host through the proxy), and a per-project
`--add-host` injected by pi-container (there is no hook on the nested `podman run`, and
it would be wrong for containers that legitimately mean the host).

**So this stays a documented constraint with a per-project fix**: the literal address in
the project's own config. That does not have to be an untracked file. `169.254.1.2` is
meaningless outside a pi-container agent, but a compose **named overlay** confines it —
an explicit `-f` list replaces auto-discovery, so `docker-compose.pi-container.yml` is
inert for everyone who does not pass it, while `docker-compose.override.yml` (loaded
automatically) would not be. That makes the fix reviewable and shared instead of
rediscovered per operator. Selecting it is what the `PI_CONTAINER` marker is for, so
"forgot the overlay" stops being a failure mode rather than becoming a warning; the
address is `PI_CONTAINER_HOST_IP` wherever substitution is possible. Documented in
[Configuration](../configuration.md#nested-containers) because the failure gives the
operator nothing to search for: no error, no log line, just an empty graph.

---

## Dropping Docker

Nesting does not *technically* require removing Docker — the nesting happens inside the
agent container regardless of which runtime started it. Removing it is still the right
call, for three reasons.

**1. Docker has no user namespace by default.** Every verified result above rests on the
agent container's `uid_map` (`0→501`) — container root is an unprivileged host user. Under
stock Docker there is no remap: container uid 0 *is* host uid 0. The flag set this design
needs (`/dev/fuse`, `/dev/net/tun`, relaxed SELinux, a nested container engine) is
well-contained under a userns and is a credible host-escape surface without one. Shipping
the same feature on both runtimes would mean shipping two materially different security
guarantees under one config flag.

**2. `DockerRuntime` is already unverified.** Its own docstring in
[runtimes.py](../../src/runtimes.py) says "Untested here (no docker binary on this host)",
and it relies on `--network` ordering to land the isolated network on `eth1` because Docker
has no per-attachment interface naming. On this machine `docker` is a shell alias to
`podman`. Adding a security-sensitive feature to a code path nobody exercises is how the
guarantee quietly becomes false.

**3. Rootless nesting is a first-class podman feature.** `--userns`, `newuidmap` in the
container, `pasta`, `containers.conf` drop-ins, and rootless nested overlay are podman
mechanisms with an upstream-maintained reference image. On Docker the equivalent is
`--privileged` DinD, already rejected.

What removal deletes:

- `DockerRuntime` and its registry entry in `ContainerRuntime.create()`.
- `proxy_secondary_connect_argv()` — the abstract hook exists *only* because
  `docker run` attaches one network, so the whole post-run `network connect` step in
  `network.py::_actually_start()` goes with it.
- Docker branches in `validate_environment()`, plus `docker`-specific docs and env vars.

`ContainerRuntime` should stay as an abstraction (`PodmanRuntime` remains the single
subclass) rather than being flattened into `run.py` — it is the seam where a future
runtime would attach, and collapsing it would be a large diff for no gain.

---

## Edge cases

| Scenario | Behavior |
|---|---|
| `nested_containers.enabled: false` (default) | No new flags, no volume, no entrypoint work. Only cost is the toolchain's ~150 MB in the image. |
| Nesting enabled, registries not allowlisted | Pulls fail with mitmproxy's 403. `_warn_about_registry_allowlist` in `run.py` catches this at startup. |
| Registry allowlisted, its blob CDN not | The subtler half of the same failure: token and manifest succeed, then the 307 to the CDN is blocked and the pull dies part-way through the first layer. Same preflight warns, naming the missing hostname. |
| Nested container tries to reach the internet directly | Impossible — verified `Network unreachable`. Proxy is the only route. |
| Nested container publishes a port | Binds in the agent's netns. Reachable from the isolated network; reachable from the **host** only for ports listed in `nested_containers.ports.publish`, which adds the outer `-p` hop — see [Exposing nested ports to the host](#11-exposing-nested-ports-to-the-host). |
| Nested container bind-mounts `/workspace/...` | Works — nested containers are children in the agent's mount namespace. Cannot reach beyond what the agent already sees. |
| TLS from inside a nested container | Works for tools honouring `SSL_CERT_FILE`/`NODE_EXTRA_CA_CERTS`/etc. Images with a hardcoded bundle path need a per-image `COPY`. |
| `storage: volume`, project directory deleted | Volume is labelled with `pi-container.project.path`; the orphan-cleanup pass reclaims it exactly as it does project images. |
| `storage: tmpfs` on the 2 GiB default machine | First sizeable pull hits OOM. Docs must state the machine-sizing requirement. |
| Concurrent runs in the same workspace | Both mount the same nested-storage volume. Podman's own storage locks (in the per-run `XDG_RUNTIME_DIR`) do **not** span containers — see [Open questions](#open-questions). |
| Inner `podman run --memory=…` | Best-effort; no cgroup delegation. Aggregate limit from `resources.agent` still applies. |
| Host is a Linux machine with `user:100000:65536` | The `pi:1:999` + `pi:1001:64535` split stays inside the mapped range by construction. A wider range would not. |

---

## Implementation phases

1. **Podman-only.** Remove `DockerRuntime`, `proxy_secondary_connect_argv()`, the
   post-run `network connect` step, and the docker branches in `validate_environment()`.
   No behavior change on podman. Independently reviewable and shippable.
2. **Image toolchain.** Containerfile packages, `/etc/subuid` + `/etc/subgid`, setuid on
   `newuidmap`/`newgidmap`, the `containers.conf` drop-in. Verify a nested
   `podman run` end-to-end from a shell in the agent container.
3. **Orchestration.** `nested_containers` config + schema + `schema_version` bump,
   `PodmanRuntime.nested_container_args()`, volume create/label/orphan-cleanup,
   entrypoint block.
4. **Docs + allowlist.** Commented registry block, `configuration.md`, an
   `architecture.md` section, and an explicit security note that enabling nesting relaxes
   SELinux confinement.

Each phase leaves the tree working; the security-relevant surface all lands in 3.

---

## Files changed

| File | Change |
|---|---|
| `src/runtimes.py` | Delete `DockerRuntime` + registry entry + `proxy_secondary_connect_argv()`. Add `PodmanRuntime.nested_container_args(cfg, project_hash) -> list[str]` returning the device/security/volume/env flags. Update the module docstring (it currently documents two runtimes). |
| `src/network.py` | Delete the `proxy_secondary_connect_argv()` call in `_actually_start()`. Add `read_nested_containers_config()`. |
| `src/run.py` | Call `read_nested_containers_config()`; splice `nested_container_args()` into `pi_container_cmd`. Add `_ensure_nested_volume()` (create + label) and `_cleanup_orphaned_nested_volumes()` mirroring `_cleanup_orphaned_project_images()`. |
| `src/util.py` | `validate_environment()`: podman-only. |
| `src/schema_common.py` | `SCHEMA` gains `nested_containers` (`enabled`, `storage`, `security`). |
| `pi-coding-agent/Containerfile` | Nesting runtime packages; `COPY --from` the toolchain image; `/etc/subuid`/`/etc/subgid`; `setcap` on `newuidmap`/`newgidmap`; `docker` shim; `/etc/containers/{policy.json,registries.conf,containers.conf.d/50-pi-container.conf}`. |
| `pi-coding-agent-builder/` | **New.** Builds podman 6 (explicit `BUILDTAGS`), netavark/aardvark-dns 2.0 and CPython/uv/podman-compose from source, and stages Node 26 (official tarball by default, `NODE_SOURCE=build` to compile) — one independent stage each, under `/out`. Every version, commit and sha256 is an `ARG` in its `Containerfile`; the scripts assert them via `require_env` and default nothing. |
| `pi-coding-agent/entrypoint.sh` | `PI_CONTAINER_NESTED` block creating/chowning `/run/user/1000` and the store. |
| `pi-coding-agent/default/config.yaml` | New `nested_containers` section; bump `schema_version`. |
| `pi-coding-agent/default/allowlist.yaml` | Commented registry allow-rule block. |
| `docs/configuration.md`, `docs/architecture.md`, `docs/getting-started.md` | Document the section, the trust model change, machine sizing; drop Docker references. |
| `src/tests/test_runtimes.py` | Remove `DockerRuntime` tests; add `nested_container_args()` tests (flags present when enabled, absent when disabled, `engine_t` vs `disable`). |
| `src/tests/test_network.py`, `src/tests/test_run.py` | `read_nested_containers_config()` defaults/parsing; nested-volume lifecycle + orphan cleanup. |
| `src/tests/test_config_schema.py` | Schema entry + `schema_version` bump. |
| `CHANGELOG.md` | Added (nesting), Changed/Removed (Docker support — a breaking change; warrants a minor version bump). |

---

## Open questions

1. ~~**Startup warning for un-allowlisted registries.**~~ Resolved — `run.py`'s
   `_warn_about_registry_allowlist` scans `allowlist.yaml` when `nested_containers.enabled`
   is true and warns both when no registry is present and when a registry is allowed
   without the CDN host its layer blobs redirect to. The blob-host map is verified
   against the live registries and needs re-checking when one moves its CDN, as Docker
   Hub did (Cloudflare → CloudFront).
2. **Concurrent runs sharing one nested-storage volume.** Two agents in the same
   workspace both mount `pi-nested-<hash>`, and each has its own `XDG_RUNTIME_DIR`, so
   podman's storage locks don't see each other — a plausible route to a corrupted store.
   Options: per-run volumes (loses cache sharing), a run-scoped subdirectory inside the
   shared volume (keeps the layer cache separate but the disk shared), or refcount the
   volume like the proxy already is. Needs a decision before phase 3; the subdirectory
   option looks cheapest.
3. **Should `storage: tmpfs` exist at all?** It only works on a large `podman machine`
   and re-pulls every session. It may be more honest to ship `volume` only and let users
   who want ephemerality delete the volume.
4. **Nested-container flow attribution.** Accepting the agent's IP for all nested flows
   is the phase-1 answer. If per-container attribution is later wanted, it needs inner
   containers addressable on the isolated network — a much larger change, and one that
   would reopen the sibling-sidecar tradeoff.
5. **Does `pi` need a `docker` alias?** Many tools shell out to `docker` specifically.
   *Answered: yes.* A one-line `exec podman "$@"` shim at `/usr/local/bin/docker`, plus
   `podman-compose` pinned as the compose provider, makes `docker compose` work
   unmodified with no `DOCKER_HOST` and no socket. Debian's `podman-docker` would have
   provided the shim but depends on its own podman 5.4.2, so the shim is written in the
   Containerfile instead.

---

## Appendix A: verification log

Every command below was run on this host (podman 6.0.2, `applehv`, macOS/arm64). The
reference image `quay.io/podman/stable` was used to test the nested engine before the
agent image carries the toolchain; it was removed afterwards.

```bash
# ── Baseline ────────────────────────────────────────────────────────────────
podman info --format '{{.Host.Security.Rootless}}'                 # true
podman machine ssh 'cat /etc/subuid; ls -l /dev/fuse /dev/net/tun; getenforce'
#   core:100000:1000000 | both crw-rw-rw- | Enforcing
podman machine ssh 'stat -fc %T /sys/fs/cgroup'                    # cgroup2fs

# ── Nesting primitives, in the real agent image ─────────────────────────────
podman run --rm --entrypoint cat pi-coding-agent:local /proc/self/uid_map
#   0 501 1  /  1 100000 1000000     → container UIDs 1..1000000 are mapped
podman run --rm --user pi --entrypoint unshare pi-coding-agent:local -U -r id
#   uid=0(root) — nested userns works as uid 1000, no extra flags
podman run --rm --entrypoint sh pi-coding-agent:local \
  -c 'command -v newuidmap fuse-overlayfs slirp4netns pasta podman || echo NONE-OF-THESE'
#   NONE-OF-THESE → toolchain must be added

# ── SELinux matrix (device access) ──────────────────────────────────────────
# default label      → /dev/net/tun: Permission denied
# label=type:container_engine_t → BOTH-OPEN-OK
# label=disable                 → BOTH-OPEN-OK

# ── Nested engine ───────────────────────────────────────────────────────────
podman run --rm --device /dev/fuse --security-opt label=disable --user podman \
  quay.io/podman/stable podman info --format '{{.Store.GraphDriverName}}'
#   overlay  (native rootless overlay, not vfs)

# inner container, routed network: bridge IP 10.88.0.35, aardvark DNS
# 169.254.1.1, HTTPS-OK

# ── No-bypass proof (outer on --internal, no default route) ─────────────────
#   INNER-HTTPS-FAIL / "Network unreachable" to raw 1.1.1.1

# ── SELinux matrix (full nested run) ───────────────────────────────────────
# engine_t, plain inner              → crun: mount tmpfs to proc/acpi: Permission denied
# engine_t + inner label=disable     → same failure
# engine_t + inner unmask=all        → INNER-STARTED-OK
# label=disable, plain inner         → INNER-STARTED-OK

# ── containers.conf drop-in (/etc/containers/containers.conf.d/) ────────────
# [containers] env=[...]     → propagates to every inner container   ✅
# [containers] mounts=[...]  → auto-mounts into every inner container ✅
# [containers] volumes=[...] → silently ignored (wrong key)           ❌
# [containers] unmask=[...]  → silently ignored (not a config key)    ❌

# ── Storage layout ─────────────────────────────────────────────────────────
# named volume at /home/pi/.local/share/containers, mounted UNDER
# --mount type=tmpfs,notmpcopyup,destination=/home/pi/ → content visible, WRITE-OK
```

## Appendix B: implementation-time verification (agent image, not the reference image)

Re-run against the real `pi-coding-agent` image once the toolchain landed. Everything
below was measured, and three of the four findings contradict the design above.

```bash
# ── Image toolchain ────────────────────────────────────────────────────────
# apt: containers-common does NOT exist on Debian → golang-github-containers-common
# newuidmap/newgidmap present, /etc/subuid = pi:1:999 + pi:1001:64535
# image size 965 MB → 1.08 GB (+117 MB)

# ── newuidmap privilege mechanism ──────────────────────────────────────────
# chmod u+s        → euid 0, CapEff 00000000800405fb, and STILL:
#                    "newuidmap: write to uid_map failed: Operation not permitted"
#                    (both a 1-extent and podman's 3-extent map)
# setcap cap_setuid+ep (setuid bit removed) → map written, podman info reports
#                    rootless=true, driver=overlay        ← this is what shipped

# ── Nested store driver ────────────────────────────────────────────────────
podman info --format '{{.Store.GraphDriverName}} {{.Store.GraphRoot}}'
#   overlay /home/pi/.local/share/containers/storage   (native, not vfs) ✅

# ── Outer flag set required to START an inner container ────────────────────
# label=disable                          → crun: sethostname: Operation not permitted
# label=disable + SYS_ADMIN              → crun: ping_group_range: Read-only file system
# label=disable + unmask=ALL             → crun: sethostname: Operation not permitted
# label=disable + unmask=ALL + SYS_ADMIN → INNER-STARTED-OK ✅
#   inner container also confirmed: SSL_CERT_FILE set + /etc/pi-container-ca.crt
#   readable (the containers.conf drop-in works), HTTPS OK on a routed network.

# ── Compose, end to end in the agent image ─────────────────────────────────
# `docker compose up` (podman-docker shim → podman → podman-compose):
#   [hello] | COMPOSE-SERVICE-RAN in 73add0bfe5a8
#   [hello] | COMPOSE-CA-PRESENT                     ← containers.conf drop-in ✅
# First attempt failed with "netavark: nftables error: unable to execute nft"
# → nftables added to the image (compose creates a user-defined network).

# ── No-bypass proof, re-run with the FINAL flag set ────────────────────────
# outer on `network create --internal --disable-dns`, no default route:
#   outer egress: unreachable
#   INNER-HTTPS-FAIL (no bypass)
#   wget: can't connect to remote host (1.1.1.1): Network unreachable
#   INNER-RAW-IP-FAIL (no bypass)                                          ✅
# Same again through a compose-created bridge network:
#   COMPOSE-HTTPS-FAIL (no bypass) / COMPOSE-RAW-IP-FAIL (no bypass)         ✅

# ── Note on the SELinux matrix above ───────────────────────────────────────
# `--security-opt unmask=...` is accepted by the podman *remote* client only as
# unmask=ALL or an explicit path list (lowercase `all` is not special).
```

### Appendix B2: re-verification after the toolchain moved to a builder image

The apt-installed podman 5.4.2 was replaced by podman 6.0.2 built from source (with
netavark/aardvark-dns 2.0.0 and CPython/uv/podman-compose), copied in from
`pi-coding-agent-builder:local`. Everything above was re-run against it, unchanged
flags:

```bash
# ── Build tags actually present in the binary ──────────────────────────────
go version -m bin/podman | grep -- -tags
#   build -tags=seccomp,libsqlite3,containers_image_openpgp,
#               exclude_graphdriver_btrfs,grpcnotrace                        ✅
ldd bin/podman
#   libsqlite3.so.0, libseccomp.so.2, libc.so.6, libm.so.6 — no gpgme        ✅

# ── The nested runtime, same outer flags as before ─────────────────────────
podman info --format '…'
#   rootless=true driver=overlay netbackend=netavark runtime=crun
#   logdriver=k8s-file cgroups=cgroupfs                                      ✅
#   (k8s-file confirms the omitted `systemd` build tag: no journald default
#    pointing at a socket that does not exist in this container.)

# ── Plain run, docker shim, compose ────────────────────────────────────────
podman run --rm alpine:3 …   → NESTED-RUN-OK / NESTED-CA-PRESENT             ✅
docker --version             → podman version 6.0.2                          ✅
docker compose up            → [probe] COMPOSE-SERVICE-RAN / COMPOSE-CA-PRESENT ✅

# ── No-bypass proof, re-run on `network create --internal --disable-dns` ───
#   AGENT-HTTPS-FAIL (expected)
#   NESTED-HTTPS-FAIL (no bypass)      / NESTED-RAW-IP-FAIL (no bypass)      ✅
#   COMPOSE-HTTPS-FAIL (no bypass)     / COMPOSE-RAW-IP-FAIL (no bypass)     ✅
```

Two incidental findings:

- **A git tag is not a commit.** `git ls-remote <url> refs/tags/v6.0.2` returns the
  *annotated tag object's* sha; the pin has to be the peeled commit
  (`refs/tags/v6.0.2^{}`). The commit assertion in `clone_verified()` caught this on
  the first build instead of silently accepting whatever the tag pointed at.
- **The PGO profile run can fail the build.** `--enable-optimizations` makes `make`
  run part of CPython's test suite; one test failed under memory pressure
  (`make: *** [Makefile:1020: profile-run-stamp] Error 2`) and passed on a plain
  retry. `build-python.sh` now says so when that specific step is what failed.
- **Node's `configure` drops features with a warning, not an error.** Node 26 implements
  the `Temporal` API in Rust; with no toolchain present it prints `WARNING: cargo not
  found! Support for Temporal will be disabled.` and completes successfully, yielding a
  Node with no `Temporal` global — which `node:26.3.1-trixie-slim` does have (measured).
  Building Node here therefore needs the same pinned Rust as netavark, and the build
  asserts `Temporal` both in the configure log and in the staged binary.

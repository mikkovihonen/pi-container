#!/bin/bash
# Shared helpers for the toolchain build scripts in this directory.
#
# Sourced, never executed: the caller owns `set -euo pipefail`.

# Everything that will be copied into the agent image is staged here, mirroring
# its final layout — $PI_STAGE/usr/local/bin/podman becomes /usr/local/bin/podman.
# Staging (rather than installing into the builder's own /usr/local) is what keeps
# the toolchains out of the shipped image: Go, Rust, the source trees and their
# build caches add ~3 GB that never leaves this image.
PI_STAGE="${PI_STAGE:-/out}"

# ─── Architecture ────────────────────────────────────────────────────────────
# Every download below is per-architecture. Getting it wrong is not caught until
# the very end of a long build, and then only as "Exec format error", so the
# mapping lives in one place.

deb_arch() {
    dpkg --print-architecture
}

# The GNU triple's architecture component (what Rust and ./configure call it).
gnu_arch() {
    case "$(deb_arch)" in
        amd64) echo x86_64 ;;
        arm64) echo aarch64 ;;
        *)
            echo "ERROR: unsupported architecture '$(deb_arch)'" >&2
            return 1
            ;;
    esac
}

# ─── Build arguments ─────────────────────────────────────────────────────────
#
# Every version, git commit and sha256 this directory builds from is declared as an
# ARG in Containerfile and reaches these scripts as an environment variable — podman
# exports build args to RUN, so no `VAR="${VAR}" bash script.sh` plumbing is needed.
#
# Nothing is defaulted in a script. A pin that quietly fell back to a value baked in
# here would defeat the reason for hoisting them: the Containerfile would no longer be
# the single answer to "what is this image built from?", and a stage whose ARG was
# renamed or dropped would keep building — from the stale pin, silently. So assert
# instead, and name the ARG so the fix needs no digging.
require_env() {
    local name missing=0

    for name in "$@"; do
        if [ -z "${!name:-}" ]; then
            echo "ERROR: $(basename "$0") requires ${name}, which is unset or empty." >&2
            missing=1
        fi
    done

    if [ "${missing}" -ne 0 ]; then
        echo "       These are build ARGs. Declare them for this stage in" >&2
        echo "       pi-coding-agent-builder/Containerfile, or pass --build-arg NAME=value." >&2
        return 1
    fi
}

# ─── Fetching sources ────────────────────────────────────────────────────────

# Download a file and verify its SHA-256 before anything reads it.
fetch_verified() {
    local url="$1" sha="$2" dest="$3"

    echo "Downloading ${url##*/}"
    curl -fsSL --retry 3 --retry-delay 2 -o "${dest}" "${url}"
    echo "${sha}  ${dest}" | sha256sum -c - > /dev/null
}

# Shallow-clone a tag and assert which commit it resolves to.
#
# A tag is a mutable pointer; the commit is not. Pinning the commit is what
# actually fixes the source, and it fails loudly if a tag is ever moved.
#
# The pin is the COMMIT, not the tag object. Every tag used here is annotated, so
# `git ls-remote <url> refs/tags/<tag>` prints the tag object's own sha — a
# different value that will never match. Get the commit with the peeled ref:
#   git ls-remote --tags <url> | grep 'refs/tags/<tag>^{}'
clone_verified() {
    local repo="$1" tag="$2" commit="$3" dir="$4"

    echo "Cloning ${repo##*/} ${tag}"
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "${tag}" "${repo}" "${dir}"

    local head
    head="$(git -C "${dir}" rev-parse HEAD)"
    if [ "${head}" != "${commit}" ]; then
        echo "ERROR: ${repo} ${tag} resolves to ${head}, expected ${commit}." >&2
        echo "       Either the tag was moved or the pin is wrong — refusing to build it." >&2
        return 1
    fi
}

# ─── Rust toolchain ──────────────────────────────────────────────────────────
#
# Needed by two unrelated stages, which is why the pin lives here rather than in
# either of them:
#   * build-network.sh — netavark and aardvark-dns are Rust programs.
#   * build-node.sh    — Node 26 implements `Temporal` in Rust. Without a toolchain
#                        node's configure prints "cargo not found! Support for Temporal
#                        will be disabled" as a *warning* and builds a Node without it.
#
# Debian trixie has 1.85, and both netavark and aardvark-dns declare
# rust-version = "1.88", so the toolchain is downloaded. Hashes come from
# https://static.rust-lang.org/dist/channel-rust-stable.toml.
#
# The pin is the one set of ARGs declared *before* the first FROM in Containerfile,
# because it is the only one two stages share. Each of those stages re-declares
# `ARG RUST_VERSION` (and friends) with no default to inherit the global value —
# which is what keeps this a single pin rather than two that can drift apart.

# Install the pinned Rust into /opt/rust and prepend it to PATH.
#
# The caller is responsible for removing /opt/rust when done: a stage keeps whatever it
# leaves behind, and none of it is meant to ship.
install_rust() {
    local arch rust_sha rust_pkg
    require_env RUST_VERSION RUST_DIST_DATE RUST_SHA256_X86_64 RUST_SHA256_AARCH64
    arch="$(gnu_arch)"

    case "${arch}" in
        x86_64) rust_sha="${RUST_SHA256_X86_64}" ;;
        aarch64) rust_sha="${RUST_SHA256_AARCH64}" ;;
        *)
            echo "ERROR: no pinned Rust tarball for '${arch}'" >&2
            return 1
            ;;
    esac

    rust_pkg="rust-${RUST_VERSION}-${arch}-unknown-linux-gnu"
    fetch_verified \
        "https://static.rust-lang.org/dist/${RUST_DIST_DATE}/${rust_pkg}.tar.gz" \
        "${rust_sha}" \
        "/tmp/${rust_pkg}.tar.gz"
    tar -C /usr/src -xzf "/tmp/${rust_pkg}.tar.gz"
    rm -f "/tmp/${rust_pkg}.tar.gz"

    # --without=rust-docs drops most of the tarball's size. --disable-ldconfig because
    # nothing outside this image links against Rust's shared libraries.
    run_logged "Installing Rust ${RUST_VERSION}" /tmp/rust-install.log \
        "/usr/src/${rust_pkg}/install.sh" \
        --prefix=/opt/rust --without=rust-docs --disable-ldconfig
    rm -rf "/usr/src/${rust_pkg}"

    export PATH="/opt/rust/bin:${PATH}"
    rustc --version
    cargo --version
}

# ─── Running build steps ─────────────────────────────────────────────────────

# Run a verbose build step with its output captured to a log file. On failure,
# dump the tail of that log to stderr so the actual error reaches the build
# output (build.py streams every line at INFO) instead of being swallowed.
run_logged() {
    local desc="$1"
    local log="$2"
    shift 2

    echo "${desc}"
    if ! "$@" > "${log}" 2>&1; then
        echo "ERROR: ${desc} failed. Last 60 lines of ${log}:" >&2
        tail -n 60 "${log}" >&2
        return 1
    fi
}

# ─── Provenance ──────────────────────────────────────────────────────────────

# Record one shipped component, so "which podman is this, built how?" is answerable
# inside the agent container without network access.
#
# One fragment file per build script rather than one shared manifest: each script runs
# in its own build stage with its own $PI_STAGE, and four stages appending to the same
# path would mean the last COPY silently wins. The agent image concatenates the
# fragments into /usr/local/share/pi-container/toolchain-versions.txt.
stage_record() {
    local fragment_dir="${PI_STAGE}/usr/local/share/pi-container/toolchain-versions.d"
    local fragment="${fragment_dir}/$(basename "$0" .sh).txt"

    mkdir -p "${fragment_dir}"
    printf '%-14s %s\n' "$1" "$2" >> "${fragment}"
}

# ─── Memory ──────────────────────────────────────────────────────────────────

# MemAvailable, not MemFree: reclaimable page cache counts, and on a warm builder
# the two differ by an order of magnitude.
mem_avail_mib() {
    awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo
}

# How many compile jobs to run at once, capped by MEMORY rather than core count.
#
# The podman machine defaults to 2 GiB and is documented to need 4 GiB, on a host
# with many more cores than that supports: `-j $(nproc)` OOM-kills the compiler,
# and the failure looks like an unrelated internal error ("gcc: fatal error: Killed
# signal terminated program cc1", or rustc's "signal: 9, SIGKILL: kill"). Giving the
# VM more memory automatically raises the cap.
#
# $1 is the peak RSS of one job in MiB. Always returns at least 1.
mem_capped_jobs() {
    local mib_per_job="$1"
    local cpu_jobs mem_jobs

    cpu_jobs="$(nproc)"
    mem_jobs=$(($(mem_avail_mib) / mib_per_job))
    if [ "${mem_jobs}" -lt 1 ]; then
        mem_jobs=1
    fi
    if [ "${mem_jobs}" -lt "${cpu_jobs}" ]; then
        echo "${mem_jobs}"
    else
        echo "${cpu_jobs}"
    fi
}

#!/bin/bash
# Build netavark and aardvark-dns from source and stage them for the agent image.
#
# WHY FROM SOURCE
# Not for build tags — these have none worth choosing. Purely for the version:
# podman 6.0.0's release notes say it "must be used with [...] Netavark and Aardvark
# v2.0.0", and Debian trixie ships 1.14.0. Mixing them is not a supported
# combination, so building podman 6 forces building these too.
#
# Upstream publishes a prebuilt netavark.gz per release, but only for x86_64 — no
# use on an Apple Silicon podman machine, which is aarch64.
#
# WHAT THEY DO HERE
# netavark is the network backend podman calls to set up a user-defined network.
# A plain rootless `podman run` never needs it (it reaches the network through
# pasta), but `docker compose` creates a network per project, and that path goes
# through netavark → nft. aardvark-dns is the resolver that gives compose services
# their service-name DNS.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
# shellcheck source=pi-coding-agent-builder/common.sh
source "$(dirname "$0")/common.sh"

# Pins come from Containerfile as build ARGs; see require_env in common.sh. RUST_* is
# checked by install_rust rather than here, since that is what consumes it.
require_env \
    NETAVARK_VERSION NETAVARK_COMMIT NETAVARK_VENDOR_SHA256 \
    AARDVARK_VERSION AARDVARK_COMMIT AARDVARK_VENDOR_SHA256

# The Rust toolchain is a builder-only dependency; nothing Rust-related is staged. Its
# pin is the one declared before the first FROM in Containerfile, because this stage and
# the node stage share it.
install_rust

# Both crates are built from upstream's own vendor tarball, so cargo must never reach
# crates.io: --offline plus this makes a missing crate an error instead of a silent
# network fetch that would defeat the pinned hashes.
export CARGO_NET_OFFLINE="true"

# ─── Build ───────────────────────────────────────────────────────────────────

# Source and dependencies come from two separately verified places: the git tag
# (pinned to a commit) and upstream's release vendor tarball (pinned to the sha256
# published in the release's own `sha256sum` asset). The tarball holds only the
# vendor/ tree, so the .cargo/config.toml that redirects crates.io at it is written
# here.
build_helper() {
    local name="$1" tag="$2" commit="$3" vendor_sha="$4"
    local src="/usr/src/${name}"

    clone_verified "https://github.com/containers/${name}.git" "${tag}" "${commit}" "${src}"
    fetch_verified \
        "https://github.com/containers/${name}/releases/download/${tag}/${name}-${tag}-vendor.tar.gz" \
        "${vendor_sha}" \
        "/tmp/${name}-vendor.tar.gz"
    tar -C "${src}" -xzf "/tmp/${name}-vendor.tar.gz"
    rm -f "/tmp/${name}-vendor.tar.gz"

    mkdir -p "${src}/.cargo"
    printf '%s\n' \
        '[source.crates-io]' \
        'replace-with = "vendored-sources"' \
        '' \
        '[source.vendored-sources]' \
        'directory = "vendor"' \
        > "${src}/.cargo/config.toml"

    cd "${src}"
    # --bin ${name} skips the extra binaries in these workspaces (netavark's
    # dhcp-proxy and its client, used only for macvlan/ipvlan DHCP, and the
    # connection tester). Podman does not call them in this setup.
    #
    # rustc is the heaviest compiler in this image — a release build of a large
    # dependency graph peaks around 700 MiB per unit — so cargo's default of one
    # job per core has to be capped on a small builder.
    local jobs
    jobs="$(mem_capped_jobs 700)"
    echo "Using ${jobs} compile job(s) (cpus=$(nproc), available memory=$(mem_avail_mib) MiB)"
    run_logged "Building ${name} ${tag}" "/tmp/${name}-build.log" \
        cargo build --release --offline --jobs "${jobs}" --bin "${name}"

    # helper_binaries_dir's default list starts with /usr/local/libexec/podman,
    # which is where podman looks for both of these.
    install -D -m 0755 "target/release/${name}" \
        "${PI_STAGE}/usr/local/libexec/podman/${name}"
    "${PI_STAGE}/usr/local/libexec/podman/${name}" --version

    cd /
    rm -rf "${src}"
}

build_helper netavark "${NETAVARK_VERSION}" "${NETAVARK_COMMIT}" "${NETAVARK_VENDOR_SHA256}"
build_helper aardvark-dns "${AARDVARK_VERSION}" "${AARDVARK_COMMIT}" "${AARDVARK_VENDOR_SHA256}"

stage_record "netavark" "${NETAVARK_VERSION}"
stage_record "aardvark-dns" "${AARDVARK_VERSION}"

# As in build-podman.sh: the toolchain and its build caches are ~2 GB this stage has
# no reason to keep.
rm -rf /opt/rust /root/.cargo /root/.rustup

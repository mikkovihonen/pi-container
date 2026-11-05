#!/bin/bash
# Download, verify, and stage the Rust toolchain.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
# shellcheck source=pi-coding-agent-builder/common.sh
source "$(dirname "$0")/common.sh"

require_env RUST_VERSION RUST_DIST_DATE RUST_SHA256_X86_64 RUST_SHA256_AARCH64

arch="$(gnu_arch)"
case "${arch}" in
    x86_64) rust_sha="${RUST_SHA256_X86_64}" ;;
    aarch64) rust_sha="${RUST_SHA256_AARCH64}" ;;
    *)
        echo "ERROR: no pinned Rust tarball for '${arch}'" >&2
        exit 1
        ;;
esac

rust_pkg="rust-${RUST_VERSION}-${arch}-unknown-linux-gnu"
fetch_verified \
    "https://static.rust-lang.org/dist/${RUST_DIST_DATE}/${rust_pkg}.tar.gz" \
    "${rust_sha}" \
    "/tmp/${rust_pkg}.tar.gz"
tar -C /usr/src -xzf "/tmp/${rust_pkg}.tar.gz"
rm -f "/tmp/${rust_pkg}.tar.gz"

mkdir -p "${PI_STAGE}/usr/local"
run_logged "Installing Rust ${RUST_VERSION}" /tmp/rust-install.log \
    "/usr/src/${rust_pkg}/install.sh" \
    --prefix="${PI_STAGE}/usr/local" --without=rust-docs --disable-ldconfig
rm -rf "/usr/src/${rust_pkg}"

# Verify using the staged binaries
"${PI_STAGE}/usr/local/bin/rustc" --version
"${PI_STAGE}/usr/local/bin/cargo" --version

stage_record "rust" "${RUST_VERSION}"


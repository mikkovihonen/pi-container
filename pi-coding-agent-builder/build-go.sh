#!/bin/bash
# Download, verify, and stage the Go toolchain.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
# shellcheck source=pi-coding-agent-builder/common.sh
source "$(dirname "$0")/common.sh"

require_env GO_VERSION GO_SHA256_AMD64 GO_SHA256_ARM64

case "$(deb_arch)" in
    amd64) go_sha="${GO_SHA256_AMD64}" ;;
    arm64) go_sha="${GO_SHA256_ARM64}" ;;
    *)
        echo "ERROR: no pinned Go tarball for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

go_tarball="go${GO_VERSION}.linux-$(deb_arch).tar.gz"
fetch_verified "https://go.dev/dl/${go_tarball}" "${go_sha}" "/tmp/${go_tarball}"

mkdir -p "${PI_STAGE}/usr/local"
tar -C "${PI_STAGE}/usr/local" -xzf "/tmp/${go_tarball}"
rm -f "/tmp/${go_tarball}"

# Symlink binaries into $PI_STAGE/usr/local/bin so they are on PATH
mkdir -p "${PI_STAGE}/usr/local/bin"
ln -sf ../go/bin/go "${PI_STAGE}/usr/local/bin/go"
ln -sf ../go/bin/gofmt "${PI_STAGE}/usr/local/bin/gofmt"

# Verify using the staged binaries
"${PI_STAGE}/usr/local/bin/go" version

stage_record "go" "${GO_VERSION}"


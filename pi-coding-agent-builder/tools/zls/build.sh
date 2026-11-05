#!/bin/bash
# Build zls Zig language server from source and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env ZLS_VERSION ZLS_COMMIT ZIG_VERSION ZIG_SHA256_AMD64 ZIG_SHA256_ARM64

case "$(deb_arch)" in
    amd64)
        zig_arch="x86_64"
        zig_sha="${ZIG_SHA256_AMD64}"
        ;;
    arm64)
        zig_arch="aarch64"
        zig_sha="${ZIG_SHA256_ARM64}"
        ;;
    *)
        echo "ERROR: unsupported architecture '$(deb_arch)' for zig bootstrap" >&2
        exit 1
        ;;
esac

zig_tarball="zig-linux-${zig_arch}-${ZIG_VERSION}.tar.xz"
fetch_verified "https://ziglang.org/download/${ZIG_VERSION}/${zig_tarball}" "${zig_sha}" "/tmp/${zig_tarball}"
mkdir -p /opt/zig
tar -C /opt/zig --strip-components=1 -xJf "/tmp/${zig_tarball}"
rm -f "/tmp/${zig_tarball}"
export PATH="/opt/zig:${PATH}"

src="/usr/src/zls"
clone_verified "https://github.com/zigtools/zls.git" "${ZLS_VERSION}" "${ZLS_COMMIT}" "${src}"

cd "${src}"
zig build -Doptimize=ReleaseSafe --prefix "${PI_STAGE}/usr/local"
rm -rf "${src}" /opt/zig /root/.cache/zig

"${PI_STAGE}/usr/local/bin/zls" --version
stage_record "zls" "${ZLS_VERSION}"


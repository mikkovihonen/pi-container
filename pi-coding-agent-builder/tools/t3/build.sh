#!/bin/bash
# Download, verify, and stage the T3 Code CLI binary and client assets.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env T3_VERSION T3_SHA256_AMD64 T3_SHA256_ARM64

case "$(deb_arch)" in
    amd64) t3_sha="${T3_SHA256_AMD64}"; t3_arch="x64" ;;
    arm64) t3_sha="${T3_SHA256_ARM64}"; t3_arch="arm64" ;;
    *)
        echo "ERROR: no pinned T3 archive for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

t3_tarball="t3-${T3_VERSION}-linux-${t3_arch}.tar.gz"
t3_url="https://github.com/pingdotgg/t3code/releases/download/v${T3_VERSION}/${t3_tarball}"

fetch_verified "${t3_url}" "${t3_sha}" "/tmp/${t3_tarball}"

mkdir -p "${PI_STAGE}/usr/local/lib/t3"
tar -C "${PI_STAGE}/usr/local/lib/t3" --strip-components=1 -xzf "/tmp/${t3_tarball}"
rm -f "/tmp/${t3_tarball}"

if [ -f "${SCRIPT_DIR}/t3-advertise-host.cjs" ]; then
    cp "${SCRIPT_DIR}/t3-advertise-host.cjs" "${PI_STAGE}/usr/local/lib/t3/t3-advertise-host.cjs"
fi
if [ -f "${SCRIPT_DIR}/t3-seed-settings.cjs" ]; then
    cp "${SCRIPT_DIR}/t3-seed-settings.cjs" "${PI_STAGE}/usr/local/lib/t3/t3-seed-settings.cjs"
fi

mkdir -p "${PI_STAGE}/usr/local/bin"
ln -sf ../lib/t3/t3 "${PI_STAGE}/usr/local/bin/t3"

stage_record "t3" "${T3_VERSION}"


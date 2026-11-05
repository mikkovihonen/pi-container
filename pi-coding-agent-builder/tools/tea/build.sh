#!/bin/bash
# Download, verify, and stage the Gitea / Forgejo CLI (tea) release binary.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env TEA_VERSION TEA_SHA256_AMD64 TEA_SHA256_ARM64

case "$(deb_arch)" in
    amd64) tea_sha="${TEA_SHA256_AMD64}"; tea_arch="amd64" ;;
    arm64) tea_sha="${TEA_SHA256_ARM64}"; tea_arch="arm64" ;;
    *)
        echo "ERROR: no pinned Gitea/Forgejo CLI binary for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

tea_binary="tea-${TEA_VERSION}-linux-${tea_arch}"
tea_url="https://gitea.com/gitea/tea/releases/download/v${TEA_VERSION}/${tea_binary}"

mkdir -p "${PI_STAGE}/usr/local/bin"
fetch_verified "${tea_url}" "${tea_sha}" "${PI_STAGE}/usr/local/bin/tea"
chmod 0755 "${PI_STAGE}/usr/local/bin/tea"
ln -sf tea "${PI_STAGE}/usr/local/bin/fj"

"${PI_STAGE}/usr/local/bin/tea" --version
stage_record "tea" "${TEA_VERSION}"

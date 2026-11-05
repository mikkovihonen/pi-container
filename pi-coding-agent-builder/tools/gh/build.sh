#!/bin/bash
# Download, verify, and stage the GitHub CLI (gh) release binary.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env GH_VERSION GH_SHA256_AMD64 GH_SHA256_ARM64

case "$(deb_arch)" in
    amd64) gh_sha="${GH_SHA256_AMD64}"; gh_arch="amd64" ;;
    arm64) gh_sha="${GH_SHA256_ARM64}"; gh_arch="arm64" ;;
    *)
        echo "ERROR: no pinned GitHub CLI archive for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

gh_tarball="gh_${GH_VERSION}_linux_${gh_arch}.tar.gz"
gh_url="https://github.com/cli/cli/releases/download/v${GH_VERSION}/${gh_tarball}"

fetch_verified "${gh_url}" "${gh_sha}" "/tmp/${gh_tarball}"

mkdir -p "${PI_STAGE}/usr/local"
tar -C "${PI_STAGE}/usr/local" --strip-components=1 -xzf "/tmp/${gh_tarball}"
rm -f "/tmp/${gh_tarball}"

"${PI_STAGE}/usr/local/bin/gh" --version
stage_record "gh" "${GH_VERSION}"

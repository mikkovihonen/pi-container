#!/bin/bash
# Download, verify, and stage the GitLab CLI (glab) release binary.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env GLAB_VERSION GLAB_SHA256_AMD64 GLAB_SHA256_ARM64

case "$(deb_arch)" in
    amd64) glab_sha="${GLAB_SHA256_AMD64}"; glab_arch="amd64" ;;
    arm64) glab_sha="${GLAB_SHA256_ARM64}"; glab_arch="arm64" ;;
    *)
        echo "ERROR: no pinned GitLab CLI archive for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

glab_tarball="glab_${GLAB_VERSION}_linux_${glab_arch}.tar.gz"
glab_url="https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/packages/generic/glab/${GLAB_VERSION}/${glab_tarball}"

fetch_verified "${glab_url}" "${glab_sha}" "/tmp/${glab_tarball}"

mkdir -p "${PI_STAGE}/usr/local"
tar -C "${PI_STAGE}/usr/local" -xzf "/tmp/${glab_tarball}" bin/glab
rm -f "/tmp/${glab_tarball}"

"${PI_STAGE}/usr/local/bin/glab" --version
stage_record "glab" "${GLAB_VERSION}"

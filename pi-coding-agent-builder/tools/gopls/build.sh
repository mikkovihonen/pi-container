#!/bin/bash
# Build gopls language server from source using the builder Go toolchain and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env GOPLS_VERSION GOPLS_COMMIT

src="/usr/src/tools"
clone_verified "https://github.com/golang/tools.git" "${GOPLS_VERSION}" "${GOPLS_COMMIT}" "${src}"

cd "${src}/gopls"
mkdir -p "${PI_STAGE}/usr/local/bin"
go build -o "${PI_STAGE}/usr/local/bin/gopls"
rm -rf "${src}" /root/go

"${PI_STAGE}/usr/local/bin/gopls" version
stage_record "gopls" "${GOPLS_VERSION}"


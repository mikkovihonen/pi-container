#!/bin/bash
# Build fd from source and stage it as /usr/local/bin/fd.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env FD_VERSION FD_COMMIT

src="/usr/src/fd"
clone_verified "https://github.com/sharkdp/fd.git" "${FD_VERSION}" "${FD_COMMIT}" "${src}"

cd "${src}"
cargo build --release
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/fd "${PI_STAGE}/usr/local/bin/fd"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/fd" --version
stage_record "fd" "${FD_VERSION}"


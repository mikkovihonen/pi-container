#!/bin/bash
# Build ripgrep (rg) with PCRE2 support from source and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env RIPGREP_VERSION RIPGREP_COMMIT

src="/usr/src/ripgrep"
clone_verified "https://github.com/BurntSushi/ripgrep.git" "${RIPGREP_VERSION}" "${RIPGREP_COMMIT}" "${src}"

cd "${src}"
cargo build --release --features 'pcre2'
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/rg "${PI_STAGE}/usr/local/bin/rg"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/rg" --version
stage_record "ripgrep" "${RIPGREP_VERSION}"


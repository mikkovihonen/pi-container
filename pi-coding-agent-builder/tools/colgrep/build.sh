#!/bin/bash
# Build colgrep multi-vector semantic code search CLI from source and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env COLGREP_VERSION COLGREP_COMMIT

src="/usr/src/colgrep"
echo "Cloning colgrep ${COLGREP_VERSION}"
git -c advice.detachedHead=false clone --quiet --depth 1 "https://github.com/lightonai/next-plaid.git" "${src}"
head="$(git -C "${src}" rev-parse HEAD)"
if [ "${head}" != "${COLGREP_COMMIT}" ]; then
    echo "ERROR: colgrep resolves to ${head}, expected ${COLGREP_COMMIT}." >&2
    exit 1
fi

cd "${src}"
cargo build --release -p colgrep
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/colgrep "${PI_STAGE}/usr/local/bin/colgrep"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/colgrep" --version
stage_record "colgrep" "${COLGREP_VERSION}"


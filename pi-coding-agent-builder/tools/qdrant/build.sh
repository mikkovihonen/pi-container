#!/bin/bash
# Build Qdrant vector database from source with SIMD acceleration and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env QDRANT_VERSION QDRANT_COMMIT

src="/usr/src/qdrant"
clone_verified "https://github.com/qdrant/qdrant.git" "${QDRANT_VERSION}" "${QDRANT_COMMIT}" "${src}"

cd "${src}"
cargo build --release --bin qdrant --no-default-features --features service
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/qdrant "${PI_STAGE}/usr/local/bin/qdrant"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/qdrant" --version
stage_record "qdrant" "${QDRANT_VERSION}"


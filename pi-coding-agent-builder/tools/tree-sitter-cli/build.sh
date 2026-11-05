#!/bin/bash
# Build tree-sitter CLI from source and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env TREE_SITTER_VERSION TREE_SITTER_COMMIT

src="/usr/src/tree-sitter"
clone_verified "https://github.com/tree-sitter/tree-sitter.git" "${TREE_SITTER_VERSION}" "${TREE_SITTER_COMMIT}" "${src}"

cd "${src}"
cargo build --release -p tree-sitter-cli
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/tree-sitter "${PI_STAGE}/usr/local/bin/tree-sitter"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/tree-sitter" --version
stage_record "tree-sitter-cli" "${TREE_SITTER_VERSION}"


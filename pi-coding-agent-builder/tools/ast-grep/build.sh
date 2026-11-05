#!/bin/bash
# Build ast-grep and sg from source and stage them.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env AST_GREP_VERSION AST_GREP_COMMIT

src="/usr/src/ast-grep"
clone_verified "https://github.com/ast-grep/ast-grep.git" "${AST_GREP_VERSION}" "${AST_GREP_COMMIT}" "${src}"

cd "${src}"
cargo build --release -p ast-grep
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/ast-grep "${PI_STAGE}/usr/local/bin/ast-grep"
ln -sf ast-grep "${PI_STAGE}/usr/local/bin/sg"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/ast-grep" --version
stage_record "ast-grep" "${AST_GREP_VERSION}"


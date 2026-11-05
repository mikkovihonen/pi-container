#!/bin/bash
# Build rust-analyzer from source using the builder Rust toolchain and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env RUST_ANALYZER_VERSION RUST_ANALYZER_COMMIT

src="/usr/src/rust-analyzer"
clone_verified "https://github.com/rust-lang/rust-analyzer.git" "${RUST_ANALYZER_VERSION}" "${RUST_ANALYZER_COMMIT}" "${src}"

cd "${src}"
cargo build --release -p rust-analyzer
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 target/release/rust-analyzer "${PI_STAGE}/usr/local/bin/rust-analyzer"
rm -rf "${src}" /root/.cargo/registry /root/.cargo/git

"${PI_STAGE}/usr/local/bin/rust-analyzer" --version
stage_record "rust-analyzer" "${RUST_ANALYZER_VERSION}"


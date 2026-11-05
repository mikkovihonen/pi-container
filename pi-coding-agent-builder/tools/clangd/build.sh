#!/bin/bash
# Build clangd C/C++ language server from source and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env CLANGD_VERSION CLANGD_COMMIT

src="/usr/src/llvm-project"
clone_verified "https://github.com/llvm/llvm-project.git" "${CLANGD_VERSION}" "${CLANGD_COMMIT}" "${src}"

mkdir -p /tmp/llvm-build
cmake -G Ninja -S "${src}/llvm" -B /tmp/llvm-build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${PI_STAGE}/usr/local" \
    -DLLVM_ENABLE_PROJECTS="clang;clang-tools-extra" \
    -DLLVM_TARGETS_TO_BUILD="host" \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_ENABLE_BINDINGS=OFF

ninja -C /tmp/llvm-build -j "$(mem_capped_jobs 800)" clangd
mkdir -p "${PI_STAGE}/usr/local/bin"
install -m755 /tmp/llvm-build/bin/clangd "${PI_STAGE}/usr/local/bin/clangd"
rm -rf /tmp/llvm-build "${src}"

"${PI_STAGE}/usr/local/bin/clangd" --version
stage_record "clangd" "${CLANGD_VERSION}"


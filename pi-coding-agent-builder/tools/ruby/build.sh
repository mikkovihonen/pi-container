#!/bin/bash
# Build Ruby from source with Rust-based YJIT and jemalloc and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env RUBY_VERSION RUBY_SHA256

tarball="ruby-${RUBY_VERSION}.tar.xz"
fetch_verified "https://cache.ruby-lang.org/pub/ruby/3.4/${tarball}" "${RUBY_SHA256}" "/tmp/${tarball}"

src="/usr/src/ruby-${RUBY_VERSION}"
mkdir -p "${src}"
tar -C /usr/src -xJf "/tmp/${tarball}"
rm -f "/tmp/${tarball}"

cd "${src}"
./configure --prefix="${PI_STAGE}/usr/local" \
    --enable-yjit \
    --with-jemalloc \
    --disable-install-doc
make -j "$(mem_capped_jobs 400)"
make install
rm -rf "${src}"

"${PI_STAGE}/usr/local/bin/ruby" --version
stage_record "ruby" "${RUBY_VERSION}"


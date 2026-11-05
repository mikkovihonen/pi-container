#!/bin/bash
# Compile sqlite-vec vector search extension for SQLite from source with SIMD kernels and stage it.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env SQLITE_VEC_VERSION SQLITE_VEC_COMMIT

src="/usr/src/sqlite-vec"
clone_verified "https://github.com/asg017/sqlite-vec.git" "${SQLITE_VEC_VERSION}" "${SQLITE_VEC_COMMIT}" "${src}"

cd "${src}"
ver="$(cat VERSION | tr -d ' \n\r')"
major="$(echo "${ver}" | cut -d. -f1)"
minor="$(echo "${ver}" | cut -d. -f2)"
patch="$(echo "${ver}" | cut -d. -f3 | cut -d- -f1)"

python3 -c "
import sys
tmpl = open('sqlite-vec.h.tmpl').read()
out = (
    tmpl.replace('\${VERSION}', '${ver}')
    .replace('\${VERSION_MAJOR}', '${major}')
    .replace('\${VERSION_MINOR}', '${minor}')
    .replace('\${VERSION_PATCH}', '${patch}')
    .replace('\${SOURCE}', '${SQLITE_VEC_COMMIT}')
    .replace('\${DATE}', '2024-01-01T00:00:00Z')
)
open('sqlite-vec.h', 'w').write(out)
"

arch="$(gnu_arch)"
simd_flags=""
if [ "${arch}" = "x86_64" ]; then
    simd_flags="-mavx -DSQLITE_VEC_ENABLE_AVX"
elif [ "${arch}" = "aarch64" ]; then
    simd_flags="-march=armv8-a+simd -DSQLITE_VEC_ENABLE_NEON -flax-vector-conversions"
fi

gcc -O3 -fPIC -shared -Wall -Wextra -Ivendor/ -I. -lm ${simd_flags} sqlite-vec.c -o vec0.so
mkdir -p "${PI_STAGE}/usr/local/lib/sqlite" "${PI_STAGE}/usr/local/include"
install -m755 vec0.so "${PI_STAGE}/usr/local/lib/sqlite/vec0.so"
install -m644 sqlite-vec.h "${PI_STAGE}/usr/local/include/sqlite-vec.h"
rm -rf "${src}"

test -f "${PI_STAGE}/usr/local/lib/sqlite/vec0.so"
stage_record "sqlite-vec" "${SQLITE_VEC_VERSION}"

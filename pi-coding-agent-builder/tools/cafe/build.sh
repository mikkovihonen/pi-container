#!/bin/bash
# Download, verify, and stage the GitCafe CLI (cafe) with Bun runtime.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env CAFE_VERSION CAFE_SHA256 BUN_VERSION BUN_SHA256_AMD64 BUN_SHA256_ARM64

case "$(deb_arch)" in
    amd64) bun_sha="${BUN_SHA256_AMD64}"; bun_arch="x64" ;;
    arm64) bun_sha="${BUN_SHA256_ARM64}"; bun_arch="aarch64" ;;
    *)
        echo "ERROR: no pinned Bun archive for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

bun_zip="bun-linux-${bun_arch}.zip"
bun_url="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/${bun_zip}"
fetch_verified "${bun_url}" "${bun_sha}" "/tmp/${bun_zip}"

cafe_tarball="cli-${CAFE_VERSION}.tgz"
cafe_url="https://registry.npmjs.org/@gitcafe/cli/-/cli-${CAFE_VERSION}.tgz"
fetch_verified "${cafe_url}" "${CAFE_SHA256}" "/tmp/${cafe_tarball}"

mkdir -p "${PI_STAGE}/usr/local/lib/cafe/bin"
unzip -q -j "/tmp/${bun_zip}" "bun-linux-${bun_arch}/bun" -d "${PI_STAGE}/usr/local/lib/cafe/bin"
chmod 0755 "${PI_STAGE}/usr/local/lib/cafe/bin/bun"
rm -f "/tmp/${bun_zip}"

tar -C "${PI_STAGE}/usr/local/lib/cafe" --strip-components=1 -xzf "/tmp/${cafe_tarball}"
chmod 0755 "${PI_STAGE}/usr/local/lib/cafe/cafe.js"
rm -f "/tmp/${cafe_tarball}"

mkdir -p "${PI_STAGE}/usr/local/bin"
cat << 'EOF' > "${PI_STAGE}/usr/local/bin/cafe"
#!/bin/sh
exec /usr/local/lib/cafe/bin/bun /usr/local/lib/cafe/cafe.js "$@"
EOF
chmod 0755 "${PI_STAGE}/usr/local/bin/cafe"

"${PI_STAGE}/usr/local/bin/cafe" --version || true
stage_record "cafe" "${CAFE_VERSION}"


#!/bin/bash
# Install, configure, and stage Azure CLI (az) with azure-devops extension.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${SCRIPT_DIR}/../../common.sh"
[ -f "${COMMON_SH}" ] || COMMON_SH="/build/common.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"

require_env AZ_VERSION AZURE_DEVOPS_VERSION AZURE_DEVOPS_SHA256

whl_name="azure_devops-${AZURE_DEVOPS_VERSION}-py2.py3-none-any.whl"
whl_url="https://github.com/Azure/azure-devops-cli-extension/releases/download/20261015.1/${whl_name}"

fetch_verified "${whl_url}" "${AZURE_DEVOPS_SHA256}" "/tmp/${whl_name}"

az_dir="${PI_STAGE}/usr/local/lib/azure-cli"
mkdir -p "${az_dir}"
/usr/local/bin/uv venv --python /usr/local/bin/python3 "${az_dir}"
/usr/local/bin/uv pip install --python "${az_dir}/bin/python" "azure-cli==${AZ_VERSION}" distro

ext_sys_dir="${az_dir}/extensions"
mkdir -p "${ext_sys_dir}"
AZURE_EXTENSION_SYS_DIR="${ext_sys_dir}" "${az_dir}/bin/python" -m azure.cli extension add --source "/tmp/${whl_name}" --system --yes
rm -f "/tmp/${whl_name}"

mkdir -p "${PI_STAGE}/usr/local/bin"
cat << 'EOF' > "${PI_STAGE}/usr/local/bin/az"
#!/bin/sh
export AZURE_EXTENSION_SYS_DIR="/usr/local/lib/azure-cli/extensions"
exec /usr/local/lib/azure-cli/bin/python3 -m azure.cli "$@"
EOF
chmod 0755 "${PI_STAGE}/usr/local/bin/az"

AZURE_EXTENSION_SYS_DIR="${ext_sys_dir}" "${az_dir}/bin/python" -m azure.cli version
stage_record "az" "${AZ_VERSION}"

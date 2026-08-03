#!/bin/bash
set -euo pipefail

# NOTE ON ERROR HANDLING
# Every step below is a separate statement. Do NOT chain them with `&&`:
# `set -e` is ignored for every command of an AND-OR list except the last one
# (POSIX), so `wget ... && ./configure ... && make ...` silently falls through
# on failure. Combined with `> /dev/null 2>&1` that turned any failed CPython
# build into a bare "line 59: pip: command not found" further down the script.

PYTHON_VERSION="3.14.6"
PYTHON_SHA256="74d0d71d0600e477651a077101d6e62d1e2e69b8e992ba18c993dd643b7ba222"
BUILD_DIR="/tmp/python-build"

set_env_var() {
    local key="$1"
    local value="$2"

    # Construct the line formatted as KEY=\ VALUE\
    local line="${key}=${value}"

    # Check if the key already exists in the file
    if grep -q "^${key}=" /etc/environment; then
        # Replace the existing line
        sed -i "s|^${key}=.*|${line}|" /etc/environment
    else
        # Append the new line to the file
        echo "${line}" | tee -a /etc/environment > /dev/null
    fi
    export ${key}=${value}
}

# Run a verbose build step with its output captured to a log file. On failure,
# dump the tail of that log to stderr so the actual error reaches the build
# output (build.py streams every line at INFO) instead of being swallowed.
run_logged() {
    local desc="$1"
    local log="$2"
    shift 2

    echo "${desc}"
    if ! "$@" > "${log}" 2>&1; then
        echo "ERROR: ${desc} failed. Last 60 lines of ${log}:" >&2
        tail -n 60 "${log}" >&2
        exit 1
    fi
}

set_env_var "PYTHON_VERSION" "${PYTHON_VERSION}"
set_env_var "UV_SYSTEM_CERTS" "1"

run_logged "Updating apt indexes" /tmp/apt-update.log apt-get update
run_logged "Installing apt packages" /tmp/apt-install.log \
    apt-get install -y \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libncurses5-dev \
    libncursesw5-dev \
    libreadline-dev \
    libsqlite3-dev \
    libgdbm-dev \
    libdb5.3-dev \
    libbz2-dev \
    libexpat1-dev \
    liblzma-dev \
    libffi-dev \
    uuid-dev

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

# -nv keeps output to one line but, unlike -q, still reports failures.
echo "Downloading Python ${PYTHON_VERSION}"
wget -nv "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
echo "${PYTHON_SHA256}  Python-${PYTHON_VERSION}.tgz" | sha256sum -c -
tar -xf "Python-${PYTHON_VERSION}.tgz"
cd "Python-${PYTHON_VERSION}"

run_logged "Configuring Python ${PYTHON_VERSION}" /tmp/python-configure.log \
    ./configure --enable-optimizations

# Cap make parallelism by MEMORY, not cores. A PGO-instrumented compile of the
# larger translation units (Parser/parser.o especially) peaks close to 1 GiB, so
# `-j $(nproc)` OOM-kills the compiler on a container VM that has many cores but
# little RAM — the podman machine defaults to 2 GiB regardless of CPU count. The
# failure surfaces as:
#   gcc: fatal error: Killed signal terminated program cc1
# Giving the VM more memory (podman machine set --memory) automatically raises
# this cap. MAKE_JOBS overrides it, but note the build does not inherit the host
# environment — set it here or plumb it through the Containerfile as an ARG/ENV.
MIB_PER_JOB=900
cpu_jobs="$(nproc)"
mem_avail_mib="$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)"
mem_jobs=$(( mem_avail_mib / MIB_PER_JOB ))
if [ "${mem_jobs}" -lt 1 ]; then
    mem_jobs=1
fi
make_jobs="${MAKE_JOBS:-$(( mem_jobs < cpu_jobs ? mem_jobs : cpu_jobs ))}"
echo "Using ${make_jobs} make job(s) (cpus=${cpu_jobs}, available memory=${mem_avail_mib} MiB)"

run_logged "Building Python ${PYTHON_VERSION}" /tmp/python-make.log \
    make -s -j "${make_jobs}"
run_logged "Installing Python ${PYTHON_VERSION}" /tmp/python-make-install.log \
    make install

# `make install` (ensurepip) provides python3/python3.14 and pip3/pip3.14 only;
# the unversioned aliases below are what the rest of this script and
# pi/commands.sh expect on PATH. `ln -sf` succeeds even when its target does not
# exist (leaving a dangling symlink that reads as "command not found"), so
# verify afterwards rather than trusting the symlinks.
ln -sf /usr/local/bin/python3.14 /usr/local/bin/python
ln -sf /usr/local/bin/pip3.14 /usr/local/bin/pip
ln -sf /usr/local/bin/idle3.14 /usr/local/bin/idle
ln -sf /usr/local/bin/pydoc3.14 /usr/local/bin/pydoc
ln -sf /usr/local/bin/python3.14-config /usr/local/bin/python-config

for tool in python pip; do
    if ! command -v "${tool}" > /dev/null; then
        echo "ERROR: ${tool} is not on PATH after installing Python ${PYTHON_VERSION}." >&2
        echo "Contents of /usr/local/bin:" >&2
        ls -l /usr/local/bin >&2
        exit 1
    fi
done
python --version
pip --version

# Drop the source tree and tarball (~600 MB); otherwise they are baked into the
# image layer.
cd /
rm -rf "${BUILD_DIR}"

export PIP_ROOT_USER_ACTION=ignore

pip install --root-user-action=ignore --upgrade pip
pip install --root-user-action=ignore -r /dev/stdin <<EOF
uv==0.11.30 \
    --hash=sha256:cc28cb55c2b3c80a26ea374a172fec70b0561ada211e6fb23936ccea3ecb80b2 \
    --hash=sha256:ea5f0d4fe452dc3daf915c714504eb2f1e570f8ebac752abf51f9e6f58a1ff68 \
    --hash=sha256:7c41e83a2811c22e04ae50d0986932318ba82e6f9e29f0fca727d855df6bd959 \
    --hash=sha256:988133c7f44c6c64f6fef482483014995260cdb3a68270805256ddb9e6fed9e8 \
    --hash=sha256:7d9d922cfef27757156f1023eb057abab192e3e9f5436ba60eac57ffbc2b5c23 \
    --hash=sha256:a2aff328164d7e8fbcf6b82182cc16f7a729ec7edfce77b4d0c2908fca12bd63 \
    --hash=sha256:6a29031ff95150ea6156607394db8f79dfb06d5287f46ff07e3f60b9df76121c
EOF

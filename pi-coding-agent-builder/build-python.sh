#!/bin/bash
# Build CPython from source and stage it, with uv and podman-compose, for the
# agent image.
#
# WHY FROM SOURCE
# The agent image is Node-based and has no python3 at all. Debian's python3 would
# drag in a second interpreter tree at 3.13; this builds the version the workspace
# actually targets (requires-python >=3.14) and keeps a single interpreter on PATH.
#
# WHY HERE AND NOT PER PROJECT
# This used to live in each workspace's .pi-container/dependencies/root/commands.sh,
# which meant a workspace whose root/commands.sh was the default no-op got no Python
# at all (while pi/commands.sh's own template tells people to run `python -m venv`),
# and every project-specific image rebuild recompiled CPython — a PGO build, so
# minutes of CPU and the most OOM-prone step in the whole build. Building it once
# here fixes both, and gives the nested-container compose provider (podman-compose)
# an interpreter to run on.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
#
# NOTE ON ERROR HANDLING
# Every step below is a separate statement. Do NOT chain them with `&&`:
# `set -e` is ignored for every command of an AND-OR list except the last one
# (POSIX), so `wget ... && ./configure ... && make ...` silently falls through
# on failure. Combined with `> /dev/null 2>&1` that turned any failed CPython
# build into a bare "line 59: pip: command not found" further down the script.
set -euo pipefail
# shellcheck source=pi-coding-agent-builder/common.sh
source "$(dirname "$0")/common.sh"

# Pins come from Containerfile as build ARGs; see require_env in common.sh.
# The *_SHA256 values for the pip packages are space-separated lists — pip needs one
# --hash per wheel it might resolve for this architecture.
require_env \
    PYTHON_VERSION PYTHON_SHA256 \
    UV_VERSION UV_SHA256 \
    PODMAN_COMPOSE_VERSION PODMAN_COMPOSE_SHA256 \
    PYTHON_DOTENV_VERSION PYTHON_DOTENV_SHA256 \
    PYYAML_VERSION PYYAML_SHA256

BUILD_DIR="/usr/src/python"

# Peak RSS of one compile job. A PGO-instrumented compile of the larger
# translation units (Parser/parser.o especially) gets close to 1 GiB; a plain
# build is far lighter.
if [ "${PYTHON_OPTIMIZE:-1}" = "1" ]; then
    MIB_PER_JOB=900
    CONFIGURE_ARGS=(--enable-optimizations)
else
    MIB_PER_JOB=400
    CONFIGURE_ARGS=()
fi

# Fail fast on a builder that cannot fit even one job, instead of compiling for
# several minutes and dying at `gcc: fatal error: Killed signal terminated
# program cc1`. build.py checks this on the host before starting the build; this
# is the backstop for a build started some other way, and for memory that was
# consumed between that check and here.
mem_avail="$(mem_avail_mib)"
if [ "${mem_avail}" -lt "${MIB_PER_JOB}" ]; then
    echo "ERROR: only ${mem_avail} MiB available; building Python ${PYTHON_VERSION}" >&2
    echo "       needs ~${MIB_PER_JOB} MiB for a single compile job and would be OOM-killed." >&2
    echo "" >&2
    echo "Fix one of:" >&2
    echo "  * give the VM more memory:  podman machine stop && podman machine set --memory 4096 && podman machine start" >&2
    echo "  * free memory in the VM:    stop containers you are not using (podman ps)" >&2
    echo "  * skip the PGO build:       build with --build-arg PYTHON_OPTIMIZE=0 (~10-20% slower Python)" >&2
    exit 1
fi

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

fetch_verified \
    "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" \
    "${PYTHON_SHA256}" \
    "Python-${PYTHON_VERSION}.tgz"
tar -xf "Python-${PYTHON_VERSION}.tgz"
cd "Python-${PYTHON_VERSION}"

run_logged "Configuring Python ${PYTHON_VERSION}" /tmp/python-configure.log \
    ./configure --prefix=/usr/local "${CONFIGURE_ARGS[@]}"

# MAKE_JOBS overrides the memory-derived cap; the Containerfile exposes it as a
# build ARG. mem_capped_jobs re-reads the memory rather than reusing the preflight's
# number: the download and ./configure both ran in between, so the figure from the
# top of this script is stale by the time it decides how many compilers to run.
make_jobs="${MAKE_JOBS:-$(mem_capped_jobs "${MIB_PER_JOB}")}"
echo "Using ${make_jobs} make job(s) (cpus=$(nproc), available memory=$(mem_avail_mib) MiB)"

# Not run_logged: this step has one failure mode worth explaining on the spot.
# --enable-optimizations makes `make` run part of CPython's own test suite to
# collect profile data, and a single failing test there fails the build. That has
# been observed under memory pressure in this builder and passes on a plain retry,
# so the hint matters — otherwise it reads as a compiler bug.
echo "Building Python ${PYTHON_VERSION}"
if ! make -s -j "${make_jobs}" > /tmp/python-make.log 2>&1; then
    echo "ERROR: building Python ${PYTHON_VERSION} failed. Last 60 lines of /tmp/python-make.log:" >&2
    tail -n 60 /tmp/python-make.log >&2
    if grep -q 'profile-run-stamp' /tmp/python-make.log; then
        echo "" >&2
        echo "The failure is in the PGO profile run: CPython ran its own test suite to collect" >&2
        echo "profile data and at least one test failed. In this builder that is usually" >&2
        echo "resource pressure rather than a broken compiler, and a plain retry passes." >&2
        echo "If it keeps failing, build with PYTHON_OPTIMIZE=0 to skip the PGO stage." >&2
    fi
    exit 1
fi

# Installed twice, on purpose:
#   * into the builder's own /usr/local, because pip has to run on a working
#     interpreter to stage the packages below, and
#   * into $PI_STAGE, which is what the agent image copies.
# DESTDIR only relocates where files are written; the interpreter still believes
# its prefix is /usr/local, so the staged tree is correct in the agent image and
# every ensurepip-generated shebang points at /usr/local/bin/python3.14.
run_logged "Installing Python ${PYTHON_VERSION}" /tmp/python-make-install.log \
    make install
run_logged "Staging Python ${PYTHON_VERSION}" /tmp/python-make-stage.log \
    make install DESTDIR="${PI_STAGE}"

# `make install` (ensurepip) provides python3/python3.14 and pip3/pip3.14 only;
# the unversioned aliases below are what project scripts expect on PATH.
# `ln -sf` succeeds even when its target does not exist (leaving a dangling
# symlink that reads as "command not found"), so verify afterwards rather than
# trusting the symlinks.
py_minor="${PYTHON_VERSION%.*}"
for prefix in /usr/local "${PI_STAGE}/usr/local"; do
    ln -sf "python${py_minor}" "${prefix}/bin/python"
    ln -sf "pip${py_minor}" "${prefix}/bin/pip"
    ln -sf "idle${py_minor}" "${prefix}/bin/idle"
    ln -sf "pydoc${py_minor}" "${prefix}/bin/pydoc"
    ln -sf "python${py_minor}-config" "${prefix}/bin/python-config"
done

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

# Drop the source tree and tarball (~600 MB) before pip runs, so a build that
# fails later has already given the disk back.
cd /
rm -rf "${BUILD_DIR}"

export PIP_ROOT_USER_ACTION=ignore

# `--root ${PI_STAGE}` writes into the staged tree while keeping paths and script
# shebangs rooted at /usr/local. `--ignore-installed` is required with it: pip
# checks satisfaction against the *running* environment, where these are already
# present after the install above, and would otherwise report "Requirement already
# satisfied" and stage nothing at all.
stage_pip() {
    pip install --root-user-action=ignore --root "${PI_STAGE}" --ignore-installed "$@"
}

# Emit one hash-pinned requirement line from a name, a version and a space-separated
# list of sha256 values. The hashes live in Containerfile with everything else this
# image is pinned to; this only reassembles them into the format pip parses.
#
# A requirement with no hash at all would install whatever the index serves, so treat
# an empty list as a bug rather than writing a bare `name==version`.
requirement() {
    local name="$1" version="$2" hashes="$3" hash count=0

    printf '%s==%s' "${name}" "${version}"
    # Unquoted on purpose: the caller passes one string and word splitting is how it
    # becomes a list of hashes.
    # shellcheck disable=SC2086
    for hash in ${hashes}; do
        printf ' \\\n    --hash=sha256:%s' "${hash}"
        count=$((count + 1))
    done
    printf '\n'

    if [ "${count}" -eq 0 ]; then
        echo "ERROR: no hashes given for ${name}==${version}." >&2
        return 1
    fi
}

pip install --root-user-action=ignore --upgrade pip
stage_pip --upgrade pip

requirement uv "${UV_VERSION}" "${UV_SHA256}" > /tmp/req-uv.txt
stage_pip -r /tmp/req-uv.txt

# podman-compose: the compose provider for nested containers (`docker compose` /
# `podman compose` inside the agent). Pure Python, so it runs on the interpreter
# built above rather than pulling Debian's separate python3 in as the apt package
# would. Hash-pinned, including its two runtime dependencies — pip requires
# hashes for the whole resolved set once any requirement has one.
{
    requirement podman-compose "${PODMAN_COMPOSE_VERSION}" "${PODMAN_COMPOSE_SHA256}"
    requirement python-dotenv "${PYTHON_DOTENV_VERSION}" "${PYTHON_DOTENV_SHA256}"
    requirement pyyaml "${PYYAML_VERSION}" "${PYYAML_SHA256}"
} > /tmp/req-compose.txt
stage_pip -r /tmp/req-compose.txt

# ─── Verify the STAGED tree, not the builder's own ───────────────────────────
# Only $PI_STAGE reaches the agent image, and the two can disagree: a missing
# --ignore-installed silently stages nothing, and a wrong --root stages files
# under paths that will never exist. So check the staged copy end to end.
stage_bin="${PI_STAGE}/usr/local/bin"
stage_site="${PI_STAGE}/usr/local/lib/python${py_minor}/site-packages"

for tool in python python"${py_minor}" pip uv podman-compose; do
    if [ ! -e "${stage_bin}/${tool}" ]; then
        echo "ERROR: ${tool} was not staged into ${stage_bin}." >&2
        ls -l "${stage_bin}" >&2
        exit 1
    fi
done

# A staged script whose shebang points into $PI_STAGE would be broken the moment
# it is copied. This is the one failure mode --root exists to prevent, so assert it.
compose_shebang="$(head -n 1 "${stage_bin}/podman-compose")"
if [ "${compose_shebang}" != "#!/usr/local/bin/python${py_minor}" ]; then
    echo "ERROR: staged podman-compose has shebang '${compose_shebang}'," >&2
    echo "       expected '#!/usr/local/bin/python${py_minor}'." >&2
    exit 1
fi

# The staged interpreter derives its prefix from its own location, so running it
# from $PI_STAGE exercises the staged stdlib rather than the builder's.
"${stage_bin}/python${py_minor}" --version
"${stage_bin}/uv" --version
# NOT `podman-compose --version`: it shells out to `podman version`, and podman is
# staged by a later script. Import and print the version instead.
PYTHONPATH="${stage_site}" "${stage_bin}/python${py_minor}" \
    -c 'import podman_compose; print("podman-compose", podman_compose.__version__)'

if [ "${PYTHON_OPTIMIZE:-1}" = "1" ]; then
    stage_record "python" "${PYTHON_VERSION} (PGO)"
else
    stage_record "python" "${PYTHON_VERSION} (no PGO)"
fi
stage_record "uv" "$("${stage_bin}/uv" --version | awk '{print $2}')"
stage_record "podman-compose" "${PODMAN_COMPOSE_VERSION}"

#!/bin/bash
# Provide Node.js for the agent image, and stage it under $PI_STAGE.
#
# WHY THIS IS HERE AT ALL
# The agent image used to *be* the `node:<ver>-trixie-slim` image. That made Node the
# one component whose version was chosen by a base-image tag rather than by this
# repository, with two consequences: the image inherited whatever else that tag
# carried, and moving off a Node release meant moving off a base image. Doing it here
# puts Node on the same footing as everything else in this directory — pinned by
# content, verified, and bumped by editing one line.
#
# TWO MODES (NODE_SOURCE)
#   prebuilt  (default) Fetch the official linux-$arch tarball from nodejs.org and
#                       stage it. Seconds, and it is byte-for-byte the Node the agent
#                       image had before: the `node:` images are built by extracting
#                       exactly this tarball into /usr/local with --strip-components=1
#                       (verified — that is where their /usr/local/CHANGELOG.md comes
#                       from). So "prebuilt" is not a downgrade from the base image;
#                       it *is* the base image's Node, with the version pinned here.
#   build               Compile from source. Costs ~65 minutes on a 9-core/8 GB machine
#                       and produces a trixie-native build rather than the generic
#                       glibc one. Worth it only if you need to change the configure
#                       flags below — Node has no build tags worth choosing, so unlike
#                       podman there is no correctness argument for compiling.
#
# Both modes run the same verification at the end, which is the point of keeping them
# in one script: whichever way Node arrives, the same parity checks apply.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
# shellcheck source=pi-coding-agent-builder/common.sh
source "$(dirname "$0")/common.sh"

# Pins come from Containerfile as build ARGs; see require_env in common.sh. All four are
# required in both modes even though each mode reads only some of them: that way a
# NODE_VERSION bump cannot half-land, with the unused mode still pointing at the hashes
# of the previous release.
require_env \
    NODE_VERSION NODE_SRC_SHA256 \
    NODE_BIN_SHA256_X64 NODE_BIN_SHA256_ARM64

NODE_SOURCE="${NODE_SOURCE:-prebuilt}"

BUILD_DIR="/usr/src/node"

# ─── prebuilt ────────────────────────────────────────────────────────────────

stage_prebuilt_node() {
    local node_arch sha tarball

    # Node's own naming, which is neither Debian's nor the GNU triple's.
    case "$(deb_arch)" in
        amd64)
            node_arch="x64"
            sha="${NODE_BIN_SHA256_X64}"
            ;;
        arm64)
            node_arch="arm64"
            sha="${NODE_BIN_SHA256_ARM64}"
            ;;
        *)
            echo "ERROR: no pinned Node tarball for '$(deb_arch)'" >&2
            return 1
            ;;
    esac

    tarball="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
    fetch_verified \
        "https://nodejs.org/dist/v${NODE_VERSION}/${tarball}" \
        "${sha}" \
        "/tmp/${tarball}"

    # --strip-components=1 drops the node-v.../ prefix so bin/, lib/, include/ and
    # share/ land directly under /usr/local, exactly as the node: image does.
    # CHANGELOG.md and README.md are excluded: they would sit loose in /usr/local and
    # `make install` does not produce them either, so excluding them keeps the two
    # modes' output identical. LICENSE is kept.
    mkdir -p "${PI_STAGE}/usr/local"
    tar -xJf "/tmp/${tarball}" -C "${PI_STAGE}/usr/local" \
        --strip-components=1 --no-same-owner \
        --exclude=CHANGELOG.md --exclude=README.md
    rm -f "/tmp/${tarball}"
}

# ─── build ───────────────────────────────────────────────────────────────────

build_node_from_source() {
    # Peak RSS of one compile job. V8's generated builtins and regexp/wasm sources are
    # the heavy ones; 2 GiB is what keeps a 4 GB builder from being OOM-killed.
    local mib_per_job=2000
    local mem_avail make_jobs

    # Configure flags, and what is deliberately left at its default:
    #   --prefix=/usr/local  matches where the node: image put it, so nothing in the
    #                        agent image or a workspace has to learn a new path.
    #   NOT --ninja          GYP's ninja generator is faster, but node's Makefile does
    #                        not forward `make -j` to ninja, and ninja defaults to
    #                        nproc+2 — which would silently ignore the memory cap below
    #                        and OOM the 4 GB builder. Plain make honours -j.
    #   bundled OpenSSL      NOT --shared-openssl. Official Node builds bundle it, and
    #                        matching them keeps crypto behaviour identical to the
    #                        prebuilt mode. The agent image has libssl3 for Python.
    #   full ICU             the default, and what the node: image shipped.
    #                        --with-intl=small-icu would cut build time but silently
    #                        change `Intl` results for any tool the agent runs.
    local configure_args=(--prefix=/usr/local)

    # Fail fast rather than several *tens of minutes* in.
    mem_avail="$(mem_avail_mib)"
    if [ "${mem_avail}" -lt "${mib_per_job}" ]; then
        echo "ERROR: only ${mem_avail} MiB available; compiling Node ${NODE_VERSION} needs" >&2
        echo "       ~${mib_per_job} MiB for a single compile job (V8's largest translation" >&2
        echo "       units) and would be OOM-killed." >&2
        echo "" >&2
        echo "Fix one of:" >&2
        echo "  * give the VM more memory:  podman machine stop && podman machine set --memory 8192 && podman machine start" >&2
        echo "  * free memory in the VM:    stop containers you are not using (podman ps)" >&2
        echo "  * do not compile at all:    build with --build-arg NODE_SOURCE=prebuilt (the default)" >&2
        return 1
    fi

    # Node 26 implements the `Temporal` API in Rust. Without a toolchain, configure
    # prints
    #   WARNING: cargo not found! Support for Temporal will be disabled.
    # and carries on — producing a Node with no `Temporal` global. The node: image this
    # replaces has it (measured), so dropping it would be a silent language-feature
    # regression. The pin is in common.sh, shared with build-network.sh.
    install_rust

    mkdir -p "${BUILD_DIR}"
    cd "${BUILD_DIR}"

    fetch_verified \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}.tar.xz" \
        "${NODE_SRC_SHA256}" \
        "node-v${NODE_VERSION}.tar.xz"
    tar -xf "node-v${NODE_VERSION}.tar.xz"
    cd "node-v${NODE_VERSION}"

    # Node's configure is a Python script and GYP needs an interpreter too. This uses
    # Debian's python3, not the CPython built in the sibling stage: the stages are
    # deliberately independent, and node-gyp's generators are well tested against the
    # distro interpreter.
    run_logged "Configuring Node ${NODE_VERSION}" /tmp/node-configure.log \
        ./configure "${configure_args[@]}"

    # configure downgrades a missing toolchain to a warning and silently drops
    # features, so read its own output back rather than trusting that install_rust
    # worked.
    if grep -q 'Support for Temporal will be disabled' /tmp/node-configure.log; then
        echo "ERROR: configure disabled Temporal — the Rust toolchain was not visible to it." >&2
        grep -i 'warning' /tmp/node-configure.log >&2
        return 1
    fi

    make_jobs="${MAKE_JOBS:-$(mem_capped_jobs "${mib_per_job}")}"
    echo "Using ${make_jobs} compile job(s) (cpus=$(nproc), available memory=${mem_avail} MiB)"
    if [ "${make_jobs}" -eq 1 ]; then
        echo "NOTE: one job — expect this to take a very long time. Either give the podman"
        echo "      machine more memory, or use NODE_SOURCE=prebuilt."
    fi

    run_logged "Building Node ${NODE_VERSION}" /tmp/node-make.log \
        make -j "${make_jobs}"

    # Installed only into $PI_STAGE — nothing later in this stage needs to *run* node,
    # so there is no reason to install it into the builder's own /usr/local.
    run_logged "Staging Node ${NODE_VERSION}" /tmp/node-make-install.log \
        make install DESTDIR="${PI_STAGE}"

    # Drop the source tree (~2 GB with build artefacts) and the toolchain that was only
    # needed for Temporal.
    cd /
    rm -rf "${BUILD_DIR}" /opt/rust /root/.cargo /root/.rustup
}

# ─── Dispatch ────────────────────────────────────────────────────────────────

case "${NODE_SOURCE}" in
    prebuilt)
        echo "Staging Node ${NODE_VERSION} from the official prebuilt tarball (NODE_SOURCE=prebuilt)"
        stage_prebuilt_node
        ;;
    build)
        echo "Compiling Node ${NODE_VERSION} from source (NODE_SOURCE=build)"
        build_node_from_source
        ;;
    *)
        echo "ERROR: NODE_SOURCE must be 'prebuilt' or 'build', got '${NODE_SOURCE}'." >&2
        exit 1
        ;;
esac

# The node: image shipped this alias; some scripts and shebangs use `nodejs`. Neither
# the tarball nor `make install` creates it.
ln -sf node "${PI_STAGE}/usr/local/bin/nodejs"

# ─── Verify the STAGED tree (both modes) ─────────────────────────────────────
# Parity with what node:26.3.1-trixie-slim provided is the bar: node, npm, npx and the
# nodejs alias, with full ICU and Temporal. (That image shipped no yarn, so its absence
# here is not a regression.)
stage_bin="${PI_STAGE}/usr/local/bin"
for tool in node nodejs npm npx; do
    if [ ! -e "${stage_bin}/${tool}" ]; then
        echo "ERROR: ${tool} was not staged into ${stage_bin}." >&2
        ls -l "${stage_bin}" >&2
        exit 1
    fi
done

node_version="$("${stage_bin}/node" --version)"
echo "node ${node_version}"
if [ "${node_version}" != "v${NODE_VERSION}" ]; then
    echo "ERROR: staged node reports ${node_version}, expected v${NODE_VERSION}." >&2
    exit 1
fi

# npm is a script run by node; exercise it through the staged interpreter so a broken
# lib/node_modules layout surfaces here rather than at `npm install -g` in the agent
# image, several layers later.
npm_version="$("${stage_bin}/node" "${PI_STAGE}/usr/local/lib/node_modules/npm/bin/npm-cli.js" --version)"
echo "npm ${npm_version}"

# Intl with full ICU is the behaviour the node: image had. A --with-intl=small-icu
# build would still pass every check above and quietly differ here: it carries English
# data only, so a non-English locale falls back to en-US.
#
# The test is what small-icu *changes* — which locale fi-FI resolves to, and whether
# January comes out in English — not the exact Finnish string. An earlier version
# asserted "tammikuuta" and failed a perfectly good build: with only `month` in the
# options, ECMA-402 formats the standalone nominative "tammikuu", while "tammikuuta" is
# the partitive that appears inside a full date.
if ! "${stage_bin}/node" -e '
const dtf = new Intl.DateTimeFormat("fi-FI", {month: "long"});
const loc = dtf.resolvedOptions().locale;
const month = dtf.format(new Date(2020, 0, 1));
if (loc !== "fi-FI" || month === "January") {
  console.error(`small ICU: fi-FI resolved to ${loc}, January formatted as ${month}`);
  process.exit(1);
}
console.log(`full ICU (icu ${process.versions.icu}, fi-FI -> ${month})`);
'; then
    echo "ERROR: staged node does not have full ICU data." >&2
    exit 1
fi

# Temporal: in build mode this catches a Rust toolchain that configure did not see; in
# prebuilt mode it catches a tarball flavour that was built without it.
if ! "${stage_bin}/node" -e 'if (typeof Temporal === "undefined") { console.error("no Temporal global"); process.exit(1); } Temporal.PlainDate.from("2026-08-05"); console.log("Temporal present");'; then
    echo "ERROR: staged node has no Temporal support (the node: image it replaces does)." >&2
    exit 1
fi

echo "Shared libraries node needs at runtime:"
ldd "${stage_bin}/node"

stage_record "node" "v${NODE_VERSION} (${NODE_SOURCE})"
stage_record "npm" "${npm_version}"

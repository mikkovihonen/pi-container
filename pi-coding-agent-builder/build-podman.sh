#!/bin/bash
# Build podman from source and stage it for the agent image.
#
# WHY FROM SOURCE
# Two reasons, in order of importance:
#
#  1. BUILD TAGS. podman's Makefile derives BUILDTAGS by probing the build host
#     for installed -dev headers (hack/btrfs_installed_tag.sh, systemd_tag.sh,
#     libsubid_tag.sh, ...), which makes the feature set of the binary an accident
#     of whatever the packager had installed. Setting BUILDTAGS explicitly turns it
#     into a decision, and the right decision for a rootless podman running INSIDE
#     an unprivileged container is not the one a distro makes for a host.
#     See https://podman.io/docs/installation#build-and-run-dependencies.
#
#  2. VERSION. Debian trixie ships 5.4.2 (March 2025). Podman 6 requires exactly
#     what this image already provides and drops what it does not: cgroups v2 only
#     (we run cgroupfs on v2), nftables only (netavark shells out to nft here),
#     pasta only — slirp4netns support is gone, and CNI with it.
#
# Podman 6.0.0's release notes state it "must be used with [...] Netavark and
# Aardvark v2.0.0"; Debian has 1.14.0. That pairing is why build-network.sh exists.
#
# Run from pi-coding-agent-builder/Containerfile, as root, at image build time.
set -euo pipefail
# shellcheck source=pi-coding-agent-builder/common.sh
source "$(dirname "$0")/common.sh"

# Pins come from Containerfile as build ARGs; see require_env in common.sh. Go is a
# builder-only dependency pinned there too — nothing Go-related is staged, but podman 6
# requires Go >= 1.25 to build, which rules out Debian trixie's 1.24 and is one more
# reason the toolchain is downloaded rather than apt-installed.
require_env \
    PODMAN_VERSION PODMAN_COMMIT \
    GO_VERSION GO_SHA256_AMD64 GO_SHA256_ARM64

# ─── Build tags ──────────────────────────────────────────────────────────────
#
# ENABLED
#   seccomp                   REQUIRED. Without it podman cannot apply a seccomp
#                             profile, and every container the agent starts would
#                             run with unfiltered syscalls. The nested-container
#                             design leans on the default profile being applied.
#   libsqlite3                cgo SQLite rather than the pure-Go implementation.
#                             Podman 6 dropped BoltDB, so the database is always
#                             SQLite; libsqlite3-0 is in the agent image already.
#   containers_image_openpgp  Go-native OpenPGP instead of cgo gpgme plus the
#                             separate podman-sequoia shared library. Drops two
#                             runtime dependencies for signature policy this image
#                             does not use (policy.json is insecureAcceptAnything).
#   exclude_graphdriver_btrfs No btrfs storage driver, so no libbtrfs. Nested
#                             storage is overlay, or vfs when overlay-on-overlay
#                             is not supported — never btrfs.
#   grpcnotrace               Upstream default. Drops gRPC's tracing tables.
#
# DELIBERATELY OMITTED
#   systemd    Would enable the journald log driver AND make it the default. There
#              is no journal in the agent container, so that default would point
#              every container's logs at a socket that does not exist. Without the
#              tag the default is k8s-file and `podman logs` works as expected.
#   libsubid   Resolves subuid/subgid through shadow's libsubid instead of reading
#              /etc/subuid directly. The agent image writes /etc/subuid itself, and
#              direct parsing is the path verified to work for nested user
#              namespaces (see docs/design/nested-containers.md).
#   apparmor   Loading an AppArmor profile from inside an unprivileged container
#              cannot succeed. Without the tag podman skips it rather than trying.
BUILDTAGS="seccomp libsqlite3 containers_image_openpgp exclude_graphdriver_btrfs grpcnotrace"

# ─── Go toolchain ────────────────────────────────────────────────────────────

case "$(deb_arch)" in
    amd64) go_sha="${GO_SHA256_AMD64}" ;;
    arm64) go_sha="${GO_SHA256_ARM64}" ;;
    *)
        echo "ERROR: no pinned Go tarball for '$(deb_arch)'" >&2
        exit 1
        ;;
esac

go_tarball="go${GO_VERSION}.linux-$(deb_arch).tar.gz"
fetch_verified "https://go.dev/dl/${go_tarball}" "${go_sha}" "/tmp/${go_tarball}"
tar -C /opt -xzf "/tmp/${go_tarball}"
rm -f "/tmp/${go_tarball}"

export PATH="/opt/go/bin:${PATH}"
# Podman vendors its dependencies in-tree, so the build needs no module downloads;
# -mod=vendor makes that explicit and fails loudly if something is missing rather
# than silently reaching out to a proxy. GOTOOLCHAIN=local likewise refuses to
# fetch a different Go than the one pinned above.
export GOFLAGS="-mod=vendor"
export GOTOOLCHAIN="local"
go version

# ─── Build ───────────────────────────────────────────────────────────────────

clone_verified https://github.com/containers/podman.git \
    "${PODMAN_VERSION}" "${PODMAN_COMMIT}" /usr/src/podman
cd /usr/src/podman

# Only these two binaries are built. Not podman-remote (the agent talks to its own
# local podman), not quadlet (a systemd generator, and there is no systemd here),
# and not the man pages (they need go-md2man; `podman --help` is unaffected).
#
# rootlessport is not optional: it is how rootless podman publishes a container
# port, which is what `docker compose` with a `ports:` mapping does. Podman finds
# it through helper_binaries_dir, whose default list starts with
# /usr/local/libexec/podman — hence the install path below.
#
# -p caps how many packages the Go compiler builds concurrently. Podman is a large
# module and gc's peak RSS per package is a few hundred MiB, which is enough to
# OOM a 2 GiB builder that has 10 cores.
go_jobs="$(mem_capped_jobs 400)"
echo "Using ${go_jobs} compile job(s) (cpus=$(nproc), available memory=$(mem_avail_mib) MiB)"

run_logged "Building podman ${PODMAN_VERSION}" /tmp/podman-build.log \
    make BUILDTAGS="${BUILDTAGS}" GOFLAGS="${GOFLAGS} -p=${go_jobs}" bin/podman bin/rootlessport

install -D -m 0755 bin/podman "${PI_STAGE}/usr/local/bin/podman"
install -D -m 0755 bin/rootlessport "${PI_STAGE}/usr/local/libexec/podman/rootlessport"

# ─── Verify ──────────────────────────────────────────────────────────────────

./bin/podman --version

# The tags above only matter if they actually reached the compiler. `go version -m`
# reads them back out of the binary, which catches a typo'd tag name (Go ignores
# unknown tags silently) and a Makefile that overrode BUILDTAGS.
echo "Build settings recorded in the binary:"
go version -m bin/podman | grep -E '^\s+build\s+(-tags|-compiler|CGO_ENABLED)' || true

if ! go version -m bin/podman | grep -qE '^\s+build\s+-tags=.*\bseccomp\b'; then
    echo "ERROR: podman was built WITHOUT the seccomp build tag." >&2
    echo "       Containers the agent starts would run with no seccomp profile." >&2
    exit 1
fi

# containers_image_openpgp replaces cgo gpgme. If gpgme is still linked in, the tag
# did not take effect and the agent image would need libgpgme11 to run podman at all.
echo "Shared libraries podman needs at runtime:"
ldd bin/podman
if ldd bin/podman | grep -q gpgme; then
    echo "ERROR: podman is linked against libgpgme despite containers_image_openpgp." >&2
    exit 1
fi

stage_record "podman" "${PODMAN_VERSION}"
stage_record "podman-tags" "${BUILDTAGS}"

# The builder image is not shipped, but it is kept on the host between builds:
# Go plus the source tree plus the build cache is ~2 GB of nothing useful.
cd /
rm -rf /usr/src/podman /opt/go /root/.cache/go-build /root/go

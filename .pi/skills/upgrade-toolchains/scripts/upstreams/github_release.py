import json
import re
import sys
import urllib.request

sys.dont_write_bytecode = True

from upstreams.base import ToolReleaseInfo
from upstreams.github_git import get_git_tags_and_commits


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "pi-container-upgrade-tools"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "pi-container-upgrade-tools"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def resolve_netavark() -> ToolReleaseInfo:
    release_data = _fetch_json("https://api.github.com/repos/containers/netavark/releases/latest")
    tag = release_data["tag_name"]
    tags = get_git_tags_and_commits("https://github.com/containers/netavark.git")
    commit = tags.get(tag, "")

    vendor_sha = ""
    for asset in release_data.get("assets", []):
        if asset["name"] == "sha256sum":
            txt = _fetch_text(asset["browser_download_url"])
            for line in txt.splitlines():
                if f"netavark-{tag}-vendor.tar.gz" in line:
                    vendor_sha = line.split()[0]
                    break

    return ToolReleaseInfo(
        tool="netavark",
        version=tag,
        args={
            "NETAVARK_VERSION": tag,
            "NETAVARK_COMMIT": commit,
            "NETAVARK_VENDOR_SHA256": vendor_sha,
        },
        category="core",
        release_url=f"https://github.com/containers/netavark/releases/tag/{tag}",
    )


def resolve_aardvark_dns() -> ToolReleaseInfo:
    release_data = _fetch_json("https://api.github.com/repos/containers/aardvark-dns/releases/latest")
    tag = release_data["tag_name"]
    tags = get_git_tags_and_commits("https://github.com/containers/aardvark-dns.git")
    commit = tags.get(tag, "")

    vendor_sha = ""
    for asset in release_data.get("assets", []):
        if asset["name"] == "sha256sum":
            txt = _fetch_text(asset["browser_download_url"])
            for line in txt.splitlines():
                if f"aardvark-dns-{tag}-vendor.tar.gz" in line:
                    vendor_sha = line.split()[0]
                    break

    return ToolReleaseInfo(
        tool="aardvark-dns",
        version=tag,
        args={
            "AARDVARK_VERSION": tag,
            "AARDVARK_COMMIT": commit,
            "AARDVARK_VENDOR_SHA256": vendor_sha,
        },
        category="core",
        release_url=f"https://github.com/containers/aardvark-dns/releases/tag/{tag}",
    )


def resolve_zls() -> ToolReleaseInfo:
    # Get latest zls tag from git
    zls_tags = get_git_tags_and_commits("https://github.com/zigtools/zls.git")
    candidates = [t for t in zls_tags if re.match(r"^\d+\.\d+\.\d+$", t)]
    candidates.sort(key=lambda x: [int(p) for p in x.split(".")])
    zls_tag = candidates[-1]
    zls_commit = zls_tags[zls_tag]

    # Resolve matching Zig compiler release
    zig_data = _fetch_json("https://ziglang.org/download/index.json")
    zig_ver = zls_tag
    if zig_ver not in zig_data:
        # Fall back to latest stable release
        stables = [k for k in zig_data if k != "master"]
        stables.sort(key=lambda x: [int(p) for p in x.split(".")])
        zig_ver = stables[-1]

    zig_x86_sha = zig_data[zig_ver]["x86_64-linux"]["shasum"]
    zig_arm_sha = zig_data[zig_ver]["aarch64-linux"]["shasum"]

    return ToolReleaseInfo(
        tool="zls",
        version=zls_tag,
        args={
            "ZLS_VERSION": zls_tag,
            "ZLS_COMMIT": zls_commit,
            "ZIG_VERSION": zig_ver,
            "ZIG_SHA256_AMD64": zig_x86_sha,
            "ZIG_SHA256_ARM64": zig_arm_sha,
        },
        category="lsp",
        release_url=f"https://github.com/zigtools/zls/releases/tag/{zls_tag}",
    )


def resolve_t3() -> ToolReleaseInfo:
    release_data = _fetch_json("https://api.github.com/repos/pingdotgg/t3code/releases/latest")
    tag = release_data["tag_name"]
    version = tag.removeprefix("v")

    sha_x64 = ""
    sha_arm64 = ""
    for asset in release_data.get("assets", []):
        if asset["name"] == "SHA256SUMS":
            txt = _fetch_text(asset["browser_download_url"])
            for line in txt.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    sha, filename = parts[0], parts[1].lstrip("*")
                    if filename == f"t3-{version}-linux-x64.tar.gz":
                        sha_x64 = sha
                    elif filename == f"t3-{version}-linux-arm64.tar.gz":
                        sha_arm64 = sha

    return ToolReleaseInfo(
        tool="t3",
        version=version,
        args={
            "T3_VERSION": version,
            "T3_SHA256_AMD64": sha_x64,
            "T3_SHA256_ARM64": sha_arm64,
        },
        category="tools",
        release_url=f"https://github.com/pingdotgg/t3code/releases/tag/{tag}",
    )


def resolve_gh() -> ToolReleaseInfo:
    release_data = _fetch_json("https://api.github.com/repos/cli/cli/releases/latest")
    tag = release_data["tag_name"]
    version = tag.removeprefix("v")

    sha_amd64 = ""
    sha_arm64 = ""
    for asset in release_data.get("assets", []):
        if asset["name"] == f"gh_{version}_checksums.txt":
            txt = _fetch_text(asset["browser_download_url"])
            for line in txt.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    sha, filename = parts[0], parts[1].lstrip("*")
                    if filename == f"gh_{version}_linux_amd64.tar.gz":
                        sha_amd64 = sha
                    elif filename == f"gh_{version}_linux_arm64.tar.gz":
                        sha_arm64 = sha

    return ToolReleaseInfo(
        tool="gh",
        version=version,
        args={
            "GH_VERSION": version,
            "GH_SHA256_AMD64": sha_amd64,
            "GH_SHA256_ARM64": sha_arm64,
        },
        category="vcs",
        release_url=f"https://github.com/cli/cli/releases/tag/{tag}",
    )


def resolve_glab() -> ToolReleaseInfo:
    release_data = _fetch_json("https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/permalink/latest")
    tag = release_data["tag_name"]
    version = tag.removeprefix("v")

    sha_amd64 = ""
    sha_arm64 = ""
    for link in release_data.get("assets", {}).get("links", []):
        if link.get("name") == "checksums.txt":
            txt = _fetch_text(link["url"])
            for line in txt.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    sha, filename = parts[0], parts[1].lstrip("*")
                    if filename == f"glab_{version}_linux_amd64.tar.gz":
                        sha_amd64 = sha
                    elif filename == f"glab_{version}_linux_arm64.tar.gz":
                        sha_arm64 = sha

    return ToolReleaseInfo(
        tool="glab",
        version=version,
        args={
            "GLAB_VERSION": version,
            "GLAB_SHA256_AMD64": sha_amd64,
            "GLAB_SHA256_ARM64": sha_arm64,
        },
        category="vcs",
        release_url=f"https://gitlab.com/gitlab-org/cli/-/releases/{tag}",
    )


def resolve_tea() -> ToolReleaseInfo:
    release_data = _fetch_json("https://gitea.com/api/v1/repos/gitea/tea/releases/latest")
    tag = release_data["tag_name"]
    version = tag.removeprefix("v")

    sha_amd64 = ""
    sha_arm64 = ""
    for asset in release_data.get("assets", []):
        if asset["name"] == "checksums.txt":
            txt = _fetch_text(asset["browser_download_url"])
            for line in txt.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    sha, filename = parts[0], parts[1].lstrip("*")
                    if filename == f"tea-{version}-linux-amd64":
                        sha_amd64 = sha
                    elif filename == f"tea-{version}-linux-arm64":
                        sha_arm64 = sha

    return ToolReleaseInfo(
        tool="tea",
        version=version,
        args={
            "TEA_VERSION": version,
            "TEA_SHA256_AMD64": sha_amd64,
            "TEA_SHA256_ARM64": sha_arm64,
        },
        category="vcs",
        release_url=f"https://gitea.com/gitea/tea/releases/tag/{tag}",
    )


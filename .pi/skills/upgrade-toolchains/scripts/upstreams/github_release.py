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

import json
import re
import sys
import tomllib
import urllib.request

sys.dont_write_bytecode = True

from upstreams.base import ToolReleaseInfo, parse_version_tuple


def _fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pi-container-upgrade-tools"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def resolve_go() -> ToolReleaseInfo:
    data = json.loads(_fetch_url("https://go.dev/dl/?mode=json").decode("utf-8"))
    latest_stable = data[0]
    ver = latest_stable["version"].removeprefix("go")

    sha_amd64 = ""
    sha_arm64 = ""
    for f in latest_stable.get("files", []):
        if f.get("os") == "linux" and f.get("kind") == "archive":
            if f.get("arch") == "amd64":
                sha_amd64 = f.get("sha256", "")
            elif f.get("arch") == "arm64":
                sha_arm64 = f.get("sha256", "")

    return ToolReleaseInfo(
        tool="go",
        version=ver,
        args={
            "GO_VERSION": ver,
            "GO_SHA256_AMD64": sha_amd64,
            "GO_SHA256_ARM64": sha_arm64,
        },
        category="core",
        release_url=f"https://go.dev/doc/devel/release#go{ver}",
    )


def resolve_rust() -> ToolReleaseInfo:
    raw = _fetch_url("https://static.rust-lang.org/dist/channel-rust-stable.toml").decode("utf-8")
    data = tomllib.loads(raw)
    rust_pkg = data["pkg"]["rust"]
    ver = rust_pkg["version"].split()[0]
    dist_date = data["date"]
    sha_x86_64 = rust_pkg["target"]["x86_64-unknown-linux-gnu"]["hash"]
    sha_aarch64 = rust_pkg["target"]["aarch64-unknown-linux-gnu"]["hash"]

    return ToolReleaseInfo(
        tool="rust",
        version=ver,
        args={
            "RUST_VERSION": ver,
            "RUST_DIST_DATE": dist_date,
            "RUST_SHA256_X86_64": sha_x86_64,
            "RUST_SHA256_AARCH64": sha_aarch64,
        },
        category="core",
        release_url=f"https://github.com/rust-lang/rust/releases/tag/{ver}",
    )


def resolve_node() -> ToolReleaseInfo:
    data = json.loads(_fetch_url("https://nodejs.org/dist/index.json").decode("utf-8"))
    # Filter for stable releases (ignore alpha/beta/rc)
    stable_releases = [d for d in data if re.match(r"^v\d+\.\d+\.\d+$", d.get("version", ""))]
    if not stable_releases:
        raise ValueError("No stable Node.js releases found")
    release = stable_releases[0]
    ver_tag = release["version"]  # e.g. "v26.13.0"
    ver = ver_tag.removeprefix("v")

    # Fetch SHASUMS256.txt for this release
    sums_txt = _fetch_url(f"https://nodejs.org/dist/{ver_tag}/SHASUMS256.txt").decode("utf-8")
    src_sha = ""
    x64_sha = ""
    arm64_sha = ""

    src_name = f"node-{ver_tag}.tar.gz"
    x64_name = f"node-{ver_tag}-linux-x64.tar.xz"
    arm64_name = f"node-{ver_tag}-linux-arm64.tar.xz"

    for line in sums_txt.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sha, filename = parts[0], parts[1]
            if filename == src_name:
                src_sha = sha
            elif filename == x64_name:
                x64_sha = sha
            elif filename == arm64_name:
                arm64_sha = sha

    return ToolReleaseInfo(
        tool="node",
        version=ver,
        args={
            "NODE_VERSION": ver,
            "NODE_SRC_SHA256": src_sha,
            "NODE_BIN_SHA256_X64": x64_sha,
            "NODE_BIN_SHA256_ARM64": arm64_sha,
        },
        category="core",
        release_url=f"https://nodejs.org/en/blog/release/{ver_tag}",
    )


def resolve_git() -> ToolReleaseInfo:
    txt = _fetch_url("https://mirrors.edge.kernel.org/pub/software/scm/git/sha256sums.asc").decode("utf-8")
    matches = re.findall(r"([0-9a-f]{64})\s+git-(\d+\.\d+\.\d+)\.tar\.xz", txt)
    if not matches:
        raise ValueError("Could not parse git versions from kernel.org")

    latest_sha, latest_ver = sorted(matches, key=lambda x: parse_version_tuple(x[1]))[-1]

    return ToolReleaseInfo(
        tool="git",
        version=latest_ver,
        args={
            "GIT_VERSION": latest_ver,
            "GIT_SHA256": latest_sha,
        },
        category="vcs",
        release_url=f"https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/{latest_ver}.txt",
    )


def resolve_ruby() -> ToolReleaseInfo:
    html = _fetch_url("https://www.ruby-lang.org/en/downloads/releases/").decode("utf-8")
    links = re.findall(r'href="(/en/news/[^"]+released/)"', html)
    if not links:
        raise ValueError("Could not find Ruby release announcements")

    # Find highest stable Ruby 3.x release
    candidates: list[tuple[str, str]] = []
    for link in links:
        m = re.search(r"ruby-(3(?:-\d+)+)-released", link)
        if m:
            ver_dots = m.group(1).replace("-", ".")
            candidates.append((ver_dots, link))

    if not candidates:
        raise ValueError("Could not find Ruby 3.x release announcements")

    candidates.sort(key=lambda x: parse_version_tuple(x[0]))
    ruby_ver, news_rel_link = candidates[-1]
    news_url = "https://www.ruby-lang.org" + news_rel_link
    news_txt = _fetch_url(news_url).decode("utf-8")

    m_sha = re.search(r"ruby-.*?tar\.xz.*?sha256:\s*([0-9a-f]{64})", news_txt, re.I | re.S)
    if not m_sha:
        raise ValueError(f"Could not resolve SHA256 for Ruby {ruby_ver} in {news_url}")

    ruby_sha = m_sha.group(1)

    return ToolReleaseInfo(
        tool="ruby",
        version=ruby_ver,
        args={
            "RUBY_VERSION": ruby_ver,
            "RUBY_SHA256": ruby_sha,
        },
        category="vcs",
        release_url=news_url,
    )

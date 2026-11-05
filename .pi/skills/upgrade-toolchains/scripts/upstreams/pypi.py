import json
import sys
import urllib.request

sys.dont_write_bytecode = True

from upstreams.base import ToolReleaseInfo


def _fetch_pypi_package(pkg_name: str) -> dict:
    req = urllib.request.Request(
        f"https://pypi.org/pypi/{pkg_name}/json",
        headers={"User-Agent": "pi-container-upgrade-tools"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def resolve_uv() -> ToolReleaseInfo:
    data = _fetch_pypi_package("uv")
    ver = data["info"]["version"]
    releases = data.get("releases", {}).get(ver, [])

    # Collect SHA256 hashes for all Linux wheels
    hashes: list[str] = []
    for rel in releases:
        fn = rel.get("filename", "")
        if "linux" in fn and fn.endswith(".whl"):
            sha = rel.get("digests", {}).get("sha256")
            if sha and sha not in hashes:
                hashes.append(sha)

    # Format into multiline string like Containerfile
    sha_str = "\\\n    " + " \\\n    ".join(hashes)
    return ToolReleaseInfo(
        tool="uv",
        version=ver,
        args={"UV_VERSION": ver, "UV_SHA256": f'"{sha_str}"'},
        category="core",
        release_url=f"https://github.com/astral-sh/uv/releases/tag/{ver}",
    )


def resolve_podman_compose() -> ToolReleaseInfo:
    data = _fetch_pypi_package("podman-compose")
    ver = data["info"]["version"]
    releases = data.get("releases", {}).get(ver, [])

    hashes: list[str] = []
    for rel in releases:
        sha = rel.get("digests", {}).get("sha256")
        if sha and sha not in hashes:
            hashes.append(sha)

    sha_str = "\\\n    " + " \\\n    ".join(hashes)
    return ToolReleaseInfo(
        tool="podman-compose",
        version=ver,
        args={"PODMAN_COMPOSE_VERSION": ver, "PODMAN_COMPOSE_SHA256": f'"{sha_str}"'},
        category="core",
        release_url=f"https://github.com/containers/podman-compose/releases/tag/v{ver}",
    )


def resolve_python_dotenv() -> ToolReleaseInfo:
    data = _fetch_pypi_package("python-dotenv")
    ver = data["info"]["version"]
    releases = data.get("releases", {}).get(ver, [])

    hashes: list[str] = []
    for rel in releases:
        sha = rel.get("digests", {}).get("sha256")
        if sha and sha not in hashes:
            hashes.append(sha)

    sha_str = "\\\n    " + " \\\n    ".join(hashes)
    return ToolReleaseInfo(
        tool="python-dotenv",
        version=ver,
        args={"PYTHON_DOTENV_VERSION": ver, "PYTHON_DOTENV_SHA256": f'"{sha_str}"'},
        category="core",
        release_url=f"https://github.com/theskumar/python-dotenv/releases/tag/v{ver}",
    )


def resolve_pyyaml() -> ToolReleaseInfo:
    data = _fetch_pypi_package("PyYAML")
    ver = data["info"]["version"]
    releases = data.get("releases", {}).get(ver, [])

    hashes: list[str] = []
    for rel in releases:
        sha = rel.get("digests", {}).get("sha256")
        if sha and sha not in hashes:
            hashes.append(sha)

    sha_str = "\\\n    " + " \\\n    ".join(hashes)
    return ToolReleaseInfo(
        tool="pyyaml",
        version=ver,
        args={"PYYAML_VERSION": ver, "PYYAML_SHA256": f'"{sha_str}"'},
        category="core",
        release_url=f"https://github.com/yaml/pyyaml/releases/tag/{ver}",
    )


def resolve_az() -> ToolReleaseInfo:
    data = _fetch_pypi_package("azure-cli")
    ver = data["info"]["version"]

    # Fetch latest azure-devops extension release metadata
    req = urllib.request.Request(
        "https://raw.githubusercontent.com/Azure/azure-cli-extensions/main/src/index.json",
        headers={"User-Agent": "pi-container-upgrade-tools"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        ext_index = json.loads(resp.read().decode())
    latest_devops = ext_index["extensions"]["azure-devops"][-1]
    devops_ver = latest_devops["metadata"]["version"]
    devops_sha = latest_devops["sha256Digest"]

    return ToolReleaseInfo(
        tool="az",
        version=ver,
        args={
            "AZ_VERSION": ver,
            "AZURE_DEVOPS_VERSION": devops_ver,
            "AZURE_DEVOPS_SHA256": devops_sha,
        },
        category="vcs",
        release_url=f"https://github.com/Azure/azure-cli/releases/tag/azure-cli-{ver}",
    )


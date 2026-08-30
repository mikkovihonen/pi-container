import json
import logging
import re
import socket
import subprocess
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

from models import ServerConfig
from network import ContainerNetworkManager
from server import Server
from util import is_pid_alive, run_quiet

logger = logging.getLogger(__name__)

# Registry hostname fragment → hosts where layer blobs are 307-redirected.
_REGISTRY_BLOB_HOSTS: dict[str, tuple[tuple[str, str], ...]] = {
    "docker.io": (("production.cloudfront.docker.com", "*.cloudfront.docker.com"),),
    "ghcr.io": (("pkg-containers.githubusercontent.com", "pkg-containers.githubusercontent.com"),),
    "quay.io": (("cdn01.quay.io", "*.quay.io"),),
    "gcr.io": (),
    "registry.k8s.io": (("europe-north1-docker.pkg.dev", "*.pkg.dev"),),
    "public.ecr.aws": (("d2glxqk2uabbnd.cloudfront.net", "*.cloudfront.net"),),
    "mcr.microsoft.com": (("westeurope.data.mcr.microsoft.com", "*.data.mcr.microsoft.com"),),
}

_REGEX_METACHARS = r"^$+{}[]()|"


def cleanup_orphaned_agent_containers(runtime: str, project_key: str) -> list[str]:
    """Discover and remove dead or exited agent containers associated with this project."""
    removed: list[str] = []
    try:
        result = subprocess.run(
            [runtime, "ps", "--all", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        containers = json.loads(result.stdout or "[]")
    except Exception as e:
        logger.warning(f"Could not list containers for agent orphan cleanup: {e}")
        return removed

    for container in containers:
        if not isinstance(container, dict):
            continue
        raw_names = container.get("Names") or []
        if isinstance(raw_names, str):
            name = raw_names
        elif raw_names:
            name = raw_names[0]
        else:
            name = str(container.get("Id") or "")[:12]
        name = str(name).lstrip("/")
        if not name or not name.startswith("pi-coding-agent-"):
            continue

        image = str(container.get("Image") or "")
        labels = container.get("Labels") or {}
        project_label = labels.get("pi-container.project.hash")
        is_my_project = (project_label == project_key) or (project_key in image)
        if not is_my_project:
            continue

        launcher_pid_str = labels.get("pi-container.launcher_pid")
        is_orphan = False
        if launcher_pid_str:
            try:
                launcher_pid = int(launcher_pid_str)
                if not is_pid_alive(launcher_pid):
                    is_orphan = True
            except ValueError:
                is_orphan = True
        else:
            status = str(container.get("Status") or "").lower()
            if "exited" in status or "created" in status:
                is_orphan = True

        if is_orphan:
            logger.info(f"Removing orphaned agent container from previous crashed run: {name}")
            res = run_quiet(
                [runtime, "rm", "-f", name], check=False, label=f"remove orphaned agent {name}", logger=logger
            )
            if res.returncode == 0:
                removed.append(name)

    return removed


def sweep_orphaned_servers(lock_dir: Path) -> list[str]:
    """Stop llama-server processes whose client sessions have all died."""
    return Server.cleanup_orphaned_servers(lock_dir)


def sweep_orphaned_proxies(runtime: str, lock_dir: Path) -> list[str]:
    """Stop proxy containers and remove isolated networks whose client sessions have all died."""
    return ContainerNetworkManager.cleanup_orphaned_proxies(runtime, lock_dir)


def hostname_allowed(host: str, patterns: list[str]) -> bool:
    """Check if host matches any glob or regex allowlist pattern."""
    for pattern in patterns:
        if any(c in pattern for c in _REGEX_METACHARS):
            try:
                if re.search(pattern, host, re.IGNORECASE):
                    return True
            except re.error:
                continue
        elif re.match("^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$", host, re.IGNORECASE):
            return True
    return False


def warn_about_registry_allowlist(config_dir: Path) -> None:
    """Warn when nested containers are enabled but registry blob domains are missing from allowlist."""
    allowlist_path = config_dir / "allowlist.yaml"
    try:
        from yaml_strict import load_yaml_file

        data = load_yaml_file(allowlist_path) or {}
    except Exception:
        return

    patterns = [
        str(host)
        for rule in (data.get("global") or {}).get("rules", []) or []
        if str((rule or {}).get("mode", "allow")) == "allow"
        for host in (rule or {}).get("hostnames", []) or []
    ]

    allowed = [registry for registry in _REGISTRY_BLOB_HOSTS if any(registry in pattern for pattern in patterns)]
    if not allowed:
        logger.warning(
            f"nested_containers.enabled is true but no container registry hostname is allowed in "
            f"{allowlist_path}; image pulls from nested containers will fail with mitmproxy's 403. "
            f"Uncomment the 'container-registries-allow' rule (or add the registries you use)."
        )
        return

    for registry in allowed:
        missing = [
            suggestion
            for sample, suggestion in _REGISTRY_BLOB_HOSTS[registry]
            if not hostname_allowed(sample, patterns)
        ]
        if missing:
            logger.warning(
                f"{registry} is allowed in {allowlist_path}, but the host its layer blobs redirect to "
                f"is not ({', '.join(missing)}); pulls will resolve the manifest and then fail on the "
                f"first layer with mitmproxy's 403. Add those hostnames to the rule."
            )


def unavailable_host_ports(publish: list[tuple[int, int]], expose: str) -> list[int]:
    """Check which requested host ports are currently bound by attempting local socket binds."""
    host = "127.0.0.1" if expose != "lan" else "0.0.0.0"  # noqa: S104
    taken: list[int] = []
    for host_port, _ in publish:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, host_port))
            except OSError:
                taken.append(host_port)
    return taken


def port_holders(runtime: str, ports: list[int], project_key: str = "") -> dict[int, str]:
    """Inspect running containers to identify which container holds each published port."""
    if not ports:
        return {}
    try:
        result = subprocess.run(
            [runtime, "ps", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        containers = json.loads(result.stdout or "[]")
    except OSError, subprocess.SubprocessError, json.JSONDecodeError:
        return {}

    wanted = set(ports)
    holders: dict[int, str] = {}
    for container in containers:
        if not isinstance(container, dict):
            continue
        names = container.get("Names") or []
        name = names[0] if names else container.get("Id", "")[:12]
        image = str(container.get("Image") or "")
        status = str(container.get("Status") or "").strip()
        for mapping in container.get("Ports") or []:
            start = mapping.get("host_port")
            if not isinstance(start, int):
                continue
            span = mapping.get("range") if isinstance(mapping.get("range"), int) else 1
            for port in range(start, start + max(span, 1)):
                if port in wanted and port not in holders:
                    mine = " — this workspace" if project_key and project_key in image else ""
                    holders[port] = f"{name}{mine}" + (f" ({status})" if status else "")
    return holders


def extract_server_configs(data: dict) -> tuple[list[dict], set[str]]:
    """Extract ServerConfig mappings and custom hostnames from models.json provider definitions."""
    server_configs = []
    llama_hostnames: set[str] = {"llama"}
    for name, val in data.get("providers", {}).items():
        if isinstance(val, dict) and "serverCustomParameters" in val:
            server_config = ServerConfig.from_dict(val["serverCustomParameters"])
            base_url = val.get("baseUrl")
            server_configs.append({"name": name, "config": server_config, "baseUrl": base_url})
            llama_hostnames.add(name)
            if base_url:
                parsed_host = urlparse(base_url).hostname
                if parsed_host and parsed_host not in ("localhost", "127.0.0.1", "::1"):
                    llama_hostnames.add(parsed_host)
    return server_configs, llama_hostnames

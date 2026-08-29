"""Security policy definitions and preflight validations for pi-container."""

import ipaddress
import logging
from pathlib import Path
from typing import Any

from network import _egress_truthy, load_project_config

logger = logging.getLogger(__name__)

DEFAULT_SECURITY_CONFIG: dict[str, Any] = {
    "read_only_git_hooks": True,
    "blocked_mount_paths": [
        "~/.ssh",
        "~/.gnupg",
        "~/.aws",
        "~/.azure",
        "~/.config/gcloud",
        "~/.kube",
        "~/.docker",
        "/var/run/docker.sock",
        "/var/run/podman/podman.sock",
        "/etc/shadow",
        "/etc/passwd",
        "/etc/sudoers",
        "/root",
    ],
    "blocked_ip_ranges": [
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    ],
    "git_config_allowlist": [
        "user.name",
        "user.email",
        "init.defaultBranch",
        "pull.rebase",
        "core.autocrlf",
        "core.filemode",
        "core.eol",
        "commit.gpgSign",
        "user.signingKey",
        "gpg.format",
        "gpg.ssh.program",
        "tag.gpgSign",
    ],
}


def read_security_config(config_dir: Path | None = None) -> dict[str, Any]:
    """Read the ``security`` section from config.yaml with fail-safe defaults."""
    cfg = load_project_config(config_dir)
    section = cfg.get("security") or {}

    read_only_hooks = section.get("read_only_git_hooks")
    if read_only_hooks is None:
        read_only_hooks_val = DEFAULT_SECURITY_CONFIG["read_only_git_hooks"]
    else:
        read_only_hooks_val = _egress_truthy(read_only_hooks)

    blocked_mounts = section.get("blocked_mount_paths")
    if not isinstance(blocked_mounts, list):
        blocked_mounts = DEFAULT_SECURITY_CONFIG["blocked_mount_paths"]

    blocked_ips = section.get("blocked_ip_ranges")
    if not isinstance(blocked_ips, list):
        blocked_ips = DEFAULT_SECURITY_CONFIG["blocked_ip_ranges"]

    git_allowlist = section.get("git_config_allowlist")
    if not isinstance(git_allowlist, list):
        git_allowlist = DEFAULT_SECURITY_CONFIG["git_config_allowlist"]

    return {
        "read_only_git_hooks": read_only_hooks_val,
        "blocked_mount_paths": [str(p) for p in blocked_mounts],
        "blocked_ip_ranges": [str(ip) for ip in blocked_ips],
        "git_config_allowlist": [str(k) for k in git_allowlist],
    }


def validate_mount_paths(mounts: list[str], blocked_paths: list[str]) -> list[str]:
    """Validate that extra agent bind mounts do not expose dangerous host directories.

    Args:
        mounts: List of mount specifications (e.g. "host_path:container_path[:mode]").
        blocked_paths: List of forbidden host path patterns (e.g. "~/.ssh", "/var/run/docker.sock").

    Returns:
        List of error messages describing any forbidden mount violations.
    """
    errors: list[str] = []
    resolved_blocked: list[tuple[str, Path]] = []
    for pattern in blocked_paths:
        try:
            expanded = Path(pattern).expanduser().resolve()
            resolved_blocked.append((pattern, expanded))
        except Exception:
            continue

    for mount in mounts:
        host_str = mount.split(":")[0].strip() if ":" in mount else mount.strip()
        if not host_str:
            continue
        try:
            host_path = Path(host_str).expanduser().resolve()
        except Exception:
            continue

        for pattern, blocked in resolved_blocked:
            try:
                # Check if exact match or if host_path is a subdirectory/file inside blocked
                if host_path == blocked or host_path.is_relative_to(blocked):
                    errors.append(
                        f"Mount '{mount}' is blocked: host path '{host_str}' exposes sensitive path '{pattern}'."
                    )
                    break
            except Exception:
                continue

    return errors


def is_ip_in_blocked_ranges(ip_str: str, blocked_ranges: list[str]) -> bool:
    """Check if an IP address string belongs to any of the blocked CIDR ranges.

    Args:
        ip_str: IPv4 or IPv6 address string.
        blocked_ranges: List of CIDR strings (e.g. "10.0.0.0/8", "169.254.0.0/16").

    Returns:
        True if the IP falls within any blocked network range.
    """
    try:
        ip = ipaddress.ip_address(ip_str.strip("[]"))
    except ValueError:
        return False

    for cidr in blocked_ranges:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            if ip in net:
                return True
        except ValueError:
            continue

    return False

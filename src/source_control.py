from __future__ import annotations

import sys

sys.dont_write_bytecode = True

"""Source control authentication and token lifecycle management for pi-container.

Resolves authentication credentials for source control providers (GitHub, GitLab,
Forgejo/Gitea, Azure DevOps, Bitbucket, GitCafe) exclusively from the host OS Keyring
(or active host CLI session if auto_detect_host_cli is explicitly enabled).

Neither .env files nor host environment variables are accepted as credential stores.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from network import _egress_truthy, load_project_config
from secret_store import DEFAULT_KEYRING_SERVICE, get_keyring_secret

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE_CONTROL = {
    "enabled": False,
    "auto_detect_host_cli": False,
    "setup_git_credentials": True,
}


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    canonical_env_var: str
    default_keyring_vars: tuple[str, ...]
    secondary_export_vars: tuple[str, ...]
    host_cli_cmd: tuple[str, ...] | None


PROVIDER_DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        name="github",
        canonical_env_var="GH_TOKEN",
        default_keyring_vars=("GH_TOKEN", "GITHUB_TOKEN"),
        secondary_export_vars=("GITHUB_TOKEN",),
        host_cli_cmd=("gh", "auth", "token"),
    ),
    ProviderDefinition(
        name="gitlab",
        canonical_env_var="GITLAB_TOKEN",
        default_keyring_vars=("GITLAB_TOKEN", "GLAB_TOKEN"),
        secondary_export_vars=("GLAB_TOKEN",),
        host_cli_cmd=None,
    ),
    ProviderDefinition(
        name="forgejo",
        canonical_env_var="GITEA_TOKEN",
        default_keyring_vars=("GITEA_TOKEN", "TEA_TOKEN", "FORGEJO_TOKEN"),
        secondary_export_vars=("TEA_TOKEN",),
        host_cli_cmd=None,
    ),
    ProviderDefinition(
        name="azure_devops",
        canonical_env_var="AZURE_DEVOPS_EXT_PAT",
        default_keyring_vars=("AZURE_DEVOPS_EXT_PAT", "AZURE_DEVOPS_TOKEN"),
        secondary_export_vars=("AZURE_DEVOPS_TOKEN",),
        host_cli_cmd=(
            "az",
            "account",
            "get-access-token",
            "--resource",
            "499b84de-3277-4e70-9c43-e57d724c06e7",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ),
    ),
    ProviderDefinition(
        name="bitbucket",
        canonical_env_var="BITBUCKET_TOKEN",
        default_keyring_vars=("BITBUCKET_TOKEN", "BITBUCKET_APP_PASSWORD"),
        secondary_export_vars=(),
        host_cli_cmd=None,
    ),
    ProviderDefinition(
        name="gitcafe",
        canonical_env_var="GITCAFE_TOKEN",
        default_keyring_vars=("GITCAFE_TOKEN", "CAFE_TOKEN"),
        secondary_export_vars=("CAFE_TOKEN",),
        host_cli_cmd=None,
    ),
)


def mask_token(token: str) -> str:
    """Mask a sensitive token string for safe logging."""
    if not token:
        return ""
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def read_source_control_config(config_dir: Path | None = None) -> dict:
    """Read and normalize the ``source_control`` section from config.yaml."""
    config = load_project_config(config_dir)
    section = config.get("source_control")
    if not isinstance(section, dict):
        section = {}

    enabled_val = section.get("enabled")
    auto_detect_val = section.get("auto_detect_host_cli")
    setup_git_val = section.get("setup_git_credentials")
    providers_val = section.get("providers")

    return {
        "enabled": _egress_truthy(enabled_val) if enabled_val is not None else _DEFAULT_SOURCE_CONTROL["enabled"],
        "auto_detect_host_cli": (
            _egress_truthy(auto_detect_val)
            if auto_detect_val is not None
            else _DEFAULT_SOURCE_CONTROL["auto_detect_host_cli"]
        ),
        "setup_git_credentials": (
            _egress_truthy(setup_git_val)
            if setup_git_val is not None
            else _DEFAULT_SOURCE_CONTROL["setup_git_credentials"]
        ),
        "providers": providers_val if isinstance(providers_val, dict) else {},
    }


def _probe_host_cli(cmd: tuple[str, ...]) -> str | None:
    """Execute host CLI command to retrieve authentication token."""
    bin_name = cmd[0]
    if not shutil.which(bin_name):
        return None
    try:
        res = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0:
            token = res.stdout.strip()
            return token if token else None
    except Exception as e:
        logger.debug(f"Host CLI probe failed for {cmd}: {e}")
    return None


def resolve_source_control_tokens(
    config_dir: Path | None = None,
    log: logging.Logger | None = None,
) -> dict[str, str]:
    """Resolve tokens for all source control providers according to security precedence.

    Returns a mapping of environment variable names to token values to be injected into
    the container.
    """
    sc_log = log or logger
    cfg = read_source_control_config(config_dir)
    if not cfg["enabled"]:
        sc_log.debug("Source control integration disabled in config.yaml.")
        return {}

    providers_cfg = cfg["providers"]
    auto_detect_cli = cfg["auto_detect_host_cli"]

    resolved_env: dict[str, str] = {}

    for provider in PROVIDER_DEFINITIONS:
        p_cfg = providers_cfg.get(provider.name)
        if not isinstance(p_cfg, dict):
            p_cfg = {}
        keyring_service = str(p_cfg.get("keyring_service") or DEFAULT_KEYRING_SERVICE)

        # Extract configured keyring variable(s) or fallback to provider candidate accounts
        configured_vars = p_cfg.get("keyring_var") or p_cfg.get("keyring_account")
        if configured_vars:
            if isinstance(configured_vars, (list, tuple)):
                candidates = [str(k).strip() for k in configured_vars if str(k).strip()]
            else:
                candidates = [str(configured_vars).strip()]
        else:
            candidates = list(provider.default_keyring_vars)

        token: str | None = None
        source: str = ""

        # 1. OS Keyring (the only accepted credential store)
        for account in candidates:
            secret = get_keyring_secret(account, service=keyring_service)
            if secret:
                token = secret.strip()
                source = f"OS keyring (service='{keyring_service}', account='{account}')"
                break

        # 2. Host CLI active session (if auto_detect_host_cli: true)
        if not token and auto_detect_cli and provider.host_cli_cmd:
            cli_token = _probe_host_cli(provider.host_cli_cmd)
            if cli_token:
                token = cli_token
                source = f"host CLI ({provider.host_cli_cmd[0]})"

        if token:
            sc_log.info(f"Resolved {provider.name} source control token from {source}: {mask_token(token)}")
            resolved_env[provider.canonical_env_var] = token
            for sec_var in provider.secondary_export_vars:
                resolved_env[sec_var] = token

    return resolved_env

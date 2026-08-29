import sys
from typing import TYPE_CHECKING

import yaml

from schema_common import (
    MODELS_SCHEMA,
    SCHEMA,
    _validate_hf_models,
    _validate_models_flags,
    _validate_provider_base_urls,
    _validate_schema,
)
from template_paths import _check_chat_template_paths
from version import get_git_tag_version
from yaml_strict import DuplicateKeyError, check_duplicate_keys, load_yaml_strict

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

"""Configuration schema validation for `config.yaml` and `models.json`."""


# ─── Version from git tags ──────────────────────────────────────────────────


def get_app_version() -> str | None:
    """Retrieve the current pi-container version from git tags on the repository."""
    from config import REPO_ROOT

    return get_git_tag_version(REPO_ROOT)


#: Marker text in the stale-version error.
SCHEMA_VERSION_MISMATCH = "schema_version mismatch"


# ─── Validation ─────────────────────────────────────────────────────────────
# All helpers are imported from ``schema_common.py``.


def validate_config(config_path: Path) -> tuple[bool, list[str], str | None]:
    """Validate `config.yaml` structure, required fields, and schema version against git release tag.

    Returns:
        tuple of (is_valid, error_list, schema_version_string).
    """
    errors: list[str] = []

    if not config_path.exists():
        errors.append(f"Config file not found: {config_path}")
        return False, errors, None

    try:
        data = load_yaml_strict(config_path.read_text()) or {}
    except DuplicateKeyError as e:
        # Called out separately from other syntax errors because it is the one
        # YAML mistake that otherwise parses clean and loses a setting silently.
        errors.append(f"Config file has a duplicate key: {e}")
        errors.append("  Remove the repeated key — YAML keeps only the last occurrence.")
        return False, errors, None
    except yaml.YAMLError as e:
        errors.append(f"Config file is not valid YAML: {e}")
        return False, errors, None

    # Extract schema_version
    schema_version = data.get("schema_version")
    if schema_version is None:
        errors.append("  schema_version: required field missing")
        return False, errors, None

    schema_version_str = str(schema_version)

    # Check schema_version matches app version
    app_version = get_app_version()
    if app_version is not None and schema_version_str != app_version:
        errors.append(
            f"  {SCHEMA_VERSION_MISMATCH}: config has '{schema_version_str}', "
            f"but pi-container version is '{app_version}' (from git tag). "
            f"Delete .pi-container and re-run to re-seed, or update schema_version in config.yaml."
        )

    # Validate schema
    schema_errors = _validate_schema(data, SCHEMA)
    if schema_errors:
        errors.extend(schema_errors)

    is_valid = len(errors) == 0
    return is_valid, errors, schema_version_str


#: Per-project YAML files checked for duplicate keys at launch. ``config.yaml``
#: is validated in full by ``validate_config``; the other two are consumed by
#: the proxy addons inside the proxy container, where a dropped key is invisible
#: from the host — an allowlist rule silently discarded is a security-relevant
#: failure, so the check happens here rather than after the proxy is up.
PROJECT_YAML_FILES = ("config.yaml", "allowlist.yaml", "token_replacer.yaml")


def validate_project_yaml(config_dir: Path) -> list[str]:
    """Scan all project YAML files (`config.yaml`, `allowlist.yaml`, `token_replacer.yaml`) for duplicate mapping keys."""
    errors: list[str] = []
    for name in PROJECT_YAML_FILES:
        errors.extend(check_duplicate_keys(config_dir / name))
    return errors


# ─── Models.json validation ──────────────────────────────────────────────────
# All helpers and schemas are imported from ``schema_common.py``.


def validate_models(
    models_path: Path,
    check_chat_template_paths: bool = True,
) -> tuple[bool, list[str]]:
    """Validate `.pi-container/agent/models.json` structure, provider custom parameters, flags, and base URLs."""
    errors: list[str] = []

    if not models_path.exists():
        errors.append(f"Models file not found: {models_path}")
        return False, errors

    try:
        import json as _json

        with models_path.open("r") as f:
            data = _json.load(f)
    except Exception as e:
        errors.append(f"Models file is not valid JSON: {e}")
        return False, errors

    # Validate top-level structure
    schema_errors = _validate_schema(data, MODELS_SCHEMA)
    if schema_errors:
        errors.extend(schema_errors)
        return False, errors

    # Validate flags arrays
    providers = data.get("providers", {})
    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict) or "serverCustomParameters" not in provider_cfg:
            continue
        server_params = provider_cfg.get("serverCustomParameters", {})
        flags = server_params.get("flags", [])
        if not isinstance(flags, list):
            continue

        flag_errors = _validate_models_flags(flags, f"providers.{provider_name}.serverCustomParameters.flags")
        if flag_errors:
            errors.extend(flag_errors)

    # Validate hfModels entries
    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict) or "serverCustomParameters" not in provider_cfg:
            continue
        server_params = provider_cfg.get("serverCustomParameters", {})
        hf_models = server_params.get("hfModels")
        hf_errors = _validate_hf_models(hf_models, provider_name)
        if hf_errors:
            errors.extend(hf_errors)

    # Validate baseUrl on providers with serverCustomParameters
    base_url_errors = _validate_provider_base_urls(providers)
    if base_url_errors:
        errors.extend(base_url_errors)

    # Validate --chat-template-file paths if requested
    if check_chat_template_paths:
        _check_chat_template_paths(data, models_path, errors)

    is_valid = len(errors) == 0
    return is_valid, errors

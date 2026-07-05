import sys
from typing import TYPE_CHECKING

import yaml

from schema_common import (
    MODELS_SCHEMA,
    SCHEMA,
    _validate_hf_models,
    _validate_models_flags,
    _validate_models_schema,
    _validate_schema,
)
from template_paths import _check_chat_template_paths as _shared_check_chat_template_paths
from version import get_git_tag_version

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

"""Per-project configuration schema validation.

Reads the pi-container version from the latest git tag and validates that the
seeded ``.pi-container/config.yaml`` schema_version matches. Also validates that
required configuration fields are present with the correct types.

Importing this module has no gating side effects — it does NOT validate the
environment or call ``sys.exit``.

All helper functions and schema definitions are shared with
``.github/workflows/scripts/validate_versions.py`` via ``schema_common.py``.
"""


# ─── Version from git tags ──────────────────────────────────────────────────


def get_app_version() -> str | None:
    """Return the pi-container version from the latest git tag on the current branch.

    Returns ``None`` if no tags exist (pre-release state — validation is skipped).
    The version is authoritative from git, not from ``pyproject.toml``.
    """
    from config import REPO_ROOT

    return get_git_tag_version(REPO_ROOT)


# ─── Schema definition ─────────────────────────────────────────────────────
#
# Schema definitions live in ``schema_common.py``. Local aliases keep the
# existing ``_SCHEMA`` / ``_MODELS_SCHEMA`` names stable for callers and tests.

_SCHEMA = SCHEMA
_MODELS_SCHEMA = MODELS_SCHEMA


# ─── Validation ─────────────────────────────────────────────────────────────
# All helpers are imported from ``schema_common.py``.


def validate_config(config_path: Path) -> tuple[bool, list[str], str | None]:
    """Validate the config at ``config_path``.

    Returns ``(is_valid, errors, schema_version)``:
    - ``is_valid``: True if the config passes all checks.
    - ``errors``: List of human-readable error messages (empty if valid).
    - ``schema_version``: The schema_version from the config (or None if absent).

    Checks performed:
    1. ``schema_version`` matches the app version from the latest git tag.
    2. All required fields are present with correct types.
    """
    errors: list[str] = []

    if not config_path.exists():
        errors.append(f"Config file not found: {config_path}")
        return False, errors, None

    try:
        with config_path.open("r") as f:
            data = yaml.safe_load(f) or {}
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
            f"  schema_version mismatch: config has '{schema_version_str}', "
            f"but pi-container version is '{app_version}' (from git tag). "
            f"Delete .pi-container and re-run to re-seed, or update schema_version in config.yaml."
        )

    # Validate schema
    schema_errors = _validate_schema(data, SCHEMA)
    if schema_errors:
        errors.extend(schema_errors)

    is_valid = len(errors) == 0
    return is_valid, errors, schema_version_str


# ─── Models.json validation ──────────────────────────────────────────────────
# All helpers and schemas are imported from ``schema_common.py``.


def validate_models(
    models_path: Path,
    check_chat_template_paths: bool = True,
) -> tuple[bool, list[str]]:
    """Validate a models.json file against the schema.

    Returns ``(is_valid, errors)``:
    - ``is_valid``: True if the models file passes all checks.
    - ``errors``: List of human-readable error messages (empty if valid).

    Checks performed:
    1. ``providers`` dict is present and has the correct structure.
    2. Each provider's ``serverCustomParameters.flags`` items are valid.
    3. If ``check_chat_template_paths`` is True, validates that
       ``--chat-template-file`` paths resolve correctly.
    """
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
    schema_errors = _validate_models_schema(data, MODELS_SCHEMA)
    if schema_errors:
        errors.extend(schema_errors)
        return False, errors

    # Validate flags arrays
    providers = data.get("providers", {})
    for provider_name, provider_cfg in providers.items():
        server_params = provider_cfg.get("serverCustomParameters", {})
        flags = server_params.get("flags", [])
        if not isinstance(flags, list):
            continue

        flag_errors = _validate_models_flags(flags, f"providers.{provider_name}.serverCustomParameters.flags")
        if flag_errors:
            errors.extend(flag_errors)

    # Validate hfModels entries
    for provider_name, provider_cfg in providers.items():
        server_params = provider_cfg.get("serverCustomParameters", {})
        hf_models = server_params.get("hfModels")
        hf_errors = _validate_hf_models(hf_models, provider_name)
        if hf_errors:
            errors.extend(hf_errors)

    # Validate --chat-template-file paths if requested
    if check_chat_template_paths:
        _shared_check_chat_template_paths(data, models_path, errors)

    is_valid = len(errors) == 0
    return is_valid, errors

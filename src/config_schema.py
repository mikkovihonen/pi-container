import sys
from typing import TYPE_CHECKING

import yaml

from schema_common import (
    MODELS_SCHEMA,
    SCHEMA,
    _validate_models_providers,
    _validate_schema,
)
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


#: Available prebuilt toolchains that can be declared under ``tools.include``.
AVAILABLE_TOOLS = frozenset({"go", "rust"})


def _validate_tools(data: dict) -> list[str]:
    """Validate tools.include items against AVAILABLE_TOOLS."""
    errors: list[str] = []
    tools_section = data.get("tools")
    if not isinstance(tools_section, dict):
        return errors

    include = tools_section.get("include")
    if include is None:
        return errors
    if not isinstance(include, list):
        errors.append(f"  tools.include: expected list, got {type(include).__name__} (value: {include!r})")
        return errors

    for item in include:
        if not isinstance(item, str):
            errors.append(f"  tools.include: expected string tool name, got {type(item).__name__} (value: {item!r})")
        elif item not in AVAILABLE_TOOLS:
            errors.append(
                f"  tools.include: unknown tool '{item}'. Available tools: {', '.join(sorted(AVAILABLE_TOOLS))}"
            )
    return errors


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

    # Validate tools
    tool_errors = _validate_tools(data)
    if tool_errors:
        errors.extend(tool_errors)

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


def validate_models(
    models_path: Path,
    gateways_dir: Path | None = None,
) -> tuple[bool, list[str]]:
    """Validate `.pi-container/agent/models.json` structure, provider definitions, and base URLs."""
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

    if not isinstance(data, dict):
        errors.append("Models file must be a JSON object")
        return False, errors

    # Validate top-level structure
    schema_errors = _validate_schema(data, MODELS_SCHEMA)
    if schema_errors:
        errors.extend(schema_errors)
        return False, errors

    providers = data.get("providers")
    if not isinstance(providers, dict):
        errors.append("  providers: required dictionary missing")
        return False, errors

    provider_errors = _validate_models_providers(providers, gateways_dir=gateways_dir)
    if provider_errors:
        errors.extend(provider_errors)

    is_valid = len(errors) == 0
    return is_valid, errors

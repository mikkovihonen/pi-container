import subprocess
import sys
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

"""Per-project configuration schema validation.

Reads the pi-container version from the latest git tag and validates that the
seeded ``.pi-container/config.yaml`` schema_version matches. Also validates that
required configuration fields are present with the correct types.

Importing this module has no gating side effects — it does NOT validate the
environment or call ``sys.exit``.
"""


# ─── Version from git tags ──────────────────────────────────────────────────


def get_app_version() -> str | None:
    """Return the pi-container version from the latest git tag on the current branch.

    Returns ``None`` if no tags exist (pre-release state — validation is skipped).
    The version is authoritative from git, not from ``pyproject.toml``.
    """
    from config import REPO_ROOT

    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname", "--merged", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        tags = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
        if not tags:
            return None
        # Strip leading 'v' if present
        return tags[0].lstrip("v")
    # fmt: off
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None
    # fmt: on


# ─── Schema definition ─────────────────────────────────────────────────────


# Required top-level keys with their expected types. Sub-keys are validated
# recursively when the parent is present. ``None`` for the type means the value
# can be any YAML type (used for ``egress.allow`` which has a heterogeneous dict).
_SCHEMA = {
    "resources": {
        "type": dict,
        "keys": {
            "agent": {
                "type": dict,
                "keys": {
                    "memory": {"type": (str, type(None))},
                    "cpus": {"type": (int, type(None))},
                },
            },
            "proxy": {
                "type": dict,
                "keys": {
                    "memory": {"type": (str, type(None))},
                    "cpus": {"type": (int, type(None))},
                },
            },
        },
    },
    "llama": {
        "type": dict,
        "keys": {
            "startup_timeout": {"type": int},
            "startup_attempts": {"type": int},
        },
    },
    "network": {
        "type": dict,
        "keys": {
            "ipv6": {"type": (bool, str, int)},  # YAML bools, "true"/"1"/0/1 strings
            "dns": {"type": str},
        },
    },
    "proxy": {
        "type": dict,
        "keys": {
            "expose_ui": {"type": str},
        },
    },
    "agent": {
        "type": dict,
        "keys": {
            "env": {"type": dict},
            "mounts": {"type": list},
        },
    },
    "tmpfs": {
        "type": dict,
        "keys": {
            "paths": {"type": list},
        },
    },
    "flow_export": {
        "type": dict,
        "keys": {
            "enabled": {"type": (bool, str, int)},
        },
    },
    "egress": {
        "type": dict,
        "keys": {
            "allow": {"type": dict},
        },
    },
}

# Schema for models.json serverCustomParameters structure.
# Validates hfModels and flags arrays within each provider.
_MODELS_SCHEMA = {
    "providers": {
        "type": dict,
        "keys": {
            "serverCustomParameters": {
                "type": dict,
                "required": False,
                "keys": {
                    "hfModels": {
                        "type": dict,
                        "required": True,
                        "keys": {
                            "fileFlag": {"type": str},
                            "repo": {"type": str},
                            "file": {"type": str},
                            "dir": {"type": str},
                            "additionalServerFlags": {"type": list},
                            "sha256": {"type": str},
                        },
                    },
                    "flags": {
                        "type": list,
                        "required": False,
                        "item_type": {"type": (str, int, float)},
                    },
                },
            },
        },
    },
}


# ─── Validation ─────────────────────────────────────────────────────────────


def _validate_field(value: Any, expected: type | tuple[type, ...], path: str) -> list[str]:
    """Validate a single field's type. Returns a list of error messages (empty = OK)."""
    errors: list[str] = []
    if not isinstance(value, expected):
        errors.append(f"  {path}: expected {expected}, got {type(value).__name__} (value: {value!r})")
    return errors


def _validate_schema(data: dict, schema: dict, path: str = "") -> list[str]:
    """Recursively validate a config dict against the schema.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    for key, spec in schema.items():
        current_path = f"{path}.{key}" if path else key
        value = data.get(key)

        if value is None:
            # Missing key — check if it's required by the schema
            if spec.get("required", True):
                errors.append(f"  {current_path}: required field missing")
            continue

        # Type check the parent
        errors.extend(_validate_field(value, spec["type"], current_path))
        if not isinstance(value, dict):
            continue

        # Recurse into sub-keys
        sub_schema = spec.get("keys", {})
        if sub_schema:
            errors.extend(_validate_schema(value, sub_schema, current_path))

    return errors


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
    schema_errors = _validate_schema(data, _SCHEMA)
    if schema_errors:
        errors.extend(schema_errors)

    is_valid = len(errors) == 0
    return is_valid, errors, schema_version_str


# ─── Models.json validation ──────────────────────────────────────────────────


def _validate_models_schema(
    data: dict,
    schema: dict,
    path: str = "",
) -> list[str]:
    """Recursively validate a models.json dict against the schema.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    for key, spec in schema.items():
        current_path = f"{path}.{key}" if path else key
        value = data.get(key)

        if value is None:
            # Missing key — check if it's required
            if spec.get("required", True):
                errors.append(f"  {current_path}: required field missing")
            continue

        # Type check the parent
        errors.extend(_validate_field(value, spec["type"], current_path))
        if not isinstance(value, dict):
            continue

        # Recurse into sub-keys
        sub_schema = spec.get("keys", {})
        if sub_schema:
            errors.extend(_validate_models_schema(value, sub_schema, current_path))

    return errors


def _validate_models_flags(flags: list, path: str) -> list[str]:
    """Validate the flags array in serverCustomParameters.

    Each item must be a string (flag name) or number (flag value).
    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    for i, item in enumerate(flags):
        if not isinstance(item, (str, int, float)):
            errors.append(f"  {path}[{i}]: expected str/int/float, got {type(item).__name__}")
    return errors


def _validate_hf_models(
    hf_models: dict | None,
    provider_name: str,
) -> list[str]:
    """Validate hfModels entries have required non-null fields.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    if hf_models is None:
        errors.append(f"  providers.{provider_name}.serverCustomParameters.hfModels: must not be null")
        return errors

    if not isinstance(hf_models, dict):
        errors.append(
            f"  providers.{provider_name}.serverCustomParameters.hfModels: "
            f"expected dict, got {type(hf_models).__name__}"
        )
        return errors

    if len(hf_models) == 0:
        errors.append(f"  providers.{provider_name}.serverCustomParameters.hfModels: must not be empty")
        return errors

    required_fields = ("fileFlag", "repo", "file", "dir")
    for model_name, model_cfg in hf_models.items():
        if not isinstance(model_cfg, dict):
            errors.append(
                f"  providers.{provider_name}.serverCustomParameters.hfModels.{model_name}: "
                f"expected dict, got {type(model_cfg).__name__}"
            )
            continue

        for field in required_fields:
            value = model_cfg.get(field)
            if value is None:
                errors.append(
                    f"  providers.{provider_name}.serverCustomParameters.hfModels.{model_name}.{field}: "
                    f"must not be null"
                )
            elif not isinstance(value, str):
                errors.append(
                    f"  providers.{provider_name}.serverCustomParameters.hfModels.{model_name}.{field}: "
                    f"expected str, got {type(value).__name__}"
                )

    return errors


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
    schema_errors = _validate_models_schema(data, _MODELS_SCHEMA)
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
        _check_chat_template_paths(data, models_path, errors)

    is_valid = len(errors) == 0
    return is_valid, errors


def _check_chat_template_paths(
    data: dict,
    models_path: Path,
    errors: list[str],
) -> None:
    """Check that --chat-template-file paths exist on disk.

    For seed templates, resolves .pi-container -> pi-coding-agent/default/.
    For seeded configs, resolves .pi-container from the project root.
    """
    providers = data.get("providers", {})
    for provider_name, provider_cfg in providers.items():
        server_params = provider_cfg.get("serverCustomParameters", {})
        flags = server_params.get("flags", [])
        if not isinstance(flags, list):
            continue

        for i, flag in enumerate(flags):
            if flag == "--chat-template-file":
                if i + 1 >= len(flags):
                    errors.append(
                        f"  providers.{provider_name}.serverCustomParameters.flags[{i}] "
                        f"--chat-template-file has no following path"
                    )
                    continue

                template_path = flags[i + 1]
                if not isinstance(template_path, str):
                    errors.append(
                        f"  providers.{provider_name}.serverCustomParameters.flags[{i}] "
                        f"--chat-template-file path is not a string"
                    )
                    continue

                # Resolve path
                if template_path.startswith(".pi-container/"):
                    # For seed templates, .pi-container -> pi-coding-agent/default/
                    # For seeded configs, .pi-container is relative to project root
                    suffix = template_path[len(".pi-container/") :]
                    if models_path.parent.name == "agent" and "pi-coding-agent" in str(models_path):
                        # Seed template: models.json is at pi-coding-agent/default/agent/models.json
                        # .pi-container -> pi-coding-agent/default/
                        resolved = models_path.parent.parent.parent / "default" / suffix
                    else:
                        # Seeded config: models.json is at .pi-container/agent/models.json
                        # .pi-container is the grandparent directory
                        resolved = models_path.parent.parent / suffix
                else:
                    resolved = models_path.parent / template_path

                if not resolved.exists():
                    errors.append(
                        f"  providers.{provider_name}.serverCustomParameters.flags[{i}] "
                        f"--chat-template-file path does not exist: {resolved}"
                    )

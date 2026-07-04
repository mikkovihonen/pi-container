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

from typing import Any
from urllib.parse import urlparse

"""Shared schema definitions and validators.

Used by ``config_schema.py`` (per-project config validation) and
``.github/workflows/scripts/validate_versions.py`` (CI version consistency
checks). Importing this module has no gating side effects.
"""


# ─── Field validation ───────────────────────────────────────────────────────


def _validate_field(value: Any, expected: type | tuple[type, ...], path: str) -> list[str]:
    """Validate a single field's type. Returns a list of error messages (empty = OK)."""
    errors: list[str] = []
    if not isinstance(value, expected):
        errors.append(f"  {path}: expected {expected}, got {type(value).__name__} (value: {value!r})")
    return errors


# ─── Generic recursive schema validator ─────────────────────────────────────


def _validate_schema(data: dict, schema: dict, path: str = "") -> list[str]:
    """Recursively validate a dict against a schema definition.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    for key, spec in schema.items():
        current_path = f"{path}.{key}" if path else key
        value = data.get(key)

        if value is None:
            if spec.get("required", True):
                errors.append(f"  {current_path}: required field missing")
            continue

        errors.extend(_validate_field(value, spec["type"], current_path))
        if not isinstance(value, dict):
            continue

        sub_schema = spec.get("keys", {})
        if sub_schema:
            errors.extend(_validate_schema(value, sub_schema, current_path))

    return errors


# ─── Config.yaml schema ─────────────────────────────────────────────────────
#
# Required top-level keys with their expected types. Sub-keys are validated
# recursively when the parent is present. ``None`` for the type means the value
# can be any YAML type (used for ``egress.allow`` which has a heterogeneous dict).


SCHEMA: dict = {
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
            "max_view_flows": {"type": (int, type(None)), "required": False},
            "stream_large_bodies": {"type": (str, int, type(None)), "required": False},
        },
    },
    "agent": {
        "type": dict,
        "keys": {
            "env": {"type": dict},
            "mounts": {"type": list},
        },
    },
    "nested_containers": {
        "type": dict,
        "keys": {
            "enabled": {"type": (bool, str, int)},  # YAML bools, "true"/"1"/0/1 strings
            "storage": {"type": str},  # volume | tmpfs
            "security": {"type": str},  # disable | engine_t
            "ports": {
                "type": dict,
                "keys": {
                    "expose": {"type": str},  # localhost | lan
                    "publish": {"type": list},  # [3000, "18080:8080", ...]
                },
            },
        },
    },
    "tmpfs": {
        "type": dict,
        "keys": {
            "paths": {"type": list},
        },
    },
    "volumes": {
        "type": dict,
        "required": False,
        "keys": {
            "paths": {"type": list, "required": False},
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


# ─── models.json schema ─────────────────────────────────────────────────────
#
# Schema for models.json serverCustomParameters structure.
# Validates hfModels and flags arrays within each provider.


MODELS_SCHEMA: dict = {
    "providers": {
        "type": dict,
        "keys": {
            "baseUrl": {
                "type": str,
                "required": False,
            },
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


# ─── Models schema validation ──────────────────────────────────────────────


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
            if spec.get("required", True):
                errors.append(f"  {current_path}: required field missing")
            continue

        errors.extend(_validate_field(value, spec["type"], current_path))
        if not isinstance(value, dict):
            continue

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


def _validate_provider_base_urls(providers: dict) -> list[str]:
    """Validate baseUrl for providers that have serverCustomParameters.

    Checks:
    1. baseUrl is present and non-empty string for local providers.
    2. baseUrl starts with http:// or https://.
    3. Hostname in baseUrl is not localhost / 127.0.0.1 / ::1.
    4. baseUrl specifies an explicit port.
    5. Container ports are unique across all local providers.
    """
    errors: list[str] = []
    ports_seen: dict[int, list[str]] = {}

    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            continue
        if "serverCustomParameters" not in provider_cfg:
            continue

        base_url = provider_cfg.get("baseUrl")
        if base_url is None:
            errors.append(
                f"  providers.{provider_name}.baseUrl: required field missing for local provider (e.g. 'http://llama:9999/v1')"
            )
            continue
        if not isinstance(base_url, str) or not base_url.strip():
            errors.append(
                f"  providers.{provider_name}.baseUrl: must be a non-empty string URL (e.g. 'http://llama:9999/v1')"
            )
            continue

        try:
            parsed = urlparse(base_url)
        except Exception as e:
            errors.append(f"  providers.{provider_name}.baseUrl: invalid URL '{base_url}': {e}")
            continue

        if parsed.scheme not in ("http", "https"):
            errors.append(
                f"  providers.{provider_name}.baseUrl: must start with http:// or https:// (got '{base_url}')"
            )
            continue

        hostname = parsed.hostname
        if not hostname:
            errors.append(f"  providers.{provider_name}.baseUrl: missing hostname in '{base_url}'")
            continue

        if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
            errors.append(
                f"  providers.{provider_name}.baseUrl: '{hostname}' cannot be reached from inside the container; "
                f"use 'llama', the provider name ('{provider_name}'), or a custom hostname instead"
            )
            continue

        port = parsed.port
        if port is None:
            errors.append(f"  providers.{provider_name}.baseUrl: port is required (e.g. 'http://llama:9999/v1')")
            continue

        if not (1 <= port <= 65535):
            errors.append(f"  providers.{provider_name}.baseUrl: invalid port {port} (must be between 1 and 65535)")
            continue

        ports_seen.setdefault(port, []).append(provider_name)

    for port, prov_list in ports_seen.items():
        if len(prov_list) > 1:
            prov_str = ", ".join(prov_list)
            errors.append(
                f"  Duplicate container port {port} in baseUrl for providers: {prov_str}; "
                f"each local provider must use a unique port (e.g. 9999, 9998)"
            )

    return errors

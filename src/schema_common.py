from typing import Any
from urllib.parse import urlparse

"""Shared schema definitions and validation logic for configuration files."""


# ─── Field validation ───────────────────────────────────────────────────────


def _validate_field(value: Any, expected: type | tuple[type, ...], path: str) -> list[str]:
    """Validate a single field's type, returning any error messages."""
    errors: list[str] = []
    if not isinstance(value, expected):
        errors.append(f"  {path}: expected {expected}, got {type(value).__name__} (value: {value!r})")
    return errors


# ─── Generic recursive schema validator ─────────────────────────────────────


def _validate_schema(data: dict, schema: dict, path: str = "") -> list[str]:
    """Recursively validate a dictionary structure against a schema specification."""
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
    "tools": {
        "type": dict,
        "required": False,
        "keys": {
            "include": {"type": list, "required": False},
        },
    },
    "agent": {
        "type": dict,
        "keys": {
            "env": {"type": dict},
            "mounts": {"type": list},
            "capabilities": {"type": list, "required": False},
            "devices": {"type": list, "required": False},
            "read_only_pi_container": {"type": (bool, str, int), "required": False},
            "mode": {"type": str, "required": False},
            "ports": {
                "type": dict,
                "required": False,
                "keys": {
                    "expose": {"type": str, "required": False},
                    "publish": {"type": list, "required": False},
                },
            },
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
    "git": {
        "type": dict,
        "required": False,
        "keys": {
            "safe_directory": {"type": (bool, str, int), "required": False},
            "rewrite_ssh_to_https": {"type": (bool, str, int), "required": False},
        },
    },
    "source_control": {
        "type": dict,
        "required": False,
        "keys": {
            "enabled": {"type": (bool, str, int), "required": False},
            "auto_detect_host_cli": {"type": (bool, str, int), "required": False},
            "setup_git_credentials": {"type": (bool, str, int), "required": False},
            "providers": {"type": dict, "required": False},
        },
    },
    "security": {
        "type": dict,
        "required": False,
        "keys": {
            "read_only_git_hooks": {"type": (bool, str, int), "required": False},
            "blocked_mount_paths": {"type": list, "required": False},
            "blocked_ip_ranges": {"type": list, "required": False},
            "git_config_allowlist": {"type": list, "required": False},
        },
    },
}


# ─── models.json schema ─────────────────────────────────────────────────────
#
# Schema for models.json provider definitions and client-facing model manifests.


MODELS_SCHEMA: dict = {
    "providers": {
        "type": dict,
    },
}


# ─── Models schema validation ──────────────────────────────────────────────


def _validate_models_providers(providers: dict, gateways_dir: Any = None) -> list[str]:
    """Validate providers mapping in models.json.

    Checks:
    1. Each provider is a dictionary with a non-empty 'models' list.
    2. Each model entry has a non-empty string 'id'.
    3. Rejects obsolete 'serverCustomParameters'.
    4. Validates 'baseUrl':
       - Must start with http:// or https://.
       - Hostname cannot be localhost / 127.0.0.1 / ::1.
       - Local providers (not in gateways) require an explicit port (1-65535).
       - Local container ports must be unique across providers.
    """
    errors: list[str] = []
    ports_seen: dict[int, list[str]] = {}

    from registry import load_gateway_def

    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            errors.append(f"  providers.{provider_name}: expected dict, got {type(provider_cfg).__name__}")
            continue

        if "serverCustomParameters" in provider_cfg:
            errors.append(
                f"  providers.{provider_name}.serverCustomParameters: obsolete and unsupported; "
                f"define local models under ~/.config/pi-container/models/<id>.yaml instead"
            )

        models = provider_cfg.get("models")
        if models is None:
            errors.append(f"  providers.{provider_name}.models: required field missing")
        elif not isinstance(models, list):
            errors.append(f"  providers.{provider_name}.models: expected list, got {type(models).__name__}")
        elif len(models) == 0:
            errors.append(f"  providers.{provider_name}.models: must not be empty")
        else:
            for i, m in enumerate(models):
                if not isinstance(m, dict):
                    errors.append(f"  providers.{provider_name}.models[{i}]: expected dict, got {type(m).__name__}")
                    continue
                m_id = m.get("id")
                if not m_id or not isinstance(m_id, str):
                    errors.append(f"  providers.{provider_name}.models[{i}].id: required non-empty string missing")

        # Base URL validation
        base_url = provider_cfg.get("baseUrl")
        gw_def = load_gateway_def(provider_name, registry_dir=gateways_dir)
        is_gateway = gw_def is not None

        if base_url is None:
            if not is_gateway:
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

        try:
            port = parsed.port
        except ValueError as e:
            errors.append(f"  providers.{provider_name}.baseUrl: invalid port in '{base_url}': {e}")
            continue

        if not is_gateway:
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


def validate_model_def_data(data: dict, model_id: str = "") -> list[str]:
    """Validate local model definition dictionary (~/.config/pi-container/models/<id>.yaml)."""
    errors: list[str] = []
    hf_models = data.get("hfModels")
    if hf_models is None:
        errors.append(f"model definition '{model_id}': hfModels must not be null")
    elif not isinstance(hf_models, dict):
        errors.append(f"model definition '{model_id}': hfModels expected dict, got {type(hf_models).__name__}")
    elif len(hf_models) == 0:
        errors.append(f"model definition '{model_id}': hfModels must not be empty")
    else:
        if "main" not in hf_models:
            errors.append(f"model definition '{model_id}': hfModels.main is required")
        required_fields = ("fileFlag", "repo", "file")
        for m_name, m_cfg in hf_models.items():
            if not isinstance(m_cfg, dict):
                errors.append(f"model definition '{model_id}': hfModels.{m_name} expected dict")
                continue
            for f in required_fields:
                val = m_cfg.get(f)
                if val is None or not isinstance(val, str) or not val.strip():
                    errors.append(f"model definition '{model_id}': hfModels.{m_name}.{f} must be a non-empty string")

    flags = data.get("flags")
    if flags is not None:
        if not isinstance(flags, list):
            errors.append(f"model definition '{model_id}': flags expected list")
        else:
            for i, item in enumerate(flags):
                if not isinstance(item, (str, int, float)):
                    errors.append(
                        f"model definition '{model_id}': flags[{i}] expected str/int/float, got {type(item).__name__}"
                    )

    return errors

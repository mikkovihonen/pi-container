#!/usr/bin/env python3
"""Validate version consistency across git tag, pyproject.toml, and seed config.

Checks:
  1. Latest git tag on HEAD (v* format) matches pyproject.toml [project] version
  2. pyproject.toml [project] version matches schema_version in
     pi-coding-agent/default/config.yaml
  3. pi-coding-agent/default/config.yaml is valid against the expected schema
     (required fields present, correct types)

This script runs in CI as a consistency gate before a release is published.
It validates the *template* seed config, not a seeded .pi-container/config.yaml
(which is checked at runtime by src/run.py).

Exit codes:
  0 - All checks pass
  1 - One or more checks failed (errors printed to stdout)

Usage:
  python .github/workflows/scripts/validate_versions.py
  # Or from the repo root:
  .github/workflows/scripts/validate_versions.py
"""

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TAG_PREFIX = "v"


# ─── Helpers ──────────────────────────────────────────────────────────────


def _info(msg: str) -> None:
    print(f"  ✓ {msg}")


def _error(msg: str) -> None:
    print(f"  ✗ {msg}")


def _check(label: str, condition: bool, detail: str = "") -> bool:
    """Print pass/fail for a single check. Returns True if passed."""
    if condition:
        _info(f"{label}: {detail}")
        return True
    else:
        _error(f"{label}: {detail}")
        return False


# ─── Version sources ────────────────────────────────────────────────────


def get_git_tag_version() -> str | None:
    """Return the version from the latest v* git tag on HEAD (without 'v' prefix).

    Returns None if no tags exist (pre-release — version check is skipped).
    """
    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        tags = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
        if not tags:
            return None
        # Return the first (highest) version, stripped of 'v' prefix
        version = tags[0].lstrip(TAG_PREFIX)
        return version
    # fmt: off
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None
    # fmt: on


def get_pyproject_version() -> str | None:
    """Read the version from pyproject.toml [project] version."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version")
    except Exception:
        return None


def get_seed_schema_version() -> str | None:
    """Read the schema_version from pi-coding-agent/default/config.yaml."""
    config_path = REPO_ROOT / "pi-coding-agent" / "default" / "config.yaml"
    if not config_path.exists():
        return None
    try:
        data = yaml.safe_load(config_path.read_text())
        return data.get("schema_version")
    except Exception:
        return None


# ─── Seed config schema validation ──────────────────────────────────────

# Required top-level keys with their expected types. Sub-keys are validated
# recursively when the parent is present. ``None`` for the type means the value
# can be any YAML type (used for ``egress.allow`` which has a heterogeneous dict).
_SEED_SCHEMA = {
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
            "ipv6": {"type": (bool, str, int)},
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


def _validate_field(value: Any, expected: type | tuple[type, ...], path: str) -> list[str]:
    """Validate a single field's type. Returns a list of error messages."""
    errors: list[str] = []
    if not isinstance(value, expected):
        errors.append(f"  {path}: expected {expected}, got {type(value).__name__} (value: {value!r})")
    return errors


def _validate_seed_config(data: dict, schema: dict | None = None) -> list[str]:
    """Recursively validate the seed config against the schema."""
    if schema is None:
        schema = _SEED_SCHEMA
    errors: list[str] = []
    for key, spec in schema.items():
        current_path = key
        value = data.get(key)

        if value is None:
            errors.append(f"  {current_path}: required field missing")
            continue

        errors.extend(_validate_field(value, spec["type"], current_path))
        if not isinstance(value, dict):
            continue

        sub_schema = spec.get("keys", {})
        if sub_schema:
            errors.extend(_validate_seed_config(value, sub_schema))

    return errors


# ─── Main validation logic ──────────────────────────────────────────────


def main() -> int:
    """Run all validation checks. Returns 0 on success, 1 on failure."""
    print("Validating version consistency...")
    all_pass = True

    # ── Check 1: Git tag version ─────────────────────────────────────────
    git_version = get_git_tag_version()
    if git_version is None:
        print("  ⚠ No git tags found — skipping version consistency checks.")
        print("    (This is normal for forks or shallow clones without tags.)")
        print("  ✓ Proceeding with schema-only validation.")
    else:
        _info(f"Git tag version: {TAG_PREFIX}{git_version}")

    # ── Check 2: pyproject.toml version ──────────────────────────────────
    pyproject_version = get_pyproject_version()
    if pyproject_version is None:
        _error("pyproject.toml version: not found")
        all_pass = False
    else:
        _info(f"pyproject.toml version: {pyproject_version}")

    # Check git tag matches pyproject.toml
    if git_version is not None and pyproject_version is not None:
        if git_version != pyproject_version:
            _error(
                f"Git tag vs pyproject.toml: tag is '{TAG_PREFIX}{git_version}', "
                f"pyproject.toml is '{pyproject_version}'"
            )
            all_pass = False
        else:
            _info(f"Git tag matches pyproject.toml: {TAG_PREFIX}{git_version}")

    # ── Check 3: schema_version in seed config ───────────────────────────
    seed_config_path = REPO_ROOT / "pi-coding-agent" / "default" / "config.yaml"
    seed_data: dict | None = None
    if not seed_config_path.exists():
        _error(f"Seed config not found: {seed_config_path}")
        all_pass = False
    else:
        try:
            seed_data = yaml.safe_load(seed_config_path.read_text()) or {}
        except yaml.YAMLError as e:
            _error(f"Seed config is not valid YAML: {e}")
            all_pass = False

        if seed_data is not None:
            schema_version = seed_data.get("schema_version")
            if schema_version is None:
                _error("schema_version: missing from pi-coding-agent/default/config.yaml")
                all_pass = False
            else:
                schema_version_str = str(schema_version)
                _info(f"Schema version: {schema_version_str}")

                # Check pyproject.toml matches schema_version
                if pyproject_version is not None:
                    if pyproject_version != schema_version_str:
                        _error(
                            f"pyproject.toml vs schema_version: pyproject.toml is "
                            f"'{pyproject_version}', config.yaml has '{schema_version_str}'"
                        )
                        all_pass = False
                    else:
                        _info(
                            f"pyproject.toml matches schema_version: {pyproject_version}"
                        )

                # Check git tag matches schema_version
                if git_version is not None:
                    if git_version != schema_version_str:
                        _error(
                            f"Git tag vs schema_version: tag is '{TAG_PREFIX}{git_version}', "
                            f"config.yaml has '{schema_version_str}'"
                        )
                        all_pass = False
                    else:
                        _info(
                            f"Git tag matches schema_version: {TAG_PREFIX}{git_version}"
                        )

    # ── Check 4: Seed config schema validation ───────────────────────────
    if seed_data is not None:
        print()
        print("Validating seed config schema...")
        schema_errors = _validate_seed_config(seed_data)
        if schema_errors:
            _error(f"Seed config schema has {len(schema_errors)} error(s):")
            for err in schema_errors:
                print(f"    {err}")
            all_pass = False
        else:
            _info("Seed config schema: all required fields present with correct types")

    # ── Check 5: Seed directory completeness ─────────────────────────────
    seed_dir = REPO_ROOT / "pi-coding-agent" / "default"
    if not seed_dir.exists():
        _error(f"Seed directory not found: {seed_dir}")
        all_pass = False
    else:
        print()
        print("Validating seed directory completeness...")

        # Expected directories and files that _ensure_project_config() seeds
        # from pi-coding-agent/default/ into .pi-container/
        _EXPECTED_DIRS = ("agent", "chat-templates")
        _EXPECTED_FILES = (
            "config.yaml",
            "allowlist.yaml",
            "token_replacer.yaml",
            "entrypoint.sh",
        )

        missing: list[str] = []
        for name in _EXPECTED_DIRS:
            if not (seed_dir / name).is_dir():
                missing.append(f"{name}/ (directory)")

        for name in _EXPECTED_FILES:
            if not (seed_dir / name).is_file():
                missing.append(name)

        if missing:
            _error(f"Seed directory is missing {len(missing)} expected item(s):")
            for item in missing:
                print(f"    - {item}")
            all_pass = False
        else:
            _info(
                f"Seed directory complete: {_EXPECTED_DIRS} dirs + "
                f"{len(_EXPECTED_FILES)} files all present"
            )

    # ── Check 6: chat-template-file path existence ───────────────────────
    print()
    print("Validating chat-template-file references...")
    models_path = seed_dir / "agent" / "models.json"
    template_path_errors: list[str] = []

    if not models_path.exists():
        _info("No models.json found — skipping chat-template-file checks.")
        models_data = None
    else:
        try:
            import json as _json
            with models_path.open("r") as f:
                models_data = _json.load(f)
        except Exception as e:
            _error(f"Failed to parse models.json: {e}")
            all_pass = False
            models_data = None

        if models_data is not None:
            providers = models_data.get("providers", {})
            for provider_name, provider_cfg in providers.items():
                server_params = provider_cfg.get("serverCustomParameters", {})
                flags = server_params.get("flags", [])

                if not isinstance(flags, list):
                    continue

                for i, flag in enumerate(flags):
                    if flag == "--chat-template-file":
                        if i + 1 >= len(flags):
                            template_path_errors.append(
                                f"[{provider_name}] --chat-template-file at index {i} "
                                f"has no following path value"
                            )
                            continue

                        template_path = flags[i + 1]
                        if not isinstance(template_path, str):
                            template_path_errors.append(
                                f"[{provider_name}] --chat-template-file at index {i} "
                                f"is not a string (got {type(template_path).__name__})"
                            )
                            continue

                        # Resolve path: .pi-container -> pi-coding-agent/default/
                        if template_path.startswith(".pi-container/"):
                            resolved = seed_dir / template_path[len(".pi-container/"):]
                        elif template_path.startswith("pi-coding-agent/default/"):
                            resolved = Path(template_path)
                        else:
                            resolved = seed_dir / template_path

                        if not resolved.exists():
                            template_path_errors.append(
                                f"[{provider_name}] --chat-template-file: "
                                f"'{template_path}' resolves to '{resolved}' which does not exist"
                            )

    if template_path_errors:
        _error(f"Found {len(template_path_errors)} invalid chat-template-file reference(s):")
        for err in template_path_errors:
            print(f"    - {err}")
        all_pass = False
    else:
        _info("All --chat-template-file paths exist")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    if all_pass:
        print("All checks passed.")
        return 0
    else:
        print("Validation FAILED — fix the errors above and re-run.")
        print()
        if git_version is None:
            print("Tip: run 'git fetch --tags' if you suspect tags are missing.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

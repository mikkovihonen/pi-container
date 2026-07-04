"""Tests for config_schema.py — per-project configuration validation."""

import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def valid_config(tmp_path: Path) -> Path:
    """Create a valid config.yaml with schema_version matching the test version."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "schema_version": "0.1.0",
                "resources": {
                    "agent": {"memory": "16g", "cpus": 8},
                    "proxy": {"memory": "4g", "cpus": 4},
                },
                "llama": {"startup_timeout": 180, "startup_attempts": 2},
                "network": {"ipv6": False, "dns": "1.1.1.1"},
                "proxy": {"expose_ui": "localhost"},
                "agent": {"env": {}, "mounts": []},
                "tmpfs": {"paths": []},
                "flow_export": {"enabled": False},
                "egress": {"allow": {}},
            }
        )
    )
    return config_path


@pytest.fixture
def config_with_schema_version_only(tmp_path: Path) -> Path:
    """Create a config.yaml with only schema_version (missing required fields)."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"schema_version": "0.1.0"}))
    return config_path


@pytest.fixture
def config_without_schema_version(tmp_path: Path) -> Path:
    """Create a config.yaml without schema_version."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"resources": {"agent": {"memory": "16g", "cpus": 8}}}))
    return config_path


@pytest.fixture
def config_with_wrong_schema_version(tmp_path: Path) -> Path:
    """Create a config.yaml with a mismatched schema_version."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"schema_version": "9.9.9"}))
    return config_path


# ─── get_app_version tests ─────────────────────────────────────────────────


class TestGetAppVersion:
    """Tests for get_app_version()."""

    def test_returns_none_when_no_tags(self, tmp_path: Path):
        """When no git tags exist, get_app_version returns None."""
        from config_schema import get_app_version

        # Patch subprocess.run to simulate no tags
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "tag", "--sort=-v:refname", "--merged", "HEAD"],
                returncode=0,
                stdout="",
            )
            result = get_app_version()
        assert result is None

    def test_returns_version_from_tag(self, tmp_path: Path):
        """When a git tag exists, get_app_version returns the version without 'v' prefix."""
        from config_schema import get_app_version

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "tag", "--sort=-v:refname", "--merged", "HEAD"],
                returncode=0,
                stdout="v1.2.3\n",
            )
            result = get_app_version()
        assert result == "1.2.3"

    def test_handles_multiple_tags(self, tmp_path: Path):
        """When multiple tags exist, get_app_version returns the latest one."""
        from config_schema import get_app_version

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "tag", "--sort=-v:refname", "--merged", "HEAD"],
                returncode=0,
                stdout="v2.0.0\nv1.2.3\n",
            )
            result = get_app_version()
        assert result == "2.0.0"

    def test_returns_none_on_git_error(self, tmp_path: Path):
        """When git command fails, get_app_version returns None."""
        from config_schema import get_app_version

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")
            result = get_app_version()
        assert result is None


# ─── validate_config tests ─────────────────────────────────────────────────


class TestValidateConfig:
    """Tests for validate_config()."""

    def test_valid_config_passes(self, valid_config: Path):
        """A valid config passes validation."""
        from config_schema import validate_config

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, schema_version = validate_config(valid_config)
        assert is_valid is True
        assert errors == []
        assert schema_version == "0.1.0"

    def test_missing_config_file(self, tmp_path: Path):
        """When config.yaml doesn't exist, validation fails."""
        from config_schema import validate_config

        config_path = tmp_path / "nonexistent" / "config.yaml"
        is_valid, errors, _ = validate_config(config_path)
        assert is_valid is False
        assert any("not found" in e for e in errors)

    def test_missing_schema_version(self, config_without_schema_version: Path):
        """When schema_version is missing, validation fails."""
        from config_schema import validate_config

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(config_without_schema_version)
        assert is_valid is False
        assert any("schema_version" in e and "missing" in e for e in errors)

    def test_schema_version_mismatch(self, config_with_wrong_schema_version: Path):
        """When schema_version doesn't match app version, validation fails."""
        from config_schema import validate_config

        with patch("config_schema.get_app_version", return_value="1.0.0"):
            is_valid, errors, schema_version = validate_config(config_with_wrong_schema_version)
        assert is_valid is False
        assert any("mismatch" in e for e in errors)
        assert schema_version == "9.9.9"

    def test_schema_version_match(self, valid_config: Path):
        """When schema_version matches app version, validation passes (if schema is valid)."""
        from config_schema import validate_config

        with patch("config_schema.get_app_version", return_value="0.1.0"):
            is_valid, errors, schema_version = validate_config(valid_config)
        assert is_valid is True
        assert schema_version == "0.1.0"

    def test_missing_required_fields(self, config_with_schema_version_only: Path):
        """When required fields are missing, validation fails."""
        from config_schema import validate_config

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(config_with_schema_version_only)
        assert is_valid is False
        # Should have errors for missing resources, llama, network, etc.
        assert len(errors) > 1

    def test_wrong_type_for_field(self, tmp_path: Path):
        """When a field has the wrong type, validation fails."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "schema_version": "0.1.0",
                    "resources": {
                        "agent": {"memory": 123, "cpus": 8},  # memory should be str
                    },
                    "llama": {"startup_timeout": 180, "startup_attempts": 2},
                    "network": {"ipv6": False, "dns": "1.1.1.1"},
                    "proxy": {"expose_ui": "localhost"},
                    "agent": {"env": {}, "mounts": []},
                    "tmpfs": {"paths": []},
                    "flow_export": {"enabled": False},
                    "egress": {"allow": {}},
                }
            )
        )

        from config_schema import validate_config

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(config_path)
        assert is_valid is False
        assert any("expected" in e and "memory" in e for e in errors)

    def test_invalid_yaml(self, tmp_path: Path):
        """When config.yaml is invalid YAML, validation fails."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("invalid: yaml: content: [")

        from config_schema import validate_config

        is_valid, errors, _ = validate_config(config_path)
        assert is_valid is False
        assert any("YAML" in e for e in errors)

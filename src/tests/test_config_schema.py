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
                "nested_containers": {
                    "enabled": False,
                    "storage": "volume",
                    "security": "disable",
                    "ports": {"expose": "localhost", "publish": []},
                },
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
                    "nested_containers": {
                        "enabled": False,
                        "storage": "volume",
                        "security": "disable",
                        "ports": {"expose": "localhost", "publish": []},
                    },
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


class TestNestedContainersSchema:
    """The nested_containers section is required, like every other section."""

    def test_missing_section_fails(self, valid_config: Path):
        import yaml as _yaml

        from config_schema import validate_config

        data = _yaml.safe_load(valid_config.read_text())
        del data["nested_containers"]
        valid_config.write_text(_yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("nested_containers" in e and "missing" in e for e in errors)

    def test_wrong_storage_type_fails(self, valid_config: Path):
        import yaml as _yaml

        from config_schema import validate_config

        data = _yaml.safe_load(valid_config.read_text())
        data["nested_containers"]["storage"] = 42  # must be a str
        valid_config.write_text(_yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("storage" in e for e in errors)

    def test_enabled_accepts_yaml_bool_and_string(self, valid_config: Path):
        import yaml as _yaml

        from config_schema import validate_config

        for value in (True, "true", 1):
            data = _yaml.safe_load(valid_config.read_text())
            data["nested_containers"]["enabled"] = value
            valid_config.write_text(_yaml.dump(data))
            with patch("config_schema.get_app_version", return_value=None):
                is_valid, errors, _ = validate_config(valid_config)
            assert is_valid is True, errors


class TestValidateModels:
    """Tests for validate_models() in config_schema.py."""

    def _write_models(
        self,
        tmp_path: Path,
        providers: dict,
    ) -> Path:
        """Write a models.json file and return its path."""
        import json

        models_path = tmp_path / "models.json"
        models_path.write_text(json.dumps({"providers": providers}, indent=2))
        return models_path

    def test_valid_models_passes(self, tmp_path: Path):
        """When models.json has valid structure, validation passes."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [
                            "--ctx-size",
                            4096,
                            "--n-gpu-layers",
                            999,
                        ],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "test/repo",
                                "file": "model.gguf",
                                "dir": "model-dir",
                                "additionalServerFlags": [],
                                "sha256": "",
                            },
                        },
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is True
        assert errors == []

    def test_valid_multiple_providers_passes(self, tmp_path: Path):
        """When multiple providers have valid distinct ports, validation passes."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-ornith": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": ["--ctx-size", 4096],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "test/repo1",
                                "file": "model1.gguf",
                                "dir": "model-dir1",
                                "additionalServerFlags": [],
                                "sha256": "",
                            },
                        },
                    },
                },
                "local-gemma": {
                    "baseUrl": "http://local-gemma:9998/v1",
                    "serverCustomParameters": {
                        "flags": ["--ctx-size", 8192],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "test/repo2",
                                "file": "model2.gguf",
                                "dir": "model-dir2",
                                "additionalServerFlags": [],
                                "sha256": "",
                            },
                        },
                    },
                },
                "anthropic": {
                    "baseUrl": "https://api.anthropic.com/v1",
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is True
        assert errors == []

    def test_duplicate_port_fails(self, tmp_path: Path):
        """When multiple local providers share the same container port, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-ornith": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "test/repo1",
                                "file": "model1.gguf",
                                "dir": "model-dir1",
                                "additionalServerFlags": [],
                                "sha256": "",
                            },
                        },
                    },
                },
                "local-gemma": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "test/repo2",
                                "file": "model2.gguf",
                                "dir": "model-dir2",
                                "additionalServerFlags": [],
                                "sha256": "",
                            },
                        },
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("Duplicate container port 9999" in e for e in errors)

    def test_missing_models_file(self, tmp_path: Path):
        """When models.json does not exist, validation fails."""
        from config_schema import validate_models

        models_path = tmp_path / "missing.json"
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("not found" in e.lower() for e in errors)

    def test_invalid_json(self, tmp_path: Path):
        """When models.json is not valid JSON, validation fails."""
        from config_schema import validate_models

        models_path = tmp_path / "models.json"
        models_path.write_text("{invalid json")
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("JSON" in e for e in errors)

    def test_invalid_flag_type(self, tmp_path: Path):
        """When flags contain non-string/non-number items, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "flags": [
                            "--ctx-size",
                            4096,
                            None,  # Invalid: None is not str/int/float
                        ],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("NoneType" in e for e in errors)

    def test_missing_chat_template_path(self, tmp_path: Path):
        """When --chat-template-file points to a non-existent file, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "flags": [
                            "--ctx-size",
                            4096,
                            "--chat-template-file",
                            ".pi-container/chat-templates/missing/chat_template.jinja",
                        ],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("does not exist" in e for e in errors)

    def test_chat_template_file_missing_path_value(self, tmp_path: Path):
        """When --chat-template-file has no following path, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "flags": [
                            "--ctx-size",
                            4096,
                            "--chat-template-file",  # No following path
                        ],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("no following path" in e for e in errors)

    def test_chat_template_path_with_seed_dir(self, tmp_path: Path):
        """With seed_dir, .pi-container/ paths resolve relative to seed_dir."""
        from template_paths import _resolve_chat_template_path

        seed = tmp_path / "seed"
        seed.mkdir()
        resolved = _resolve_chat_template_path(
            ".pi-container/chat-templates/foo/chat_template.jinja",
            tmp_path / "models.json",
            seed_dir=seed,
        )
        assert resolved == seed / "chat-templates/foo/chat_template.jinja"

    def test_chat_template_path_with_pi_coding_agent_prefix(self, tmp_path: Path):
        """Seed templates with pi-coding-agent/default/ prefix resolve under seed_dir."""
        from template_paths import _resolve_chat_template_path

        seed = tmp_path / "repo"
        seed.mkdir()
        # Include pi-coding-agent in the path so the seed-template branch fires
        models = seed / "pi-coding-agent" / "default" / "agent" / "models.json"
        models.parent.mkdir(parents=True, exist_ok=True)
        resolved = _resolve_chat_template_path(
            ".pi-container/chat-templates/foo/chat_template.jinja",
            models,
        )
        assert resolved == seed / "pi-coding-agent" / "default" / "chat-templates/foo/chat_template.jinja"

    def test_chat_template_path_other_prefix(self, tmp_path: Path):
        """Other paths resolve relative to models.json parent."""
        from template_paths import _resolve_chat_template_path

        models = tmp_path / "agent" / "models.json"
        models.parent.mkdir(parents=True, exist_ok=True)
        resolved = _resolve_chat_template_path(
            "relative/template.jinja",
            models,
        )
        assert resolved == tmp_path / "agent" / "relative" / "template.jinja"

    def test_chat_template_path_with_existing_file(self, tmp_path: Path):
        """When --chat-template-file points to an existing file, validation passes."""
        import json

        from config_schema import validate_models

        # Create the directory structure matching the real project:
        # .pi-container/agent/models.json
        # .pi-container/chat-templates/test-model/chat_template.jinja
        pi_container = tmp_path / ".pi-container"
        agent_dir = pi_container / "agent"
        agent_dir.mkdir(parents=True)

        template_dir = pi_container / "chat-templates" / "test-model"
        template_dir.mkdir(parents=True)
        template_file = template_dir / "chat_template.jinja"
        template_file.write_text("# template")

        # Create models.json
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {
                            "baseUrl": "http://llama:9999/v1",
                            "serverCustomParameters": {
                                "flags": [
                                    "--ctx-size",
                                    4096,
                                    "--chat-template-file",
                                    ".pi-container/chat-templates/test-model/chat_template.jinja",
                                ],
                                "hfModels": {
                                    "main": {
                                        "fileFlag": "--model",
                                        "repo": "test/repo",
                                        "file": "model.gguf",
                                        "dir": "model-dir",
                                        "additionalServerFlags": [],
                                        "sha256": "",
                                    },
                                },
                            },
                        },
                    },
                },
                indent=2,
            )
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is True
        assert errors == []

    def test_flags_not_a_list_skipped(self, tmp_path: Path):
        """When flags is not a list, the provider is skipped (no error)."""
        from template_paths import _check_chat_template_paths

        errors: list[str] = []
        data = {
            "providers": {
                "local-test": {
                    "serverCustomParameters": {
                        "flags": "not-a-list",
                    },
                },
            },
        }
        _check_chat_template_paths(data, tmp_path / "models.json", errors)
        assert errors == []

    def test_chat_template_path_non_string_flag(self, tmp_path: Path):
        """When --chat-template-file's value is not a string, validation fails."""
        import json

        from config_schema import validate_models

        pi_container = tmp_path / ".pi-container"
        agent_dir = pi_container / "agent"
        agent_dir.mkdir(parents=True)
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {
                            "serverCustomParameters": {
                                "flags": [
                                    "--chat-template-file",
                                    42,  # not a string
                                ],
                            },
                        },
                    },
                },
            )
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("path is not a string" in e for e in errors)

    def test_hf_models_null(self, tmp_path: Path):
        """When hfModels is null, validation fails."""

        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "hfModels": None,
                        "flags": [],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("must not be null" in e and "hfModels" in e for e in errors)

    def test_hf_models_empty(self, tmp_path: Path):
        """When hfModels is empty, validation fails."""

        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "hfModels": {},
                        "flags": [],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("must not be empty" in e for e in errors)

    def test_hf_models_missing_required_fields(self, tmp_path: Path):
        """When hfModels entry is missing required fields, validation fails."""

        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "test/repo",
                                # Missing: file, dir
                            },
                        },
                        "flags": [],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("file" in e and "must not be null" in e for e in errors)
        assert any("dir" in e and "must not be null" in e for e in errors)

    def test_hf_models_null_required_field(self, tmp_path: Path):
        """When hfModels entry has null required field, validation fails."""

        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "serverCustomParameters": {
                        "hfModels": {
                            "main": {
                                "fileFlag": None,
                                "repo": "test/repo",
                                "file": "model.gguf",
                                "dir": "model-dir",
                            },
                        },
                        "flags": [],
                    },
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("fileFlag" in e and "must not be null" in e for e in errors)

    def test_hf_models_not_a_dict(self, tmp_path: Path):
        """When hfModels is not a dict, validation fails."""
        import json

        from config_schema import validate_models

        pi_container = tmp_path / ".pi-container"
        agent_dir = pi_container / "agent"
        agent_dir.mkdir(parents=True)
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {
                            "serverCustomParameters": {
                                "flags": [],
                                "hfModels": "not-a-dict",
                            },
                        },
                    },
                },
            )
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("expected dict, got str" in e for e in errors)

    def test_hf_models_invalid_field_type(self, tmp_path: Path):
        """When a required hfModels field has wrong type, validation fails."""
        import json

        from config_schema import validate_models

        pi_container = tmp_path / ".pi-container"
        agent_dir = pi_container / "agent"
        agent_dir.mkdir(parents=True)
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {
                            "serverCustomParameters": {
                                "flags": [],
                                "hfModels": {
                                    "main": {
                                        "fileFlag": "--model",
                                        "repo": "test/repo",
                                        "file": 42,  # should be string
                                        "dir": "model-dir",
                                    },
                                },
                            },
                        },
                    },
                },
            )
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("expected str, got int" in e for e in errors)

    def test_hf_models_model_config_not_dict(self, tmp_path: Path):
        """When a model config is not a dict, validation reports error."""
        import json

        from config_schema import validate_models

        pi_container = tmp_path / ".pi-container"
        agent_dir = pi_container / "agent"
        agent_dir.mkdir(parents=True)
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {
                            "serverCustomParameters": {
                                "flags": [],
                                "hfModels": {
                                    "main": "not-a-dict",
                                },
                            },
                        },
                    },
                },
            )
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("expected dict, got str" in e for e in errors)

    def test_flags_with_non_string_item(self, tmp_path: Path):
        """When flags contain non-str/int/float items, validation fails."""
        import json

        from config_schema import validate_models

        pi_container = tmp_path / ".pi-container"
        agent_dir = pi_container / "agent"
        agent_dir.mkdir(parents=True)
        models_path = agent_dir / "models.json"
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {
                            "serverCustomParameters": {
                                "flags": [
                                    "--ctx-size",
                                    4096,
                                    None,  # invalid
                                ],
                            },
                        },
                    },
                },
            )
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("expected str/int/float, got NoneType" in e for e in errors)


class TestSchemaCommonDirect:
    """Direct tests for schema_common helpers (bypassing validate_models)."""

    def test_validate_models_flags_with_invalid_item(self):
        """When flags contain non-str/int/float items, validation fails."""
        from schema_common import _validate_models_flags

        errors = _validate_models_flags(["--ctx-size", 4096, None], "test.flags")
        assert len(errors) == 1
        assert "expected str/int/float, got NoneType" in errors[0]

    def test_validate_hf_models_with_non_dict(self):
        """When hfModels is not a dict, validation fails."""
        from schema_common import _validate_hf_models

        errors = _validate_hf_models("not-a-dict", "test-provider")
        assert len(errors) == 1
        assert "expected dict, got str" in errors[0]

    def test_validate_hf_models_with_none(self):
        """When hfModels is None, validation fails."""
        from schema_common import _validate_hf_models

        errors = _validate_hf_models(None, "test-provider")
        assert len(errors) == 1
        assert "must not be null" in errors[0]

    def test_validate_provider_base_urls_valid_multiple(self):
        """When multiple local providers have valid distinct ports, validation passes."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "baseUrl": "http://llama:9999/v1",
                "serverCustomParameters": {"hfModels": {}},
            },
            "local-gemma": {
                "baseUrl": "http://local-gemma:9998/v1",
                "serverCustomParameters": {"hfModels": {}},
            },
            "anthropic": {
                "baseUrl": "https://api.anthropic.com/v1",
            },
        }
        errors = _validate_provider_base_urls(providers)
        assert errors == []

    def test_validate_provider_base_urls_missing(self):
        """When local provider is missing baseUrl, validation fails."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "serverCustomParameters": {"hfModels": {}},
            }
        }
        errors = _validate_provider_base_urls(providers)
        assert len(errors) == 1
        assert "required field missing" in errors[0]

    def test_validate_provider_base_urls_non_string(self):
        """When baseUrl is not a string, validation fails."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "baseUrl": 12345,
                "serverCustomParameters": {"hfModels": {}},
            }
        }
        errors = _validate_provider_base_urls(providers)
        assert len(errors) == 1
        assert "must be a non-empty string URL" in errors[0]

    def test_validate_provider_base_urls_invalid_scheme(self):
        """When baseUrl has invalid scheme, validation fails."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "baseUrl": "ftp://llama:9999/v1",
                "serverCustomParameters": {"hfModels": {}},
            }
        }
        errors = _validate_provider_base_urls(providers)
        assert len(errors) == 1
        assert "must start with http:// or https://" in errors[0]

    def test_validate_provider_base_urls_missing_port(self):
        """When baseUrl has no port, validation fails."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "baseUrl": "http://llama/v1",
                "serverCustomParameters": {"hfModels": {}},
            }
        }
        errors = _validate_provider_base_urls(providers)
        assert len(errors) == 1
        assert "port is required" in errors[0]

    def test_validate_provider_base_urls_localhost_rejected(self):
        """When baseUrl uses localhost or 127.0.0.1, validation fails with helpful message."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "baseUrl": "http://localhost:9999/v1",
                "serverCustomParameters": {"hfModels": {}},
            },
            "local-gemma": {
                "baseUrl": "http://127.0.0.1:9998/v1",
                "serverCustomParameters": {"hfModels": {}},
            },
        }
        errors = _validate_provider_base_urls(providers)
        assert len(errors) == 2
        assert any("cannot be reached from inside the container" in e and "localhost" in e for e in errors)
        assert any("cannot be reached from inside the container" in e and "127.0.0.1" in e for e in errors)

    def test_validate_provider_base_urls_duplicate_port(self):
        """When multiple local providers share the same container port, validation fails."""
        from schema_common import _validate_provider_base_urls

        providers = {
            "local-ornith": {
                "baseUrl": "http://llama:9999/v1",
                "serverCustomParameters": {"hfModels": {}},
            },
            "local-gemma": {
                "baseUrl": "http://llama:9999/v1",
                "serverCustomParameters": {"hfModels": {}},
            },
        }
        errors = _validate_provider_base_urls(providers)
        assert len(errors) == 1
        assert "Duplicate container port 9999" in errors[0]
        assert "local-ornith" in errors[0] and "local-gemma" in errors[0]


class TestVolumesSchema:
    def test_valid_volumes_config(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["volumes"] = {"paths": ["/workspace/node_modules", "/workspace/.venv"]}
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

    def test_invalid_volumes_type(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["volumes"] = "invalid_string"
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("volumes" in e for e in errors)

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
                    "models": [
                        {
                            "id": "model-1",
                            "name": "Model 1",
                        }
                    ],
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
                    "models": [{"id": "ornith"}],
                },
                "local-gemma": {
                    "baseUrl": "http://gemma-server:9998/v1",
                    "models": [{"id": "gemma"}],
                },
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is True
        assert errors == []

    def test_file_not_found(self, tmp_path: Path):
        """When models.json does not exist, validation fails."""
        from config_schema import validate_models

        is_valid, errors = validate_models(tmp_path / "missing.json")
        assert is_valid is False
        assert any("not found" in e for e in errors)

    def test_invalid_json(self, tmp_path: Path):
        """When models.json has syntax errors, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        p.write_text("{invalid")
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("not valid JSON" in e for e in errors)

    def test_non_dict_json(self, tmp_path: Path):
        """When models.json is not a dict, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        p.write_text("[]")
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("must be a JSON object" in e for e in errors)

    def test_missing_providers_key(self, tmp_path: Path):
        """When models.json has no providers key, validation fails."""
        import json

        from config_schema import validate_models

        p = tmp_path / "models.json"
        p.write_text(json.dumps({}))
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("providers: required field missing" in e for e in errors)

    def test_non_dict_provider(self, tmp_path: Path):
        """When a provider is not a dict, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(tmp_path, {"local-test": "not-a-dict"})
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("expected dict, got str" in e for e in errors)

    def test_server_custom_parameters_rejected(self, tmp_path: Path):
        """When a provider contains legacy serverCustomParameters, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path,
            {
                "local-test": {
                    "baseUrl": "http://llama:9999/v1",
                    "models": [{"id": "test"}],
                    "serverCustomParameters": {},
                }
            },
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("serverCustomParameters: obsolete and unsupported" in e for e in errors)

    def test_missing_models_key(self, tmp_path: Path):
        """When a provider has no models key, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(tmp_path, {"local-test": {"baseUrl": "http://llama:9999/v1"}})
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("models: required field missing" in e for e in errors)

    def test_models_not_a_list(self, tmp_path: Path):
        """When a provider models field is not a list, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path, {"local-test": {"baseUrl": "http://llama:9999/v1", "models": "not-a-list"}}
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("models: expected list" in e for e in errors)

    def test_models_empty_list(self, tmp_path: Path):
        """When a provider models list is empty, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(tmp_path, {"local-test": {"baseUrl": "http://llama:9999/v1", "models": []}})
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("models: must not be empty" in e for e in errors)

    def test_model_item_not_dict(self, tmp_path: Path):
        """When a model item is not a dict, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path, {"local-test": {"baseUrl": "http://llama:9999/v1", "models": ["not-a-dict"]}}
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("models[0]: expected dict" in e for e in errors)

    def test_model_item_missing_id(self, tmp_path: Path):
        """When a model item is missing id, validation fails."""
        from config_schema import validate_models

        models_path = self._write_models(
            tmp_path, {"local-test": {"baseUrl": "http://llama:9999/v1", "models": [{"name": "Missing ID"}]}}
        )
        is_valid, errors = validate_models(models_path)
        assert is_valid is False
        assert any("models[0].id: required non-empty string missing" in e for e in errors)


class TestModelsBaseUrls:
    """Tests for baseUrl validation on providers."""

    def test_missing_base_url_for_local_provider(self, tmp_path: Path):
        """When a local provider lacks baseUrl, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(json.dumps({"providers": {"local-test": {"models": [{"id": "m1"}]}}}))
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("baseUrl: required field missing for local provider" in e for e in errors)

    def test_non_string_base_url(self, tmp_path: Path):
        """When baseUrl is not a string, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(json.dumps({"providers": {"local-test": {"baseUrl": 12345, "models": [{"id": "m1"}]}}}))
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("baseUrl: must be a non-empty string URL" in e for e in errors)

    def test_invalid_scheme(self, tmp_path: Path):
        """When baseUrl has invalid scheme, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(
            json.dumps({"providers": {"local-test": {"baseUrl": "ftp://llama:9999/v1", "models": [{"id": "m1"}]}}})
        )
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("must start with http:// or https://" in e for e in errors)

    def test_missing_port(self, tmp_path: Path):
        """When local provider baseUrl has no port, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(
            json.dumps({"providers": {"local-test": {"baseUrl": "http://llama/v1", "models": [{"id": "m1"}]}}})
        )
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("baseUrl: port is required" in e for e in errors)

    def test_invalid_port_range(self, tmp_path: Path):
        """When baseUrl port is out of range, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(
            json.dumps({"providers": {"local-test": {"baseUrl": "http://llama:99999/v1", "models": [{"id": "m1"}]}}})
        )
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("invalid port" in e for e in errors)

    def test_localhost_rejected(self, tmp_path: Path):
        """When baseUrl uses localhost, 127.0.0.1, or ::1, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-test": {"baseUrl": "http://localhost:9999/v1", "models": [{"id": "m1"}]},
                        "local-test2": {"baseUrl": "http://127.0.0.1:9998/v1", "models": [{"id": "m2"}]},
                    }
                }
            )
        )
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("cannot be reached from inside the container" in e and "localhost" in e for e in errors)
        assert any("cannot be reached from inside the container" in e and "127.0.0.1" in e for e in errors)

    def test_duplicate_port_rejected(self, tmp_path: Path):
        """When two local providers use the same container port, validation fails."""
        from config_schema import validate_models

        p = tmp_path / "models.json"
        import json

        p.write_text(
            json.dumps(
                {
                    "providers": {
                        "local-p1": {"baseUrl": "http://llama:9999/v1", "models": [{"id": "m1"}]},
                        "local-p2": {"baseUrl": "http://llama:9999/v1", "models": [{"id": "m2"}]},
                    }
                }
            )
        )
        is_valid, errors = validate_models(p)
        assert is_valid is False
        assert any("Duplicate container port 9999" in e for e in errors)

    def test_gateway_provider_can_omit_port(self, tmp_path: Path):
        """An external gateway provider is not required to specify a custom container port."""
        from config_schema import validate_models
        from registry import GatewayDef

        p = tmp_path / "models.json"
        import json

        p.write_text(
            json.dumps(
                {
                    "providers": {
                        "opper": {
                            "baseUrl": "https://api.opper.ai/v3/compat",
                            "models": [{"id": "claude"}],
                        }
                    }
                }
            )
        )
        mock_gw = GatewayDef(id="opper", base_url="https://api.opper.ai/v3/compat")
        with patch("registry.load_gateway_def", return_value=mock_gw):
            is_valid, errors = validate_models(p)
        assert is_valid is True
        assert errors == []


class TestModelDefinitionValidation:
    """Tests for validate_model_def_data in schema_common.py."""

    def test_valid_model_def_data(self):
        from schema_common import validate_model_def_data

        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    "repo": "org/repo",
                    "file": "model.gguf",
                }
            },
            "flags": ["--ctx-size", 4096],
        }
        errors = validate_model_def_data(data, model_id="test-model")
        assert errors == []

    def test_hf_models_null(self):
        from schema_common import validate_model_def_data

        errors = validate_model_def_data({"hfModels": None}, model_id="test")
        assert any("hfModels must not be null" in e for e in errors)

    def test_hf_models_not_dict(self):
        from schema_common import validate_model_def_data

        errors = validate_model_def_data({"hfModels": "not-a-dict"}, model_id="test")
        assert any("hfModels expected dict" in e for e in errors)

    def test_hf_models_empty(self):
        from schema_common import validate_model_def_data

        errors = validate_model_def_data({"hfModels": {}}, model_id="test")
        assert any("hfModels must not be empty" in e for e in errors)

    def test_hf_models_missing_main(self):
        from schema_common import validate_model_def_data

        errors = validate_model_def_data({"hfModels": {"draft": {}}}, model_id="test")
        assert any("hfModels.main is required" in e for e in errors)

    def test_hf_models_missing_required_fields(self):
        from schema_common import validate_model_def_data

        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    # Missing: repo, file
                }
            }
        }
        errors = validate_model_def_data(data, model_id="test")
        assert any("repo must be a non-empty string" in e for e in errors)
        assert any("file must be a non-empty string" in e for e in errors)

    def test_flags_invalid_item_type(self):
        from schema_common import validate_model_def_data

        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    "repo": "r",
                    "file": "f",
                }
            },
            "flags": ["--ctx-size", 4096, None],
        }
        errors = validate_model_def_data(data, model_id="test")
        assert any("flags[2] expected str/int/float, got NoneType" in e for e in errors)


class TestChatTemplatePaths:
    """Tests for check_chat_template_paths and resolve_chat_template_path in template_paths.py."""

    def test_valid_chat_template_path(self, tmp_path: Path):
        from template_paths import check_chat_template_paths

        template_file = tmp_path / "template.jinja"
        template_file.write_text("jinja")

        flags = ["--chat-template-file", str(template_file)]
        errors = check_chat_template_paths(flags, repo_root=tmp_path)
        assert errors == []

    def test_relative_chat_template_path(self, tmp_path: Path):
        from template_paths import check_chat_template_paths

        rel_path = "templates/chat.jinja"
        full_path = tmp_path / rel_path
        full_path.parent.mkdir(parents=True)
        full_path.write_text("jinja")

        flags = ["--chat-template-file", rel_path]
        errors = check_chat_template_paths(flags, repo_root=tmp_path)
        assert errors == []

    def test_missing_path_value(self, tmp_path: Path):
        from template_paths import check_chat_template_paths

        flags = ["--chat-template-file"]
        errors = check_chat_template_paths(flags, repo_root=tmp_path)
        assert any("has no following path" in e for e in errors)

    def test_non_string_path(self, tmp_path: Path):
        from template_paths import check_chat_template_paths

        flags = ["--chat-template-file", 12345]
        errors = check_chat_template_paths(flags, repo_root=tmp_path)
        assert any("path is not a string" in e for e in errors)

    def test_nonexistent_file(self, tmp_path: Path):
        from template_paths import check_chat_template_paths

        flags = ["--chat-template-file", "missing/chat.jinja"]
        errors = check_chat_template_paths(flags, repo_root=tmp_path)
        assert any("path does not exist" in e for e in errors)


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


class TestAgentReadOnlyPiContainerSchema:
    def test_valid_read_only_pi_container_boolean(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["agent"]["read_only_pi_container"] = True
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

    def test_valid_read_only_pi_container_string_and_int(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["agent"]["read_only_pi_container"] = "false"
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

        data["agent"]["read_only_pi_container"] = 0
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

    def test_invalid_read_only_pi_container_type(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["agent"]["read_only_pi_container"] = ["not", "a", "bool"]
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("read_only_pi_container" in e for e in errors)


class TestSecuritySchema:
    def test_valid_security_section(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["security"] = {
            "read_only_git_hooks": True,
            "blocked_mount_paths": ["~/.ssh", "~/.aws"],
            "blocked_ip_ranges": ["127.0.0.0/8", "10.0.0.0/8"],
            "git_config_allowlist": ["user.name", "user.email"],
        }
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

    def test_invalid_security_types(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["security"] = {
            "read_only_git_hooks": ["not_a_bool"],
            "blocked_mount_paths": "not_a_list",
        }
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("read_only_git_hooks" in e for e in errors)
        assert any("blocked_mount_paths" in e for e in errors)


class TestToolsSchema:
    def test_valid_tools_section(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["tools"] = {
            "include": ["go", "rust"],
        }
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

    def test_empty_tools_include(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["tools"] = {
            "include": [],
        }
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is True
        assert errors == []

    def test_unknown_tool_rejected(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["tools"] = {
            "include": ["unknown_lang"],
        }
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("unknown tool 'unknown_lang'" in e for e in errors)

    def test_invalid_tools_types(self, valid_config: Path):
        from config_schema import validate_config

        with valid_config.open("r") as f:
            data = yaml.safe_load(f)
        data["tools"] = {
            "include": "not_a_list",
        }
        valid_config.write_text(yaml.dump(data))

        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("tools.include" in e for e in errors)

        data["tools"] = {
            "include": [123],
        }
        valid_config.write_text(yaml.dump(data))
        with patch("config_schema.get_app_version", return_value=None):
            is_valid, errors, _ = validate_config(valid_config)
        assert is_valid is False
        assert any("expected string tool name" in e for e in errors)

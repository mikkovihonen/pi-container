from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import yaml

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from source_control import (
    mask_token,
    read_source_control_config,
    resolve_source_control_tokens,
)


class TestMaskToken:
    def test_empty_token(self):
        assert mask_token("") == ""

    def test_short_token(self):
        assert mask_token("abc") == "***"
        assert mask_token("12345678") == "***"

    def test_long_token(self):
        masked = mask_token("gho_1234567890abcdef")
        assert masked == "gho_...cdef"


class TestReadSourceControlConfig:
    def test_defaults_when_omitted(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("{}\n")
        cfg = read_source_control_config(tmp_path)
        assert cfg["enabled"] is False
        assert cfg["auto_detect_host_cli"] is False
        assert cfg["setup_git_credentials"] is True
        assert cfg["providers"] == {}

    def test_explicit_settings(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            yaml.dump(
                {
                    "source_control": {
                        "enabled": True,
                        "auto_detect_host_cli": True,
                        "setup_git_credentials": False,
                        "providers": {"github": {"token": "ghp_123"}},
                    }
                }
            )
        )
        cfg = read_source_control_config(tmp_path)
        assert cfg["enabled"] is True
        assert cfg["auto_detect_host_cli"] is True
        assert cfg["setup_git_credentials"] is False
        assert cfg["providers"] == {"github": {"token": "ghp_123"}}

    def test_string_boolean_settings(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            yaml.dump(
                {
                    "source_control": {
                        "enabled": "0",
                        "auto_detect_host_cli": "1",
                        "setup_git_credentials": "false",
                    }
                }
            )
        )
        cfg = read_source_control_config(tmp_path)
        assert cfg["enabled"] is False
        assert cfg["auto_detect_host_cli"] is True
        assert cfg["setup_git_credentials"] is False


class TestResolveSourceControlTokens:
    def test_returns_empty_when_disabled(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(yaml.dump({"source_control": {"enabled": False}}))
        tokens = resolve_source_control_tokens(tmp_path)
        assert tokens == {}

    def test_ignores_host_environment_and_agent_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Host environment variables and agent.env must NEVER be accepted as credential storage
        (tmp_path / "config.yaml").write_text(
            yaml.dump(
                {
                    "source_control": {"enabled": True},
                    "agent": {
                        "env": {
                            "GH_TOKEN": "gho_untrusted_agent_env",
                            "GITLAB_TOKEN": "glpat_untrusted_agent_env",
                        }
                    },
                }
            )
        )
        monkeypatch.setenv("GH_TOKEN", "gho_untrusted_env")
        monkeypatch.setenv("GITHUB_TOKEN", "gho_untrusted_env")
        monkeypatch.setenv("GITEA_TOKEN", "gtea_untrusted_env")
        monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "az_untrusted_env")

        with patch("source_control.get_keyring_secret", return_value=None):
            tokens = resolve_source_control_tokens(tmp_path)
        assert tokens == {}

    def test_resolves_from_os_keyring(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(yaml.dump({"source_control": {"enabled": True}}))

        def mock_get_keyring(account, service="pi-container"):
            if account == "GH_TOKEN":
                return "gho_keyring_secret_token"
            if account == "GITLAB_TOKEN":
                return "glpat_keyring_secret_token"
            if account == "GITEA_TOKEN":
                return "gtea_keyring_secret_token"
            if account == "AZURE_DEVOPS_EXT_PAT":
                return "az_keyring_secret_token"
            if account == "BITBUCKET_TOKEN":
                return "bitbucket_keyring_secret_token"
            if account == "GITCAFE_TOKEN":
                return "cafe_keyring_secret_token"
            return None

        with patch("source_control.get_keyring_secret", side_effect=mock_get_keyring):
            tokens = resolve_source_control_tokens(tmp_path)

        assert tokens["GH_TOKEN"] == "gho_keyring_secret_token"
        assert tokens["GITHUB_TOKEN"] == "gho_keyring_secret_token"
        assert tokens["GITLAB_TOKEN"] == "glpat_keyring_secret_token"
        assert tokens["GLAB_TOKEN"] == "glpat_keyring_secret_token"
        assert tokens["GITEA_TOKEN"] == "gtea_keyring_secret_token"
        assert tokens["TEA_TOKEN"] == "gtea_keyring_secret_token"
        assert tokens["AZURE_DEVOPS_EXT_PAT"] == "az_keyring_secret_token"
        assert tokens["AZURE_DEVOPS_TOKEN"] == "az_keyring_secret_token"
        assert tokens["BITBUCKET_TOKEN"] == "bitbucket_keyring_secret_token"
        assert tokens["GITCAFE_TOKEN"] == "cafe_keyring_secret_token"
        assert tokens["CAFE_TOKEN"] == "cafe_keyring_secret_token"

    def test_resolves_custom_keyring_var(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            yaml.dump(
                {
                    "source_control": {
                        "enabled": True,
                        "providers": {
                            "github": {
                                "keyring_var": "PI_CONTAINER_T3_GH_TOKEN",
                            }
                        },
                    }
                }
            )
        )

        def mock_get_keyring(account, service="pi-container"):
            if account == "PI_CONTAINER_T3_GH_TOKEN":
                return "gho_custom_keyring_token"
            return None

        with patch("source_control.get_keyring_secret", side_effect=mock_get_keyring):
            tokens = resolve_source_control_tokens(tmp_path)

        assert tokens["GH_TOKEN"] == "gho_custom_keyring_token"
        assert tokens["GITHUB_TOKEN"] == "gho_custom_keyring_token"

    def test_resolves_custom_keyring_var_list(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            yaml.dump(
                {
                    "source_control": {
                        "enabled": True,
                        "providers": {
                            "github": {
                                "keyring_var": ["NONEXISTENT_KEY", "BACKUP_GH_TOKEN"],
                            }
                        },
                    }
                }
            )
        )

        def mock_get_keyring(account, service="pi-container"):
            if account == "BACKUP_GH_TOKEN":
                return "gho_backup_token"
            return None

        with patch("source_control.get_keyring_secret", side_effect=mock_get_keyring):
            tokens = resolve_source_control_tokens(tmp_path)

        assert tokens["GH_TOKEN"] == "gho_backup_token"

    def test_custom_keyring_var_does_not_fallback_to_default(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            yaml.dump(
                {
                    "source_control": {
                        "enabled": True,
                        "providers": {
                            "github": {
                                "keyring_var": "PROJECT_SCOPED_TOKEN",
                            }
                        },
                    }
                }
            )
        )

        def mock_get_keyring(account, service="pi-container"):
            # Global token exists, but project-scoped token does NOT exist
            if account in ("GH_TOKEN", "GITHUB_TOKEN"):
                return "gho_global_token_that_must_not_leak"
            return None

        with patch("source_control.get_keyring_secret", side_effect=mock_get_keyring):
            tokens = resolve_source_control_tokens(tmp_path)

        assert "GH_TOKEN" not in tokens
        assert "GITHUB_TOKEN" not in tokens

    def test_cli_probe_skipped_when_auto_detect_false(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(yaml.dump({"source_control": {"auto_detect_host_cli": False}}))

        with (
            patch("source_control.get_keyring_secret", return_value=None),
            patch("source_control._probe_host_cli") as mock_probe,
        ):
            tokens = resolve_source_control_tokens(tmp_path)
            assert mock_probe.call_count == 0
            assert "GH_TOKEN" not in tokens

    def test_cli_probe_called_when_auto_detect_true(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            yaml.dump({"source_control": {"enabled": True, "auto_detect_host_cli": True}})
        )

        def mock_probe(cmd):
            if cmd == ("gh", "auth", "token"):
                return "gho_from_host_cli"
            return None

        with (
            patch("source_control.get_keyring_secret", return_value=None),
            patch("source_control._probe_host_cli", side_effect=mock_probe),
        ):
            tokens = resolve_source_control_tokens(tmp_path)
            assert tokens["GH_TOKEN"] == "gho_from_host_cli"
            assert tokens["GITHUB_TOKEN"] == "gho_from_host_cli"

    def test_probe_host_cli_helper(self):
        from source_control import _probe_host_cli

        with patch("shutil.which", return_value=None):
            assert _probe_host_cli(("gh", "auth", "token")) is None

        with patch("shutil.which", return_value="/usr/bin/gh"):
            mock_res = MagicMock(returncode=0, stdout="my_cli_token\n")
            with patch("subprocess.run", return_value=mock_res):
                assert _probe_host_cli(("gh", "auth", "token")) == "my_cli_token"

            mock_fail = MagicMock(returncode=1, stdout="")
            with patch("subprocess.run", return_value=mock_fail):
                assert _probe_host_cli(("gh", "auth", "token")) is None

            with patch("subprocess.run", side_effect=Exception("error")):
                assert _probe_host_cli(("gh", "auth", "token")) is None

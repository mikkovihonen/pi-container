"""
Unit tests for src/network.py

Run with:
    python -m pytest src/tests/test_network.py -v
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from network import ContainerNetworkManager, scan_config_env_refs

# ---------------------------------------------------------------------------
# ScanConfigEnvRefs
# ---------------------------------------------------------------------------


class TestScanConfigEnvRefs:
    def test_finds_env_refs(self):
        config = {"rules": [{"replace_with": {"value": "${ENV:API_KEY}"}}]}
        result = scan_config_env_refs(config)
        assert result == ["API_KEY"]

    def test_finds_multiple_refs(self):
        config = {
            "rules": [
                {"replace_with": {"value": "${ENV:KEY1}"}},
                {"replace_with": {"value": "${ENV:KEY2}"}},
            ]
        }
        result = scan_config_env_refs(config)
        assert result == ["KEY1", "KEY2"]

    def test_deduplicates_refs(self):
        config = {
            "rules": [
                {"replace_with": {"value": "${ENV:KEY}"}},
                {"replace_with": {"value": "${ENV:KEY}"}},
            ]
        }
        result = scan_config_env_refs(config)
        assert result == ["KEY"]

    def test_no_refs(self):
        config = {
            "rules": [
                {"replace_with": {"value": "literal_value"}},
            ]
        }
        result = scan_config_env_refs(config)
        assert result == []

    def test_empty_rules(self):
        assert scan_config_env_refs({}) == []

    def test_no_rules_key(self):
        assert scan_config_env_refs({"other_key": "value"}) == []

    def test_no_replace_with(self):
        config = {"rules": [{"name": "test"}]}
        result = scan_config_env_refs(config)
        assert result == []

    def test_no_value_key(self):
        config = {"rules": [{"replace_with": {}}]}
        result = scan_config_env_refs(config)
        assert result == []

    def test_empty_value(self):
        config = {"rules": [{"replace_with": {"value": ""}}]}
        result = scan_config_env_refs(config)
        assert result == []

    def test_env_ref_with_comma_no_default(self):
        """${ENV:VAR} with no comma is a required ref."""
        config = {"rules": [{"replace_with": {"value": "${ENV:SOME_VAR}"}}]}
        result = scan_config_env_refs(config)
        assert result == ["SOME_VAR"]

    def test_nested_in_non_replace_with(self):
        """Refs outside replace_with.value should be ignored."""
        config = {"rules": [{"name": "${ENV:IGNORED}", "replace_with": {"value": "safe"}}]}
        result = scan_config_env_refs(config)
        assert result == []

    def test_partial_match_not_found(self):
        """${ENV:VAR} with extra text should not match."""
        config = {"rules": [{"replace_with": {"value": "prefix_${ENV:VAR}_suffix"}}]}
        result = scan_config_env_refs(config)
        assert result == []

    def test_returns_sorted(self):
        config = {
            "rules": [
                {"replace_with": {"value": "${ENV:CHARLIE}"}},
                {"replace_with": {"value": "${ENV:ALPHA}"}},
                {"replace_with": {"value": "${ENV:BETA}"}},
            ]
        }
        result = scan_config_env_refs(config)
        assert result == ["ALPHA", "BETA", "CHARLIE"]


# ---------------------------------------------------------------------------
# ContainerNetworkManagerPullSecrets
# ---------------------------------------------------------------------------


class TestContainerNetworkManagerPullSecrets:
    def _make_manager(self, tmp_path):
        return ContainerNetworkManager(
            container_runtime="docker",
            network_name="test-net",
            proxy_image="proxy:latest",
            config_dir=tmp_path,
        )

    def test_no_config_file_returns_empty(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        secrets = mgr._pull_secrets_from_config()
        assert secrets == {}

    def test_reads_env_refs_from_config(self, tmp_path):
        config = {"rules": [{"replace_with": {"value": "${ENV:MY_SECRET}"}}]}
        config_file = tmp_path / "token_replacer.yaml"
        import yaml

        config_file.write_text(yaml.dump(config))

        with patch.dict(os.environ, {"MY_SECRET": "s3cret"}):
            mgr = self._make_manager(tmp_path)
            secrets = mgr._pull_secrets_from_config()

        assert secrets == {"MY_SECRET": "s3cret"}

    def test_skips_missing_env_vars(self, tmp_path):
        config = {"rules": [{"replace_with": {"value": "${ENV:MISSING_VAR}"}}]}
        config_file = tmp_path / "token_replacer.yaml"
        import yaml

        config_file.write_text(yaml.dump(config))

        with patch.dict(os.environ, {}, clear=True):
            mgr = self._make_manager(tmp_path)
            secrets = mgr._pull_secrets_from_config()

        assert secrets == {}


# ---------------------------------------------------------------------------
# ContainerNetworkManagerEnvFlags
# ---------------------------------------------------------------------------


class TestContainerNetworkManagerEnvFlags:
    def test_env_flags_sorted(self):
        mgr = ContainerNetworkManager(
            container_runtime="docker",
            network_name="test-net",
            proxy_image="proxy:latest",
        )
        flags = mgr._env_flags({"ZEBRA": "z", "ALPHA": "a", "MID": "m"})
        expected = ["--env", "ALPHA=a", "--env", "MID=m", "--env", "ZEBRA=z"]
        assert flags == expected

    def test_empty_secrets(self):
        mgr = ContainerNetworkManager(
            container_runtime="docker",
            network_name="test-net",
            proxy_image="proxy:latest",
        )
        flags = mgr._env_flags({})
        assert flags == []


# ---------------------------------------------------------------------------
# ContainerNetworkManagerRefCount
# ---------------------------------------------------------------------------


class TestContainerNetworkManagerRefCount:
    def _make_manager(self, tmp_path):
        lock_dir = tmp_path / ".locks"
        lock_dir.mkdir()
        mgr = ContainerNetworkManager(
            container_runtime="docker",
            network_name="test-net",
            proxy_image="proxy:latest",
            config_dir=tmp_path,
        )
        mgr.paths = {
            "lock_dir": lock_dir,
            "ref_count_lock": lock_dir / ".network_manager.lock",
            "ref_count_file": lock_dir / ".network_manager.refcount",
        }
        return mgr

    def test_ref_count_zero_when_no_file(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr._get_ref_count() == 0

    def test_ref_count_reads_file(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.paths["ref_count_file"].write_text("5\n")
        assert mgr._get_ref_count() == 5

    def test_ref_count_handles_invalid_content(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.paths["ref_count_file"].write_text("not_a_number\n")
        assert mgr._get_ref_count() == 0

    def test_start_increment_and_stop_decrement(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.paths["lock_dir"].mkdir(parents=True, exist_ok=True)

        # Mock _actually_start and _actually_stop
        mgr._actually_start = MagicMock()
        mgr._actually_stop = MagicMock()

        # First start
        with patch("fcntl.flock"):
            mgr.start()
        assert mgr._actually_start.called

        # Second start (increment only)
        with patch("fcntl.flock"):
            mgr.start()
        assert mgr._actually_start.call_count == 1  # not called again

        # First stop (decrement only)
        with patch("fcntl.flock"):
            mgr.stop()
        assert mgr._actually_stop.call_count == 0

        # Second stop (actual cleanup)
        with patch("fcntl.flock"):
            mgr.stop()
        assert mgr._actually_stop.call_count == 1

    def test_cleanup_after_full_stop(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr._actually_start = MagicMock()
        mgr._actually_stop = MagicMock()

        with patch("fcntl.flock"):
            mgr.start()
        with patch("fcntl.flock"):
            mgr.stop()

        # Ref count file should be removed
        assert not mgr.paths["ref_count_file"].exists()

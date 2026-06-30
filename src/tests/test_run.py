"""
Unit tests for src/run.py

Run with:
    python -m pytest src/tests/test_run.py -v
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run import (
    Model,
    ModelConfig,
    Server,
    ServerConfig,
    ContainerNetworkManager,
    scan_config_env_refs,
)
from util import EnvironmentError


# ---------------------------------------------------------------------------
# scan_config_env_refs
# ---------------------------------------------------------------------------


class TestScanConfigEnvRefs:
    def test_finds_env_refs(self):
        config = {
            "rules": [
                {
                    "replace_with": {"value": "${ENV:API_KEY}"}
                }
            ]
        }
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
        config = {
            "rules": [
                {"name": "${ENV:IGNORED}", "replace_with": {"value": "safe"}}
            ]
        }
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
# ModelConfig.from_dict
# ---------------------------------------------------------------------------


class TestModelConfig:
    def _make_dict(self, **overrides):
        base = {
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": None,
        }
        base.update(overrides)
        return base

    def test_basic_from_dict(self):
        mc = ModelConfig.from_dict(self._make_dict())
        assert mc.file_flag == "--model"
        assert mc.repo == "org/repo"
        assert mc.file == "model.gguf"
        assert mc.directory == Path("models")
        assert mc.additional_server_flags == []
        assert mc.sha256 is None

    def test_sha256_passed_through(self):
        sha = "abc123def456"
        mc = ModelConfig.from_dict(self._make_dict(sha256=sha))
        assert mc.sha256 == sha

    def test_sha256_none_when_missing(self):
        d = self._make_dict()
        del d["sha256"]
        mc = ModelConfig.from_dict(d)
        assert mc.sha256 is None

    def test_additional_flags_from_dict(self):
        d = self._make_dict(additionalServerFlags=["--n-gpu-layers", "35"])
        mc = ModelConfig.from_dict(d)
        assert mc.additional_server_flags == ["--n-gpu-layers", "35"]

    def test_additional_flags_default_empty(self):
        d = self._make_dict()
        del d["additionalServerFlags"]
        mc = ModelConfig.from_dict(d)
        assert mc.additional_server_flags == []

    def test_is_frozen(self):
        mc = ModelConfig.from_dict(self._make_dict())
        with pytest.raises(AttributeError):
            mc.file = "other"


# ---------------------------------------------------------------------------
# ServerConfig.from_dict
# ---------------------------------------------------------------------------


class TestServerConfig:
    def test_from_dict_basic(self):
        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    "repo": "org/repo",
                    "file": "model.gguf",
                    "dir": "models",
                    "additionalServerFlags": [],
                }
            },
            "flags": ["--ctx-size", "4096"],
        }
        sc = ServerConfig.from_dict(data)
        assert "main" in sc.hf_models
        assert sc.hf_models["main"].repo == "org/repo"
        assert sc.flags == ["--ctx-size", "4096"]

    def test_from_dict_multiple_models(self):
        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    "repo": "org/main",
                    "file": "main.gguf",
                    "dir": "models",
                    "additionalServerFlags": [],
                },
                "embedding": {
                    "fileFlag": "--embedding-model",
                    "repo": "org/embed",
                    "file": "embed.gguf",
                    "dir": "embedding",
                    "additionalServerFlags": [],
                },
            },
            "flags": [],
        }
        sc = ServerConfig.from_dict(data)
        assert "main" in sc.hf_models
        assert "embedding" in sc.hf_models
        assert sc.hf_models["embedding"].file == "embed.gguf"

    def test_from_dict_no_hf_models(self):
        sc = ServerConfig.from_dict({"flags": []})
        assert sc.hf_models == {}

    def test_from_dict_no_flags(self):
        data = {"hfModels": {}}
        sc = ServerConfig.from_dict(data)
        assert sc.flags == []

    def test_is_frozen(self):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        with pytest.raises(AttributeError):
            sc.flags = ["new"]


# ---------------------------------------------------------------------------
# Model._verify_sha256
# ---------------------------------------------------------------------------


class TestModelVerifySha256:
    def test_no_sha256_skips_verification(self):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": None,
        })
        model = Model(label="test", config=mc, models_dir=Path("/tmp"))
        # Should not raise
        with patch("run.logger") as mock_logger:
            model._verify_sha256()
        mock_logger.warning.assert_called()

    def test_matching_sha256_passes(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": "abc123",
        })
        model = Model(label="test", config=mc, models_dir=tmp_path)
        model_path = tmp_path / "models" / "model.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"test data")

        # Compute real SHA256
        import hashlib
        h = hashlib.sha256(b"test data").hexdigest()

        mc_with_hash = ModelConfig(
            file_flag=mc.file_flag,
            repo=mc.repo,
            file=mc.file,
            directory=mc.directory,
            additional_server_flags=mc.additional_server_flags,
            sha256=h,
        )
        model_with_hash = Model(label="test", config=mc_with_hash, models_dir=tmp_path)

        with patch("run.logger") as mock_logger:
            model_with_hash._verify_sha256()  # should not raise
        mock_logger.info.assert_called()

    def test_mismatched_sha256_raises(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": "WRONG_HASH_VALUE_123456789012345678901234567890123456789012345678901234",
        })
        model = Model(label="test", config=mc, models_dir=tmp_path)
        model_path = tmp_path / "models" / "model.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"test data")

        with pytest.raises(ValueError, match="SHA256 mismatch"):
            model._verify_sha256()


# ---------------------------------------------------------------------------
# Model.download
# ---------------------------------------------------------------------------


class TestModelDownload:
    def test_skips_existing_model(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": None,
        })
        model = Model(label="test", config=mc, models_dir=tmp_path)
        model_path = tmp_path / "models" / "model.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text("existing")

        with patch("run.logger") as mock_logger, \
             patch("run.hf_hub_download") as mock_hf:

            model.download()
            mock_hf.assert_not_called()
            mock_logger.info.assert_called()

    def test_downloads_when_missing(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": None,
        })
        model = Model(label="test", config=mc, models_dir=tmp_path)

        with patch("run.hf_hub_download") as mock_hf, \
             patch("run.logger") as mock_logger, \
             patch("run.hashlib") as mock_hashlib, \
             patch("run.fcntl") as mock_fcntl, \
             patch("run.LLAMA_SERVER_LOCK_DIR", tmp_path / "locks"):

            # Mock lock to be a context manager
            mock_lock_file = MagicMock()
            mock_fcntl.flock = MagicMock()

            mock_hashlib.sha256.return_value.hexdigest.return_value = "abc"

            model.download()
            mock_hf.assert_called_once()
            mock_logger.info.assert_called()

    def test_path_property(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "subdir",
            "additionalServerFlags": [],
            "sha256": None,
        })
        model = Model(label="test", config=mc, models_dir=tmp_path)
        assert model.path == tmp_path / "subdir" / "model.gguf"


# ---------------------------------------------------------------------------
# Model.cleanup_download_lock_dir
# ---------------------------------------------------------------------------


class TestModelCleanupLockDir:
    def test_removes_empty_dir(self, tmp_path):
        lock_dir = tmp_path / "model_download"
        lock_dir.mkdir()
        with patch("run.logger") as mock_logger:
            Model.cleanup_download_lock_dir(lock_dir)
        assert not lock_dir.exists()

    def test_keeps_nonempty_dir(self, tmp_path):
        lock_dir = tmp_path / "model_download"
        lock_dir.mkdir()
        (lock_dir / "file.lock").write_text("data")
        with patch("run.logger"):
            Model.cleanup_download_lock_dir(lock_dir)
        assert lock_dir.exists()

    def test_handles_nonexistent_dir(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        with patch("run.logger"):
            Model.cleanup_download_lock_dir(nonexistent)  # should not raise


# ---------------------------------------------------------------------------
# ContainerNetworkManager._pull_secrets_from_config
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
        config = {
            "rules": [
                {"replace_with": {"value": "${ENV:MY_SECRET}"}}
            ]
        }
        config_file = tmp_path / "token_replacer.yaml"
        import yaml
        config_file.write_text(yaml.dump(config))

        with patch.dict(os.environ, {"MY_SECRET": "s3cret"}):
            mgr = self._make_manager(tmp_path)
            secrets = mgr._pull_secrets_from_config()

        assert secrets == {"MY_SECRET": "s3cret"}

    def test_skips_missing_env_vars(self, tmp_path):
        config = {
            "rules": [
                {"replace_with": {"value": "${ENV:MISSING_VAR}"}}
            ]
        }
        config_file = tmp_path / "token_replacer.yaml"
        import yaml
        config_file.write_text(yaml.dump(config))

        with patch.dict(os.environ, {}, clear=True):
            mgr = self._make_manager(tmp_path)
            secrets = mgr._pull_secrets_from_config()

        assert secrets == {}


# ---------------------------------------------------------------------------
# ContainerNetworkManager._env_flags
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
# ContainerNetworkManager._get_ref_count / refcount logic
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


# ---------------------------------------------------------------------------
# Server._get_bridge_ip
# ---------------------------------------------------------------------------


class TestServerGetBridgeIp:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=lock_dir,
            repo_root=tmp_path,
            server_id="test",
        )
        return s

    def test_parses_ip_from_ip_addr(self, tmp_path):
        s = self._make_server(tmp_path)
        ip_output = "2: bridge100: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n    inet 192.168.50.1/24 brd 192.168.50.255 scope global bridge100\n"
        with patch("run.subprocess.check_output", return_value=ip_output):
            result = s._get_bridge_ip()
        assert result == "192.168.50.1"

    def test_falls_back_to_ifconfig(self, tmp_path):
        s = self._make_server(tmp_path)
        ifconfig_output = "bridge100: flags=... mtu 1500\n    inet 10.0.0.1 netmask 0xffffff00\n"
        with patch("run.subprocess.check_output",
                   side_effect=[
                       subprocess.CalledProcessError(1, "ip"),
                       ifconfig_output,
                   ]):
            result = s._get_bridge_ip()
        assert result == "10.0.0.1"

    def test_returns_none_when_no_match(self, tmp_path):
        s = self._make_server(tmp_path)
        with patch("run.subprocess.check_output", return_value="no ip here\n"):
            result = s._get_bridge_ip()
        assert result is None

    def test_returns_none_on_command_failure(self, tmp_path):
        s = self._make_server(tmp_path)
        with patch("run.subprocess.check_output", side_effect=FileNotFoundError):
            result = s._get_bridge_ip()
        assert result is None


# ---------------------------------------------------------------------------
# Server._get_server_flags
# ---------------------------------------------------------------------------


class TestServerGetServerFlags:
    def _make_server(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": ["--threads", "4"],
            "sha256": None,
        })
        sc = ServerConfig(
            hf_models={"main": mc},
            flags=["--ctx-size", "4096"],
        )
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=lock_dir,
            repo_root=tmp_path,
            server_id="test-server",
        )
        return s

    def test_includes_main_model_flag(self, tmp_path):
        s = self._make_server(tmp_path)
        flags = s._get_server_flags()
        assert "--model" in flags
        # The path includes the full path to the model file
        assert any("model.gguf" in f for f in flags)

    def test_includes_additional_server_flags(self, tmp_path):
        s = self._make_server(tmp_path)
        flags = s._get_server_flags()
        assert "--threads" in flags
        assert "4" in flags

    def test_includes_base_flags(self, tmp_path):
        s = self._make_server(tmp_path)
        flags = s._get_server_flags()
        assert "--ctx-size" in flags
        assert "4096" in flags

    def test_includes_alias(self, tmp_path):
        s = self._make_server(tmp_path)
        flags = s._get_server_flags()
        assert "--alias" in flags
        assert "test-server" in flags

    def test_raises_when_no_main_model(self, tmp_path):
        sc = ServerConfig(hf_models={}, flags=[])
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=lock_dir,
            repo_root=tmp_path,
            server_id="test",
        )
        with pytest.raises(ValueError, match="No main model"):
            s._get_server_flags()


# ---------------------------------------------------------------------------
# Server._get_current_ref_count / refcount logic
# ---------------------------------------------------------------------------


class TestServerRefCount:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        lock_dir = tmp_path / "locks" / "test-server"
        lock_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test-server",
        )
        return s

    def test_ref_count_zero_when_no_file(self, tmp_path):
        s = self._make_server(tmp_path)
        assert s._get_current_ref_count() == 0

    def test_ref_count_reads_file(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["ref_count_file"].write_text("3\n")
        assert s._get_current_ref_count() == 3

    def test_ref_count_handles_invalid(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["ref_count_file"].write_text("garbage\n")
        assert s._get_current_ref_count() == 0


# ---------------------------------------------------------------------------
# Server._is_existing_server_healthy
# ---------------------------------------------------------------------------


class TestServerIsExistingServerHealthy:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        lock_dir = tmp_path / "locks" / "test"
        lock_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test",
        )
        return s

    def test_no_pid_file(self, tmp_path):
        s = self._make_server(tmp_path)
        healthy, pid, port = s._is_existing_server_healthy()
        assert healthy is False
        assert pid is None
        assert port is None

    def test_pid_file_invalid_format(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("not_a_pid\n")
        healthy, pid, port = s._is_existing_server_healthy()
        assert healthy is False

    def test_pid_file_valid_but_process_dead(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("99999\n8080\n")
        with patch("os.kill", side_effect=OSError("No such process")):
            healthy, pid, port = s._is_existing_server_healthy()
        assert healthy is False
        assert pid == 99999
        assert port == 8080

    def test_pid_file_valid_but_health_check_fails(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("1234\n8080\n")
        with patch("os.kill"), \
             patch("urllib.request.urlopen", side_effect=Exception("fail")):
            healthy, pid, port = s._is_existing_server_healthy()
        assert healthy is False
        assert pid == 1234
        assert port == 8080

    def test_pid_file_valid_and_healthy(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("1234\n8080\n")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_resp)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("os.kill"), \
             patch("urllib.request.urlopen", return_value=mock_ctx):
            healthy, pid, port = s._is_existing_server_healthy()
        assert healthy is True
        assert pid == 1234
        assert port == 8080


# ---------------------------------------------------------------------------
# Server._start_new_server_process (partial)
# ---------------------------------------------------------------------------


class TestServerStartNewProcess:
    def _make_server(self, tmp_path):
        mc = ModelConfig.from_dict({
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": None,
        })
        sc = ServerConfig(
            hf_models={"main": mc},
            flags=["--ctx-size", "4096"],
        )
        lock_dir = tmp_path / "locks" / "test"
        lock_dir.mkdir(parents=True, exist_ok=True)
        log_dir = tmp_path / "logs" / "test"
        log_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test",
        )
        return s

    def test_starts_process(self, tmp_path):
        s = self._make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.poll.return_value = None

        # Mock the health check response with proper context manager
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "ok"}'

        # Create a context manager that returns the mock response
        mock_context = MagicMock()
        mock_context.__enter__ = MagicMock(return_value=mock_resp)
        mock_context.__exit__ = MagicMock(return_value=False)

        with patch("run.subprocess.Popen", return_value=mock_proc), \
             patch("run.get_free_port", return_value=18080), \
             patch("run.urllib.request.urlopen", return_value=mock_context), \
             patch("run.stop_process_group"), \
             patch("run.logger"), \
             patch("run.subprocess.check_output", return_value="inet 192.168.50.1/24"), \
             patch("run.os.kill"):

            s._start_new_server_process()

        assert s.server_pid == 1234
        assert s.port == 18080

    def test_fails_when_process_dies_immediately(self, tmp_path):
        s = self._make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.poll.return_value = 1  # died immediately

        with patch("run.subprocess.Popen", return_value=mock_proc), \
             patch("run.get_free_port", return_value=18080), \
             patch("run.stop_process_group"):

            with pytest.raises(Exception, match="died immediately"):
                s._start_new_server_process()


# ---------------------------------------------------------------------------
# Server.stop
# ---------------------------------------------------------------------------


class TestServerStop:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        lock_dir = tmp_path / "locks" / "test"
        lock_dir.mkdir(parents=True, exist_ok=True)
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test",
        )
        return s

    def test_full_cleanup_on_refcount_zero(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["ref_count_file"].write_text("1")
        s._cleanup = MagicMock()
        s._cleanup.return_value = None

        with patch("fcntl.flock"):
            s.stop()
        s._cleanup.assert_called_once_with(full_cleanup=True)

    def test_no_cleanup_on_refcount_above_one(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["ref_count_file"].write_text("3")
        s._cleanup = MagicMock()

        with patch("fcntl.flock"):
            s.stop()
        s._cleanup.assert_not_called()

    def test_no_cleanup_when_no_refcount_file(self, tmp_path):
        s = self._make_server(tmp_path)
        s._cleanup = MagicMock()

        s.stop()  # should not raise
        s._cleanup.assert_not_called()

"""
Unit tests for src/server.py

Run with:
    python -m pytest src/tests/test_server.py -v
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import ModelConfig, ServerConfig
from server import Server

# ---------------------------------------------------------------------------
# ServerGetBridgeIp
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
        with patch("server.subprocess.check_output", return_value=ip_output):
            result = s._get_bridge_ip()
        assert result == "192.168.50.1"

    def test_falls_back_to_ifconfig(self, tmp_path):
        s = self._make_server(tmp_path)
        ifconfig_output = "bridge100: flags=... mtu 1500\n    inet 10.0.0.1 netmask 0xffffff00\n"
        with patch(
            "server.subprocess.check_output",
            side_effect=[
                subprocess.CalledProcessError(1, "ip"),
                ifconfig_output,
            ],
        ):
            result = s._get_bridge_ip()
        assert result == "10.0.0.1"

    def test_returns_none_when_no_match(self, tmp_path):
        s = self._make_server(tmp_path)
        with patch("server.subprocess.check_output", return_value="no ip here\n"):
            result = s._get_bridge_ip()
        assert result is None

    def test_returns_none_on_command_failure(self, tmp_path):
        s = self._make_server(tmp_path)
        with patch("server.subprocess.check_output", side_effect=FileNotFoundError):
            result = s._get_bridge_ip()
        assert result is None


# ---------------------------------------------------------------------------
# ServerGetServerFlags
# ---------------------------------------------------------------------------


class TestServerGetServerFlags:
    def _make_server(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": ["--threads", "4"],
                "sha256": None,
            }
        )
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
# ServerRefCount
# ---------------------------------------------------------------------------


class TestServerRefCount:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test-server",
        )
        s.paths["lock_dir"].mkdir(parents=True, exist_ok=True)
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
# ServerIsExistingServerHealthy
# ---------------------------------------------------------------------------


class TestServerIsExistingServerHealthy:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test",
        )
        s.paths["lock_dir"].mkdir(parents=True, exist_ok=True)
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
        with patch("os.kill"), patch("urllib.request.urlopen", side_effect=Exception("fail")):
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

        with patch("os.kill"), patch("urllib.request.urlopen", return_value=mock_ctx):
            healthy, pid, port = s._is_existing_server_healthy()
        assert healthy is True
        assert pid == 1234
        assert port == 8080


# ---------------------------------------------------------------------------
# ServerStartNewProcess
# ---------------------------------------------------------------------------


class TestServerStartNewProcess:
    def _make_server(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": [],
                "sha256": None,
            }
        )
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

        with (
            patch("server.subprocess.Popen", return_value=mock_proc),
            patch("server.get_free_port", return_value=18080),
            patch("server.urllib.request.urlopen", return_value=mock_context),
            patch("server.stop_process_group"),
            patch("server.logger"),
            patch("server.subprocess.check_output", return_value="inet 192.168.50.1/24"),
            patch("server.os.kill"),
        ):
            s._start_new_server_process()

        assert s.server_pid == 1234
        assert s.port == 18080

    def test_fails_when_process_dies_immediately(self, tmp_path):
        s = self._make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_proc.poll.return_value = 1  # died immediately

        with (
            patch("server.subprocess.Popen", return_value=mock_proc),
            patch("server.get_free_port", return_value=18080),
            patch("server.stop_process_group"),
            pytest.raises(Exception, match="died immediately"),
        ):
            s._start_new_server_process()


# ---------------------------------------------------------------------------
# ServerStop
# ---------------------------------------------------------------------------


class TestServerStop:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test",
        )
        s.paths["lock_dir"].mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------------------
# ServerSocatCleanup
# ---------------------------------------------------------------------------


class TestServerSocatCleanup:
    def _make_server(self, tmp_path):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        s = Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id="test",
        )
        s.paths["lock_dir"].mkdir(parents=True, exist_ok=True)
        return s

    def test_read_socat_pid_three_line_file(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("111\n8080\n222\n")
        assert s._read_socat_pid() == 222

    def test_read_socat_pid_legacy_two_line_file(self, tmp_path):
        """Old pid files (no socat line) must parse as no socat, not crash."""
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("111\n8080\n")
        assert s._read_socat_pid() is None

    def test_read_socat_pid_blank_socat_line(self, tmp_path):
        """podman writes a blank socat line (no socat started)."""
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("111\n8080\n\n")
        assert s._read_socat_pid() is None

    def test_cleanup_kills_socat_from_pid_file_without_handle(self, tmp_path):
        """Shared-server case: the process doing final cleanup never owned the
        socat, so it must be reaped via the pid recorded in the file."""
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("111\n8080\n222\n")
        assert s.socat_process is None  # this instance never started socat

        with patch("server.stop_process_group") as mock_stop:
            s._cleanup(full_cleanup=True)

        # socat pid 222 must have been reaped
        assert any(call.args[0] == 222 for call in mock_stop.call_args_list)

    def test_cleanup_no_socat_when_pid_file_has_none(self, tmp_path):
        s = self._make_server(tmp_path)
        s.paths["pid_file"].write_text("111\n8080\n\n")

        with patch("server.stop_process_group") as mock_stop:
            s._cleanup(full_cleanup=True)

        # only llama (if any) — never a socat pid 222-style reap for a blank line
        assert all(call.args[0] != "" for call in mock_stop.call_args_list)


# ---------------------------------------------------------------------------
# ConfigFingerprint (same-name / different-serverCustomParameters isolation)
# ---------------------------------------------------------------------------


class TestConfigFingerprint:
    def _server(self, tmp_path, sc, server_id="local-gemma"):
        return Server(
            config=sc,
            models_dir=tmp_path / "models",
            llama_bin="/usr/bin/llama-server",
            bridge_interface="bridge100",
            lock_dir=tmp_path / "locks",
            repo_root=tmp_path,
            server_id=server_id,
        )

    def test_same_name_same_config_shares_identity(self, tmp_path):
        a = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": ["--ctx-size", "4096"]}))
        b = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": ["--ctx-size", "4096"]}))
        assert a.instance_key == b.instance_key
        assert a.paths["lock_dir"] == b.paths["lock_dir"]

    def test_same_name_different_config_separates(self, tmp_path):
        a = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": ["--ctx-size", "4096"]}))
        b = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": ["--ctx-size", "8192"]}))
        assert a.instance_key != b.instance_key
        assert a.paths["lock_dir"] != b.paths["lock_dir"]
        assert a.paths["log_file"] != b.paths["log_file"]

    def test_flag_order_is_significant(self, tmp_path):
        a = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": ["--a", "--b"]}))
        b = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": ["--b", "--a"]}))
        assert a.instance_key != b.instance_key

    def test_model_file_change_separates(self, tmp_path):
        base = {"fileFlag": "--model", "repo": "o/r", "dir": "d", "additionalServerFlags": [], "sha256": None}
        a = self._server(
            tmp_path, ServerConfig(hf_models={"main": ModelConfig.from_dict({**base, "file": "a.gguf"})}, flags=[])
        )
        b = self._server(
            tmp_path, ServerConfig(hf_models={"main": ModelConfig.from_dict({**base, "file": "b.gguf"})}, flags=[])
        )
        assert a.instance_key != b.instance_key

    def test_instance_key_starts_with_server_id(self, tmp_path):
        a = self._server(tmp_path, ServerConfig.from_dict({"hfModels": {}, "flags": []}))
        assert a.instance_key.startswith("local-gemma-")

    def test_alias_uses_plain_server_id_not_instance_key(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "o/r",
                "file": "m.gguf",
                "dir": "d",
                "additionalServerFlags": [],
                "sha256": None,
            }
        )
        s = self._server(tmp_path, ServerConfig(hf_models={"main": mc}, flags=[]))
        flags = s._get_server_flags()
        assert flags[flags.index("--alias") + 1] == "local-gemma"

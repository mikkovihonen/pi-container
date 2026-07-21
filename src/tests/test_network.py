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

from network import ContainerNetworkManager, scan_config_env_refs, scan_tmpfs_paths

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


# ---------------------------------------------------------------------------
# ScanTmpfsPaths
# ---------------------------------------------------------------------------


class TestScanTmpfsPaths:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_empty_config(self, tmp_path):
        self._write(tmp_path, "tmpfs:\n  paths: []\n")
        assert scan_tmpfs_paths(tmp_path) == []

    def test_single_path(self, tmp_path):
        self._write(tmp_path, "tmpfs:\n  paths:\n    - /workspace/build\n")
        assert scan_tmpfs_paths(tmp_path) == ["/workspace/build"]

    def test_multiple_paths_sorted(self, tmp_path):
        self._write(
            tmp_path, "tmpfs:\n  paths:\n    - /workspace/cache\n    - /workspace/build\n    - /workspace/tmp\n"
        )
        assert scan_tmpfs_paths(tmp_path) == ["/workspace/build", "/workspace/cache", "/workspace/tmp"]

    def test_deduplicates_paths(self, tmp_path):
        self._write(tmp_path, "tmpfs:\n  paths:\n    - /workspace/build\n    - /workspace/build\n")
        assert scan_tmpfs_paths(tmp_path) == ["/workspace/build"]

    def test_missing_config(self, tmp_path):
        assert scan_tmpfs_paths(tmp_path) == []

    def test_empty_tmpfs_section(self, tmp_path):
        self._write(tmp_path, "tmpfs:\n")
        assert scan_tmpfs_paths(tmp_path) == []

    def test_nonexistent_dir(self):
        assert scan_tmpfs_paths(Path("/nonexistent")) == []


# ---------------------------------------------------------------------------
# Per-project proxy identity + mitmweb port
# ---------------------------------------------------------------------------


class TestPerProjectProxy:
    def _make_manager(self, proxy_name):
        return ContainerNetworkManager(
            container_runtime="docker",
            network_name="pi-isolated-net-abc",
            proxy_image="proxy:latest",
            proxy_name=proxy_name,
        )

    def test_refcount_files_keyed_by_proxy_name(self):
        """Two projects' managers must not share refcount files."""
        a = self._make_manager("pi-proxy-aaaaaaaaaa")
        b = self._make_manager("pi-proxy-bbbbbbbbbb")
        assert a.paths["ref_count_file"] != b.paths["ref_count_file"]
        assert a.paths["ref_count_lock"] != b.paths["ref_count_lock"]
        assert "pi-proxy-aaaaaaaaaa" in a.paths["ref_count_file"].name
        # Lock dir itself is shared (kept out of user workspaces).
        assert a.paths["lock_dir"] == b.paths["lock_dir"]

    def test_find_free_port_returns_valid_port(self):
        from util import get_free_port

        port = get_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_mitmweb_url_uses_known_port(self):
        mgr = self._make_manager("pi-proxy-aaaaaaaaaa")
        mgr.mitmweb_port = 49732
        assert mgr.mitmweb_url() == "http://127.0.0.1:49732"

    def test_mitmweb_url_queries_when_port_unknown(self):
        mgr = self._make_manager("pi-proxy-aaaaaaaaaa")
        completed = MagicMock(returncode=0, stdout="127.0.0.1:55001\n")
        with patch("subprocess.run", return_value=completed):
            assert mgr.mitmweb_url() == "http://127.0.0.1:55001"
        assert mgr.mitmweb_port == 55001

    def test_mitmweb_url_none_when_unresolvable(self):
        mgr = self._make_manager("pi-proxy-aaaaaaaaaa")
        completed = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=completed):
            assert mgr.mitmweb_url() is None


# ---------------------------------------------------------------------------
# ReadFlowExportEnabled (config.yaml flow_export.enabled)
# ---------------------------------------------------------------------------


class TestReadFlowExportEnabled:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_missing_file_returns_default(self, tmp_path):
        from network import read_flow_export_enabled

        assert read_flow_export_enabled(tmp_path) is False
        assert read_flow_export_enabled(tmp_path, default=True) is True

    def test_enabled_true(self, tmp_path):
        from network import read_flow_export_enabled

        self._write(tmp_path, "flow_export:\n  enabled: true\n")
        assert read_flow_export_enabled(tmp_path) is True

    def test_enabled_false(self, tmp_path):
        from network import read_flow_export_enabled

        self._write(tmp_path, "flow_export:\n  enabled: false\n")
        assert read_flow_export_enabled(tmp_path, default=True) is False

    def test_section_absent_uses_default(self, tmp_path):
        from network import read_flow_export_enabled

        self._write(tmp_path, "something_else: 1\n")
        assert read_flow_export_enabled(tmp_path, default=True) is True

    def test_malformed_yaml_returns_default(self, tmp_path):
        from network import read_flow_export_enabled

        self._write(tmp_path, "flow_export: [unclosed\n")
        assert read_flow_export_enabled(tmp_path, default=False) is False


# ---------------------------------------------------------------------------
# ReadProxyForwardEnv (config.yaml egress.allow)
# ---------------------------------------------------------------------------


class TestReadProxyForwardEnv:
    def _write(self, tmp_path, allow_body):
        (tmp_path / "config.yaml").write_text("egress:\n" + allow_body)

    def test_missing_file_denies_all(self, tmp_path):
        from network import read_proxy_forward_env

        assert read_proxy_forward_env(tmp_path) == {}

    def test_flags_only_truthy_emitted(self, tmp_path):
        from network import read_proxy_forward_env

        self._write(tmp_path, "  allow:\n    ssh: true\n    smtp: false\n    git: true\n")
        env = read_proxy_forward_env(tmp_path)
        assert env == {"PROXY_ALLOW_SSH": "true", "PROXY_ALLOW_GIT": "true"}

    def test_ports_list_joined(self, tmp_path):
        from network import read_proxy_forward_env

        self._write(tmp_path, "  allow:\n    tcp_ports: [2222, 8443]\n    udp_ports: [51820]\n")
        env = read_proxy_forward_env(tmp_path)
        assert env == {"PROXY_ALLOW_TCP_PORTS": "2222,8443", "PROXY_ALLOW_UDP_PORTS": "51820"}

    def test_ports_accept_comma_string(self, tmp_path):
        from network import read_proxy_forward_env

        self._write(tmp_path, "  allow:\n    tcp_ports: '2222,8443'\n")
        assert read_proxy_forward_env(tmp_path) == {"PROXY_ALLOW_TCP_PORTS": "2222,8443"}

    def test_empty_ports_omitted(self, tmp_path):
        from network import read_proxy_forward_env

        self._write(tmp_path, "  allow:\n    ssh: true\n    tcp_ports: []\n")
        assert read_proxy_forward_env(tmp_path) == {"PROXY_ALLOW_SSH": "true"}

    def test_truthy_variants(self, tmp_path):
        from network import read_proxy_forward_env

        self._write(tmp_path, "  allow:\n    ssh: 'yes'\n    smtp: 'on'\n    git: 1\n    ntp: 'nope'\n")
        env = read_proxy_forward_env(tmp_path)
        assert env == {"PROXY_ALLOW_SSH": "true", "PROXY_ALLOW_SMTP": "true", "PROXY_ALLOW_GIT": "true"}

    def test_malformed_yaml_denies_all(self, tmp_path):
        from network import read_proxy_forward_env

        (tmp_path / "config.yaml").write_text("egress: [unclosed\n")
        assert read_proxy_forward_env(tmp_path) == {}


# ---------------------------------------------------------------------------
# ReadResourceLimits (config.yaml resources.<agent|proxy>)
# ---------------------------------------------------------------------------


class TestReadResourceLimits:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_missing_file_uses_defaults(self, tmp_path):
        from network import read_resource_limits

        assert read_resource_limits(tmp_path, "agent") == {"memory": "16g", "cpus": 8}
        assert read_resource_limits(tmp_path, "proxy") == {"memory": "4g", "cpus": 4}

    def test_reads_configured_values(self, tmp_path):
        from network import read_resource_limits

        self._write(tmp_path, "resources:\n  agent:\n    memory: 32g\n    cpus: 12\n")
        assert read_resource_limits(tmp_path, "agent") == {"memory": "32g", "cpus": 12}

    def test_partial_overrides_fill_from_defaults(self, tmp_path):
        from network import read_resource_limits

        self._write(tmp_path, "resources:\n  proxy:\n    memory: 8g\n")
        assert read_resource_limits(tmp_path, "proxy") == {"memory": "8g", "cpus": 4}

    def test_resource_limit_args(self):
        from network import resource_limit_args

        assert resource_limit_args({"memory": "16g", "cpus": 8}) == ["--memory", "16g", "--cpus", "8"]

    def test_resource_limit_args_omits_null(self):
        from network import resource_limit_args

        assert resource_limit_args({"memory": None, "cpus": 8}) == ["--cpus", "8"]
        assert resource_limit_args({"memory": "2g", "cpus": None}) == ["--memory", "2g"]
        assert resource_limit_args({"memory": None, "cpus": None}) == []


# ---------------------------------------------------------------------------
# New config.yaml readers: llama / network / proxy.expose_ui / agent extras
# ---------------------------------------------------------------------------


class TestReadLlamaConfig:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_defaults(self, tmp_path):
        from network import read_llama_config

        assert read_llama_config(tmp_path) == {"startup_timeout": 180, "startup_attempts": 2}

    def test_configured(self, tmp_path):
        from network import read_llama_config

        self._write(tmp_path, "llama:\n  startup_timeout: 600\n  startup_attempts: 5\n")
        assert read_llama_config(tmp_path) == {"startup_timeout": 600, "startup_attempts": 5}

    def test_partial_fills_defaults(self, tmp_path):
        from network import read_llama_config

        self._write(tmp_path, "llama:\n  startup_timeout: 300\n")
        assert read_llama_config(tmp_path) == {"startup_timeout": 300, "startup_attempts": 2}


class TestReadNetworkConfig:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_defaults(self, tmp_path):
        from network import read_network_config

        assert read_network_config(tmp_path) == {"ipv6": False, "dns": "1.1.1.1"}

    def test_configured(self, tmp_path):
        from network import read_network_config

        self._write(tmp_path, "network:\n  ipv6: true\n  dns: 9.9.9.9\n")
        assert read_network_config(tmp_path) == {"ipv6": True, "dns": "9.9.9.9"}

    def test_ipv6_truthy_string(self, tmp_path):
        from network import read_network_config

        self._write(tmp_path, "network:\n  ipv6: 'yes'\n")
        assert read_network_config(tmp_path)["ipv6"] is True


class TestReadProxyUiExpose:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_default_localhost(self, tmp_path):
        from network import read_proxy_ui_expose

        assert read_proxy_ui_expose(tmp_path) == "localhost"

    def test_lan(self, tmp_path):
        from network import read_proxy_ui_expose

        self._write(tmp_path, "proxy:\n  expose_ui: lan\n")
        assert read_proxy_ui_expose(tmp_path) == "lan"

    def test_invalid_falls_back_to_localhost(self, tmp_path):
        from network import read_proxy_ui_expose

        self._write(tmp_path, "proxy:\n  expose_ui: everywhere\n")
        assert read_proxy_ui_expose(tmp_path) == "localhost"

    def test_publish_args_bind_scope(self, tmp_path):
        mgr = ContainerNetworkManager(
            container_runtime="docker",
            network_name="net",
            proxy_image="proxy:latest",
            proxy_name="pi-proxy-x",
            config_dir=tmp_path,
        )
        mgr.mitmweb_port = 49999
        # default (no config) → localhost bind
        assert mgr._mitmweb_publish_args() == ["-p", "127.0.0.1:49999:8081"]
        (tmp_path / "config.yaml").write_text("proxy:\n  expose_ui: lan\n")
        assert mgr._mitmweb_publish_args() == ["-p", "49999:8081"]


class TestReadAgentExtras:
    def _write(self, tmp_path, body):
        (tmp_path / "config.yaml").write_text(body)

    def test_defaults_empty(self, tmp_path):
        from network import read_agent_extras

        assert read_agent_extras(tmp_path) == {"env": {}, "mounts": [], "capabilities": [], "devices": []}

    def test_env_and_mounts(self, tmp_path):
        from network import read_agent_extras

        self._write(tmp_path, "agent:\n  env:\n    FOO: bar\n    N: 3\n  mounts:\n    - /a:/b:ro\n")
        extras = read_agent_extras(tmp_path)
        assert extras["env"] == {"FOO": "bar", "N": "3"}
        assert extras["mounts"] == ["/a:/b:ro"]
        assert extras["capabilities"] == []
        assert extras["devices"] == []

    def test_capabilities_and_devices(self, tmp_path):
        from network import read_agent_extras

        self._write(
            tmp_path,
            "agent:\n  capabilities:\n    - SYS_PTRACE\n    - NET_RAW\n  devices:\n    - /dev/video0:/dev/video0\n    - /dev/bus/usb:/dev/bus/usb:rw\n",
        )
        extras = read_agent_extras(tmp_path)
        assert extras["capabilities"] == ["SYS_PTRACE", "NET_RAW"]
        assert extras["devices"] == ["/dev/video0:/dev/video0", "/dev/bus/usb:/dev/bus/usb:rw"]

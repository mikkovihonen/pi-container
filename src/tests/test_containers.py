import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import containers

sys.dont_write_bytecode = True


class TestWarnAboutRegistryAllowlist:
    def _allowlist(self, tmp_path: Path, hostnames: list[str]) -> Path:
        import yaml

        (tmp_path / "allowlist.yaml").write_text(
            yaml.dump({"global": {"rules": [{"name": "r", "mode": "allow", "hostnames": hostnames}]}})
        )
        return tmp_path

    def test_warns_when_no_registry_present(self, tmp_path, caplog):
        self._allowlist(tmp_path, ["pypi.org", "github.com"])
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert any("no container registry hostname is allowed" in r.message for r in caplog.records)

    def test_silent_when_registry_and_its_cdn_allowed(self, tmp_path, caplog):
        self._allowlist(tmp_path, ["pypi.org", "registry-1.docker.io", "*.cloudfront.docker.com"])
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_warns_when_registry_allowed_without_its_blob_cdn(self, tmp_path, caplog):
        """The failure this preflight exists for: manifest resolves, first layer 403s."""
        self._allowlist(tmp_path, ["registry-1.docker.io", "auth.docker.io", "*.docker.io"])
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert any("*.cloudfront.docker.com" in r.message for r in caplog.records)

    def test_stale_cloudflare_entry_does_not_satisfy_the_check(self, tmp_path, caplog):
        """Docker Hub moved its blob CDN from Cloudflare to CloudFront."""
        self._allowlist(tmp_path, ["registry-1.docker.io", "production.cloudflare.docker.com"])
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert any("*.cloudfront.docker.com" in r.message for r in caplog.records)

    def test_wildcard_pattern_covers_blob_host(self, tmp_path, caplog):
        """ghcr's blob host is usually already covered by the github rule's wildcard."""
        self._allowlist(tmp_path, ["*.ghcr.io", "*.githubusercontent.com"])
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_registry_without_blob_redirect_never_warns(self, tmp_path, caplog):
        """gcr.io serves blobs inline, so the registry host alone is sufficient."""
        self._allowlist(tmp_path, ["gcr.io", "*.gcr.io"])
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_missing_allowlist_is_silent(self, tmp_path, caplog):
        """An unreadable allowlist is the addon's problem to report, not this preflight's."""
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_commented_template_still_warns(self, tmp_path, caplog):
        """The seeded template ships the registry rule commented out."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        template = repo_root / "pi-coding-agent" / "default" / "allowlist.yaml"
        (tmp_path / "allowlist.yaml").write_text(template.read_text())
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert any("no container registry hostname is allowed" in r.message for r in caplog.records)

    def test_shipped_registry_block_is_self_consistent(self, tmp_path, caplog):
        """Uncommenting the template's registry rule must produce a clean preflight."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        template = (repo_root / "pi-coding-agent" / "default" / "allowlist.yaml").read_text()
        uncommented = "\n".join(
            line.replace("    # ", "    ", 1) if line.lstrip().startswith("#") else line
            for line in template.splitlines()
        )
        (tmp_path / "allowlist.yaml").write_text(uncommented)
        with caplog.at_level("WARNING"):
            containers.warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []


class TestHostnameAllowed:
    """Glob/regex semantics must match the proxy addon's `_matches_hostname`."""

    @pytest.mark.parametrize(
        "host,patterns,expected",
        [
            ("registry-1.docker.io", ["*.docker.io"], True),
            ("production.cloudfront.docker.com", ["*.docker.io"], False),
            ("production.cloudfront.docker.com", ["*.cloudfront.docker.com"], True),
            ("evil.com", ["*.docker.io", "pypi.org"], False),
            ("REGISTRY-1.DOCKER.IO", ["registry-1.docker.io"], True),
            ("cdn01.quay.io", [r"^cdn\d+\.quay\.io$"], True),
            ("sub.a.docker.io", ["*.docker.io"], True),
            ("docker.io", ["*.docker.io"], False),
            ("anything", ["*.["], False),  # malformed regex is skipped, not raised
        ],
    )
    def test_matching(self, host, patterns, expected):
        assert containers.hostname_allowed(host, patterns) is expected


class TestUnavailableHostPorts:
    """Preflight for ``nested_containers.ports.publish``."""

    @staticmethod
    def _bound(host: str) -> tuple[int, object]:
        """Bind an ephemeral port on ``host`` and return it with the live socket."""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, 0))
        sock.listen(1)
        return sock.getsockname()[1], sock

    def test_empty_list_is_trivially_available(self):
        assert containers.unavailable_host_ports([], "localhost") == []

    def test_free_port_is_available(self):
        port, sock = self._bound("127.0.0.1")
        sock.close()
        assert containers.unavailable_host_ports([(port, port)], "localhost") == []

    def test_bound_port_is_reported(self):
        port, sock = self._bound("127.0.0.1")
        try:
            assert containers.unavailable_host_ports([(port, port)], "localhost") == [port]
        finally:
            sock.close()

    def test_only_the_conflicting_port_is_reported(self):
        port, sock = self._bound("127.0.0.1")
        free, free_sock = self._bound("127.0.0.1")
        free_sock.close()
        try:
            assert containers.unavailable_host_ports([(free, free), (port, port)], "localhost") == [port]
        finally:
            sock.close()

    def test_the_host_port_is_probed_not_the_agent_port(self):
        port, sock = self._bound("127.0.0.1")
        try:
            free, free_sock = self._bound("127.0.0.1")
            free_sock.close()
            assert containers.unavailable_host_ports([(free, port)], "localhost") == []
        finally:
            sock.close()

    def test_lan_scope_probes_the_wildcard_address(self):
        port, sock = self._bound("0.0.0.0")
        try:
            assert containers.unavailable_host_ports([(port, port)], "lan") == [port]
        finally:
            sock.close()


class TestPortHolders:
    """Attribution for a port conflict: which container is holding it."""

    @staticmethod
    def _ps(payload, monkeypatch, returncode: int = 0):
        import subprocess

        def fake_run(cmd, **kwargs):
            assert cmd[1:] == ["ps", "--format", "json"]
            if returncode:
                raise subprocess.CalledProcessError(returncode, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

        monkeypatch.setattr(containers.subprocess, "run", fake_run)

    @staticmethod
    def _container(name: str, host_port: int, *, image: str = "alpine", span: int = 1, status: str = "Up 2 minutes"):
        return {
            "Names": [name],
            "Image": image,
            "Status": status,
            "Ports": [{"host_ip": "127.0.0.1", "host_port": host_port, "container_port": 80, "range": span}],
        }

    def test_no_ports_asked_makes_no_subprocess_call(self, monkeypatch):
        def explode(*a, **k):  # pragma: no cover
            raise AssertionError("should not shell out for an empty port list")

        monkeypatch.setattr(containers.subprocess, "run", explode)
        assert containers.port_holders("podman", []) == {}

    def test_names_the_container_publishing_the_port(self, monkeypatch):
        self._ps(json.dumps([self._container("pi-coding-agent-abc123", 18080)]), monkeypatch)
        assert containers.port_holders("podman", [18080]) == {18080: "pi-coding-agent-abc123 (Up 2 minutes)"}

    def test_marks_a_holder_belonging_to_this_workspace(self, monkeypatch):
        image = "localhost/pi-container-project-f155d7064f-60e201e6.local:latest"
        self._ps(json.dumps([self._container("pi-coding-agent-abc123", 18080, image=image)]), monkeypatch)
        holders = containers.port_holders("podman", [18080], "f155d7064f")
        assert "this workspace" in holders[18080]

    def test_other_workspace_is_not_marked_as_this_one(self, monkeypatch):
        image = "localhost/pi-container-project-aaaaaaaaaa-60e201e6.local:latest"
        self._ps(json.dumps([self._container("pi-coding-agent-abc123", 18080, image=image)]), monkeypatch)
        holders = containers.port_holders("podman", [18080], "f155d7064f")
        assert "this workspace" not in holders[18080]

    def test_a_published_range_covers_every_port_in_it(self, monkeypatch):
        self._ps(json.dumps([self._container("ranged", 8000, span=11)]), monkeypatch)
        holders = containers.port_holders("podman", [8005])
        assert holders[8005].startswith("ranged")

    def test_unrelated_ports_are_not_reported(self, monkeypatch):
        self._ps(json.dumps([self._container("other", 9999)]), monkeypatch)
        assert containers.port_holders("podman", [18080]) == {}

    def test_runtime_failure_degrades_to_no_attribution(self, monkeypatch):
        self._ps("", monkeypatch, returncode=125)
        assert containers.port_holders("podman", [18080]) == {}

    def test_unparseable_output_degrades_to_no_attribution(self, monkeypatch):
        self._ps("not json at all", monkeypatch)
        assert containers.port_holders("podman", [18080]) == {}

    def test_malformed_entries_are_skipped_not_fatal(self, monkeypatch):
        payload = json.dumps(
            [
                "a bare string, not a container",
                {"Names": [], "Ports": [{"host_port": None}]},
                {"Names": ["good"], "Image": "x", "Status": "Up", "Ports": [{"host_port": 18080, "range": 1}]},
            ]
        )
        self._ps(payload, monkeypatch)
        assert containers.port_holders("podman", [18080]) == {18080: "good (Up)"}


class TestExtractServerConfigs:
    def test_extract_single_provider(self):
        data = {
            "providers": {
                "local-ornith": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "r",
                                "file": "f",
                                "dir": "d",
                            }
                        },
                    },
                }
            }
        }
        configs, hostnames = containers.extract_server_configs(data)
        assert len(configs) == 1
        assert configs[0]["name"] == "local-ornith"
        assert configs[0]["baseUrl"] == "http://llama:9999/v1"
        assert hostnames == {"llama", "local-ornith"}

    def test_extract_multiple_providers_with_custom_hostnames(self):
        data = {
            "providers": {
                "local-ornith": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "r",
                                "file": "f",
                                "dir": "d",
                            }
                        },
                    },
                },
                "local-gemma": {
                    "baseUrl": "http://gemma-server:9998/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "r",
                                "file": "f",
                                "dir": "d",
                            }
                        },
                    },
                },
                "anthropic": {
                    "baseUrl": "https://api.anthropic.com/v1",
                },
            }
        }
        configs, hostnames = containers.extract_server_configs(data)
        assert len(configs) == 2
        assert {c["name"] for c in configs} == {"local-ornith", "local-gemma"}
        assert hostnames == {"llama", "local-ornith", "local-gemma", "gemma-server"}


class TestCleanupOrphanedAgentContainers:
    def test_removes_container_with_dead_launcher_pid(self, monkeypatch):
        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-1234567890ab"],
                    "Image": "localhost/pi-container-project-f155d7064f-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "f155d7064f",
                        "pi-container.launcher_pid": "88888",
                        "pi-container.type": "agent",
                    },
                    "Status": "Up 10 minutes",
                }
            ]
        )
        monkeypatch.setattr(
            containers.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(containers, "is_pid_alive", lambda pid: False)
        removed_calls: list[list[str]] = []
        monkeypatch.setattr(
            containers,
            "run_quiet",
            lambda cmd, **kw: removed_calls.append(cmd) or MagicMock(returncode=0),
        )

        removed = containers.cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == ["pi-coding-agent-1234567890ab"]
        assert len(removed_calls) == 1
        assert removed_calls[0][:4] == ["podman", "rm", "-f", "pi-coding-agent-1234567890ab"]

    def test_skips_container_with_live_launcher_pid(self, monkeypatch):
        import os

        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-1234567890ab"],
                    "Image": "localhost/pi-container-project-f155d7064f-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "f155d7064f",
                        "pi-container.launcher_pid": str(os.getpid()),
                        "pi-container.type": "agent",
                    },
                    "Status": "Up 10 minutes",
                }
            ]
        )
        monkeypatch.setattr(
            containers.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(containers, "is_pid_alive", lambda pid: True)
        removed_calls: list[list[str]] = []
        monkeypatch.setattr(
            containers,
            "run_quiet",
            lambda cmd, **kw: removed_calls.append(cmd) or MagicMock(returncode=0),
        )

        removed = containers.cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == []
        assert len(removed_calls) == 0

    def test_skips_containers_from_other_projects(self, monkeypatch):
        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-other-project"],
                    "Image": "localhost/pi-container-project-other123-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "other123",
                        "pi-container.launcher_pid": "88888",
                    },
                    "Status": "Up 10 minutes",
                }
            ]
        )
        monkeypatch.setattr(
            containers.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(containers, "is_pid_alive", lambda pid: False)
        removed = containers.cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == []

    def test_removes_exited_container_without_launcher_label(self, monkeypatch):
        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-old"],
                    "Image": "localhost/pi-container-project-f155d7064f-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "f155d7064f",
                    },
                    "Status": "Exited (137) 5 minutes ago",
                }
            ]
        )
        monkeypatch.setattr(
            containers.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(
            containers,
            "run_quiet",
            lambda cmd, **kw: MagicMock(returncode=0),
        )
        removed = containers.cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == ["pi-coding-agent-old"]


class TestSweepOrphanedServers:
    def test_delegates_to_server_cleanup(self, monkeypatch, tmp_path):
        called_with = []
        monkeypatch.setattr(
            containers.Server,
            "cleanup_orphaned_servers",
            lambda lock_dir: called_with.append(lock_dir) or ["cleaned-instance"],
        )
        res = containers.sweep_orphaned_servers(tmp_path)
        assert res == ["cleaned-instance"]
        assert called_with == [tmp_path]


class TestSweepOrphanedProxies:
    def test_delegates_to_network_manager_cleanup(self, monkeypatch, tmp_path):
        called_with = []
        monkeypatch.setattr(
            containers.ContainerNetworkManager,
            "cleanup_orphaned_proxies",
            lambda runtime, lock_dir: called_with.append((runtime, lock_dir)) or ["pi-proxy-1234567890"],
        )
        res = containers.sweep_orphaned_proxies("podman", tmp_path)
        assert res == ["pi-proxy-1234567890"]
        assert called_with == [("podman", tmp_path)]


class TestStartDependenciesParallel:
    def test_zero_servers_starts_proxy_synchronously(self):
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        mock_netmgr = MagicMock()
        mock_factory = MagicMock(return_value=mock_netmgr)

        with ExitStack() as stack:
            res = containers.start_dependencies_parallel([], mock_factory, stack)
            assert res == mock_netmgr
            mock_factory.assert_called_once_with("[]")
            mock_netmgr.__enter__.assert_called_once()

        mock_netmgr.__exit__.assert_called_once()

    def test_single_server_starts_in_parallel(self):
        import threading
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        mock_server = MagicMock()
        mock_server.container_port = 8080
        mock_server.port = 50001
        mock_server.port_ready_event = threading.Event()

        def _mock_server_start():
            mock_server.port_ready_event.set()

        mock_server.start.side_effect = _mock_server_start

        mock_netmgr = MagicMock()
        mock_factory = MagicMock(return_value=mock_netmgr)

        with ExitStack() as stack:
            res = containers.start_dependencies_parallel([mock_server], mock_factory, stack)
            assert res == mock_netmgr
            mock_factory.assert_called_once_with('[{"cp": 8080, "hp": 50001}]')
            mock_server.start.assert_called_once()
            mock_netmgr.start.assert_called_once()

        mock_server.stop.assert_called_once()
        mock_netmgr.stop.assert_called_once()

    def test_multiple_servers_start_in_parallel(self):
        import threading
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        s1 = MagicMock()
        s1.container_port = 8080
        s1.port = 50001
        s1.port_ready_event = threading.Event()
        s1.start.side_effect = lambda: s1.port_ready_event.set()

        s2 = MagicMock()
        s2.container_port = 8081
        s2.port = 50002
        s2.port_ready_event = threading.Event()
        s2.start.side_effect = lambda: s2.port_ready_event.set()

        mock_netmgr = MagicMock()
        mock_factory = MagicMock(return_value=mock_netmgr)

        with ExitStack() as stack:
            res = containers.start_dependencies_parallel([s1, s2], mock_factory, stack)
            assert res == mock_netmgr
            mock_factory.assert_called_once_with('[{"cp": 8080, "hp": 50001}, {"cp": 8081, "hp": 50002}]')
            s1.start.assert_called_once()
            s2.start.assert_called_once()
            mock_netmgr.start.assert_called_once()

        s1.stop.assert_called_once()
        s2.stop.assert_called_once()
        mock_netmgr.stop.assert_called_once()

    def test_server_failure_raises_and_cleans_up(self):
        import threading
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        import pytest

        mock_server = MagicMock()
        mock_server.port_ready_event = threading.Event()
        mock_server.start.side_effect = RuntimeError("Failed to start llama-server")

        mock_netmgr = MagicMock()
        mock_factory = MagicMock(return_value=mock_netmgr)

        with ExitStack() as stack, pytest.raises(RuntimeError, match="Failed to start llama-server"):
            containers.start_dependencies_parallel([mock_server], mock_factory, stack)

        mock_server.stop.assert_called_once()

    def test_netmgr_failure_raises_and_cleans_up(self):
        import threading
        from contextlib import ExitStack
        from unittest.mock import MagicMock

        import pytest

        mock_server = MagicMock()
        mock_server.container_port = 8080
        mock_server.port = 50001
        mock_server.port_ready_event = threading.Event()
        mock_server.start.side_effect = lambda: mock_server.port_ready_event.set()

        mock_netmgr = MagicMock()
        mock_netmgr.start.side_effect = RuntimeError("Failed to start proxy")
        mock_factory = MagicMock(return_value=mock_netmgr)

        with ExitStack() as stack, pytest.raises(RuntimeError, match="Failed to start proxy"):
            containers.start_dependencies_parallel([mock_server], mock_factory, stack)

        mock_server.stop.assert_called_once()
        mock_netmgr.stop.assert_called_once()

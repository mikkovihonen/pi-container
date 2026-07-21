"""
Unit tests for src/runtimes.py

Run with:
    python -m pytest src/tests/test_runtimes.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtimes import (
    ContainerRuntime,
    DockerRuntime,
    PodmanRuntime,
)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_each_runtime(self):
        assert isinstance(ContainerRuntime.create("podman"), PodmanRuntime)
        assert isinstance(ContainerRuntime.create("docker"), DockerRuntime)

    def test_unknown_runtime_raises(self):
        with pytest.raises(ValueError, match="Unsupported container runtime"):
            ContainerRuntime.create("nerdctl")

    def test_defaults_per_runtime(self):
        assert ContainerRuntime.create("podman").upstream_network == "podman"
        assert ContainerRuntime.create("docker").upstream_network == "bridge"

    def test_env_overrides_win(self):
        rt = ContainerRuntime.create("podman", bridge_interface="br0", upstream_network="mynet")
        assert rt.bridge_interface == "br0"
        assert rt.upstream_network == "mynet"


# ---------------------------------------------------------------------------
# Isolated network is created --internal for every runtime
# ---------------------------------------------------------------------------


class TestIsolatedNetworkCreate:
    @pytest.mark.parametrize("name", ["docker"])
    def test_internal_flag_used(self, name):
        argv = ContainerRuntime.create(name).create_isolated_network_argv("isolated-net")
        assert argv == ["network", "create", "--internal", "isolated-net"]

    @pytest.mark.parametrize("name", ["podman", "docker"])
    def test_delete_uses_rm(self, name):
        """`rm` works for podman/docker."""
        argv = ContainerRuntime.create(name).delete_isolated_network_argv("isolated-net")
        assert argv == ["network", "rm", "isolated-net"]

    def test_podman_disables_dns(self):
        """Regression: podman must disable aardvark-dns so the agent's resolver
        is the proxy, not the network's built-in DNS."""
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net")
        assert argv == ["network", "create", "--internal", "--disable-dns", "isolated-net"]

    def test_docker_ipv6_uses_ipv6_flag(self):
        argv = DockerRuntime().create_isolated_network_argv("isolated-net", ipv6=True)
        assert argv == ["network", "create", "--internal", "--ipv6", "isolated-net"]

    def test_podman_ipv6_flag_appended(self):
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net", ipv6=True)
        assert argv == ["network", "create", "--internal", "--disable-dns", "--ipv6", "isolated-net"]

    @pytest.mark.parametrize("name", ["podman", "docker"])
    def test_ipv6_default_is_v4_only(self, name):
        """Default (ipv6=False) must not append --ipv6 for any runtime."""
        argv = ContainerRuntime.create(name).create_isolated_network_argv("isolated-net")
        assert "--ipv6" not in argv


# ---------------------------------------------------------------------------
# Proxy network attachment
# ---------------------------------------------------------------------------


class TestProxyNetworkArgs:
    def test_podman_pins_interface_names(self):
        """Regression: podman must attach BOTH networks and pin eth0/eth1."""
        rt = PodmanRuntime()
        assert rt.proxy_network_args("isolated-net") == [
            "--network",
            "podman:interface_name=eth0",
            "--network",
            "isolated-net:interface_name=eth1",
        ]

    def test_docker_connects_second_network_post_run(self):
        rt = DockerRuntime()
        assert rt.proxy_network_args("isolated-net") == ["--network", "bridge"]
        assert rt.proxy_secondary_connect_argv("proxy", "isolated-net") == [
            "network",
            "connect",
            "isolated-net",
            "proxy",
        ]

    def test_non_docker_has_no_secondary_connect(self):
        assert PodmanRuntime().proxy_secondary_connect_argv("proxy", "isolated-net") is None


# ---------------------------------------------------------------------------
# Agent networking is identical across runtimes and always routes via proxy
# ---------------------------------------------------------------------------


class TestAgentNetworkArgs:
    @pytest.mark.parametrize("name", ["podman", "docker"])
    def test_agent_routed_through_proxy_for_all_runtimes(self, name):
        """The internal network has no gateway, so every runtime must inject
        DEFAULT_ROUTE + NET_ADMIN and point DNS at the proxy."""
        rt = ContainerRuntime.create(name)
        args = rt.agent_network_args("isolated-net", "10.89.3.2")
        assert "--network" in args and "isolated-net" in args
        assert args[args.index("--dns") + 1] == "10.89.3.2"
        assert "NET_ADMIN" in args
        assert "DEFAULT_ROUTE=10.89.3.2" in args


# ---------------------------------------------------------------------------
# tmpfs mount syntax
# ---------------------------------------------------------------------------


class TestTmpfsArgs:
    def test_docker_uses_tmpfs_flag(self):
        assert DockerRuntime().tmpfs_args("/home/pi/") == ["--tmpfs", "/home/pi/"]

    def test_podman_uses_mount_syntax(self):
        assert PodmanRuntime().tmpfs_args("/home/pi/") == [
            "--mount",
            "type=tmpfs,tmpfs-mode=1777,notmpcopyup,destination=/home/pi/",
        ]


# ---------------------------------------------------------------------------
# Host llama-server reachability
# ---------------------------------------------------------------------------


class TestLlamaHostReachability:
    def test_podman_uses_host_internal_no_socat(self):
        rt = PodmanRuntime()
        # needs_host_socat() was removed when Apple container support was dropped
        assert rt.llama_host_addr() == "host.containers.internal"

    def test_docker_uses_host_internal_no_socat(self):
        rt = DockerRuntime()
        # needs_host_socat() was removed when Apple container support was dropped
        assert rt.llama_host_addr() == "host.docker.internal"

    def test_isolated_interface_is_eth1(self):
        for name in ("podman", "docker"):
            assert ContainerRuntime.create(name).proxy_isolated_interface == "eth1"

    def test_podman_resolves_host_addr_via_probe(self):
        from unittest.mock import MagicMock, patch

        rt = PodmanRuntime()
        completed = MagicMock(stdout="192.168.127.254 host.containers.internal\n")
        with patch("runtimes.subprocess.run", return_value=completed):
            assert rt.resolve_llama_host_addr("proxy:latest") == "192.168.127.254"

    def test_podman_resolve_falls_back_on_probe_failure(self):
        from unittest.mock import patch

        rt = PodmanRuntime()
        with patch("runtimes.subprocess.run", side_effect=Exception("boom")):
            assert rt.resolve_llama_host_addr("proxy:latest") == PodmanRuntime.HOST_INTERNAL_FALLBACK_IP

    def test_vm_runtimes_set_ip_forwarding(self):
        """Podman/Docker set net.ipv4.ip_forward=1 for proxy NAT."""
        assert PodmanRuntime().proxy_extra_run_args() == ["--sysctl", "net.ipv4.ip_forward=1"]
        assert DockerRuntime().proxy_extra_run_args() == ["--sysctl", "net.ipv4.ip_forward=1"]


# ---------------------------------------------------------------------------
# IPv6 run-time --sysctl policy
# ---------------------------------------------------------------------------


class TestIpv6RunArgs:
    @pytest.mark.parametrize("name", ["podman", "docker"])
    def test_vm_disables_ipv6_when_off(self, name):
        rt = ContainerRuntime.create(name)
        assert rt.ipv6_run_args(enabled=False) == ["--sysctl", "net.ipv6.conf.all.disable_ipv6=1"]

    @pytest.mark.parametrize("name", ["podman", "docker"])
    def test_vm_enables_forwarding_for_proxy(self, name):
        rt = ContainerRuntime.create(name)
        assert rt.ipv6_run_args(enabled=True, forwarding=True) == ["--sysctl", "net.ipv6.conf.all.forwarding=1"]

    @pytest.mark.parametrize("name", ["podman", "docker"])
    def test_vm_agent_needs_no_flag_when_on(self, name):
        """An enabled endpoint container (agent, forwarding=False) needs no flag:
        the default is IPv6-on, no-forwarding — exactly right."""
        rt = ContainerRuntime.create(name)
        assert rt.ipv6_run_args(enabled=True, forwarding=False) == []


# ---------------------------------------------------------------------------
# Upstream network IPv6 config inspection (option 2)
# ---------------------------------------------------------------------------


class TestUpstreamNetworkHasIpv6:
    def test_docker_enable_ipv6_flag(self):
        assert DockerRuntime()._network_entry_has_ipv6({"EnableIPv6": True}) is True

    def test_docker_ipam_v6_subnet(self):
        entry = {"EnableIPv6": False, "IPAM": {"Config": [{"Subnet": "fd00::/64"}]}}
        assert DockerRuntime()._network_entry_has_ipv6(entry) is True

    def test_docker_v4_only(self):
        entry = {"EnableIPv6": False, "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}
        assert DockerRuntime()._network_entry_has_ipv6(entry) is False

    def test_podman_ipv6_enabled_flag(self):
        assert PodmanRuntime()._network_entry_has_ipv6({"ipv6_enabled": True}) is True

    def test_podman_subnet_v6(self):
        entry = {"ipv6_enabled": False, "subnets": [{"subnet": "fd00::/64"}]}
        assert PodmanRuntime()._network_entry_has_ipv6(entry) is True

    def test_podman_v4_only(self):
        entry = {"ipv6_enabled": False, "subnets": [{"subnet": "10.89.0.0/24"}]}
        assert PodmanRuntime()._network_entry_has_ipv6(entry) is False

    def test_base_returns_unknown(self):
        """Base class returns None (unknown); runtimes override when their JSON format is known."""
        # Use DockerRuntime as a concrete example (base class is now abstract)
        rt = DockerRuntime()
        # DockerRuntime returns False for unknown formats (no EnableIPv6 or v6 subnet)
        assert rt._network_entry_has_ipv6({"anything": True}) is False

    def test_inspect_command_failure_returns_none(self):
        from unittest.mock import MagicMock, patch

        rt = DockerRuntime()
        failed = MagicMock(returncode=1, stdout="")
        with patch("runtimes.subprocess.run", return_value=failed):
            assert rt.upstream_network_has_ipv6() is None

    def test_inspect_parses_json(self):
        from unittest.mock import MagicMock, patch

        rt = DockerRuntime()
        completed = MagicMock(returncode=0, stdout='[{"EnableIPv6": true}]')
        with patch("runtimes.subprocess.run", return_value=completed):
            assert rt.upstream_network_has_ipv6() is True

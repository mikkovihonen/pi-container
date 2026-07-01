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
    AppleContainerRuntime,
    ContainerRuntime,
    DockerRuntime,
    PodmanRuntime,
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_each_runtime(self):
        assert isinstance(ContainerRuntime.create("container"), AppleContainerRuntime)
        assert isinstance(ContainerRuntime.create("podman"), PodmanRuntime)
        assert isinstance(ContainerRuntime.create("docker"), DockerRuntime)

    def test_unknown_runtime_raises(self):
        with pytest.raises(ValueError, match="Unsupported container runtime"):
            ContainerRuntime.create("nerdctl")

    def test_defaults_per_runtime(self):
        assert ContainerRuntime.create("container").bridge_interface == "bridge100"
        assert ContainerRuntime.create("container").upstream_network == "default"
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
    @pytest.mark.parametrize("name", ["container", "docker"])
    def test_internal_flag_used(self, name):
        argv = ContainerRuntime.create(name).create_isolated_network_argv("isolated-net")
        assert argv == ["network", "create", "--internal", "isolated-net"]

    @pytest.mark.parametrize("name", ["container", "podman", "docker"])
    def test_delete_uses_rm(self, name):
        """`rm` works for podman/docker and is an alias for `delete` on Apple container."""
        argv = ContainerRuntime.create(name).delete_isolated_network_argv("isolated-net")
        assert argv == ["network", "rm", "isolated-net"]

    def test_podman_disables_dns(self):
        """Regression: podman must disable aardvark-dns so the agent's resolver
        is the proxy, not the network's built-in DNS."""
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net")
        assert argv == ["network", "create", "--internal", "--disable-dns", "isolated-net"]


# ---------------------------------------------------------------------------
# Proxy network attachment
# ---------------------------------------------------------------------------


class TestProxyNetworkArgs:
    def test_container_attaches_two_networks_in_order(self):
        rt = AppleContainerRuntime()
        assert rt.proxy_network_args("isolated-net") == [
            "--network", "default",
            "--network", "isolated-net",
        ]

    def test_podman_pins_interface_names(self):
        """Regression: podman must attach BOTH networks and pin eth0/eth1."""
        rt = PodmanRuntime()
        assert rt.proxy_network_args("isolated-net") == [
            "--network", "podman:interface_name=eth0",
            "--network", "isolated-net:interface_name=eth1",
        ]

    def test_docker_connects_second_network_post_run(self):
        rt = DockerRuntime()
        assert rt.proxy_network_args("isolated-net") == ["--network", "bridge"]
        assert rt.proxy_secondary_connect_argv("proxy", "isolated-net") == [
            "network", "connect", "isolated-net", "proxy",
        ]

    def test_non_docker_has_no_secondary_connect(self):
        assert AppleContainerRuntime().proxy_secondary_connect_argv("proxy", "isolated-net") is None
        assert PodmanRuntime().proxy_secondary_connect_argv("proxy", "isolated-net") is None


# ---------------------------------------------------------------------------
# Agent networking is identical across runtimes and always routes via proxy
# ---------------------------------------------------------------------------


class TestAgentNetworkArgs:
    @pytest.mark.parametrize("name", ["container", "podman", "docker"])
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
    def test_container_and_docker_use_tmpfs_flag(self):
        assert AppleContainerRuntime().tmpfs_args("/home/pi/") == ["--tmpfs", "/home/pi/"]
        assert DockerRuntime().tmpfs_args("/home/pi/") == ["--tmpfs", "/home/pi/"]

    def test_podman_uses_mount_syntax(self):
        assert PodmanRuntime().tmpfs_args("/home/pi/") == [
            "--mount", "type=tmpfs,tmpfs-mode=1777,destination=/home/pi/",
        ]


# ---------------------------------------------------------------------------
# Host llama-server reachability
# ---------------------------------------------------------------------------


class TestLlamaHostReachability:
    def test_container_uses_socat_and_gateway_fallback(self):
        rt = AppleContainerRuntime()
        assert rt.needs_host_socat() is True
        assert rt.llama_host_addr() is None  # proxy resolves its own gateway

    def test_podman_uses_host_internal_no_socat(self):
        rt = PodmanRuntime()
        assert rt.needs_host_socat() is False
        assert rt.llama_host_addr() == "host.containers.internal"

    def test_docker_uses_host_internal_no_socat(self):
        rt = DockerRuntime()
        assert rt.needs_host_socat() is False
        assert rt.llama_host_addr() == "host.docker.internal"

    def test_isolated_interface_is_eth1(self):
        for name in ("container", "podman", "docker"):
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

    def test_container_resolve_returns_none(self):
        assert AppleContainerRuntime().resolve_llama_host_addr("proxy:latest") is None


# ---------------------------------------------------------------------------
# Proxy extra run args (ip_forward sysctl for VM runtimes)
# ---------------------------------------------------------------------------


class TestProxyExtraRunArgs:
    def test_container_has_no_extra_args(self):
        assert AppleContainerRuntime().proxy_extra_run_args() == []

    def test_podman_and_docker_set_ip_forward(self):
        assert PodmanRuntime().proxy_extra_run_args() == ["--sysctl", "net.ipv4.ip_forward=1"]
        assert DockerRuntime().proxy_extra_run_args() == ["--sysctl", "net.ipv4.ip_forward=1"]

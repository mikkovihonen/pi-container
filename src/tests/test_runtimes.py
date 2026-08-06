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
    PodmanRuntime,
)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_podman(self):
        assert isinstance(ContainerRuntime.create("podman"), PodmanRuntime)

    def test_unknown_runtime_raises(self):
        with pytest.raises(ValueError, match="Unsupported container runtime"):
            ContainerRuntime.create("nerdctl")

    def test_docker_is_no_longer_registered(self):
        """Docker support was removed deliberately — it must not resolve."""
        with pytest.raises(ValueError, match="Unsupported container runtime"):
            ContainerRuntime.create("docker")

    def test_defaults(self):
        assert ContainerRuntime.create("podman").upstream_network == "podman"

    def test_env_overrides_win(self):
        rt = ContainerRuntime.create("podman", bridge_interface="br0", upstream_network="mynet")
        assert rt.bridge_interface == "br0"
        assert rt.upstream_network == "mynet"


# ---------------------------------------------------------------------------
# Isolated network is created --internal for every runtime
# ---------------------------------------------------------------------------


class TestIsolatedNetworkCreate:
    def test_internal_flag_used(self):
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net")
        assert "--internal" in argv

    def test_delete_uses_rm(self):
        argv = PodmanRuntime().delete_isolated_network_argv("isolated-net")
        assert argv == ["network", "rm", "isolated-net"]

    def test_podman_disables_dns(self):
        """Regression: podman must disable aardvark-dns so the agent's resolver
        is the proxy, not the network's built-in DNS."""
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net")
        assert argv == ["network", "create", "--internal", "--disable-dns", "isolated-net"]

    def test_podman_ipv6_flag_appended(self):
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net", ipv6=True)
        assert argv == ["network", "create", "--internal", "--disable-dns", "--ipv6", "isolated-net"]

    def test_ipv6_default_is_v4_only(self):
        """Default (ipv6=False) must not append --ipv6."""
        argv = PodmanRuntime().create_isolated_network_argv("isolated-net")
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

    def test_secondary_connect_hook_is_gone(self):
        """Removed with DockerRuntime: podman attaches both networks at run time."""
        assert not hasattr(PodmanRuntime(), "proxy_secondary_connect_argv")


# ---------------------------------------------------------------------------
# Agent networking is identical across runtimes and always routes via proxy
# ---------------------------------------------------------------------------


class TestAgentNetworkArgs:
    def test_agent_routed_through_proxy(self):
        """The internal network has no gateway, so the agent must get
        DEFAULT_ROUTE + NET_ADMIN and point DNS at the proxy."""
        args = PodmanRuntime().agent_network_args("isolated-net", "10.89.3.2")
        assert "--network" in args and "isolated-net" in args
        assert args[args.index("--dns") + 1] == "10.89.3.2"
        assert "NET_ADMIN" in args
        assert "DEFAULT_ROUTE=10.89.3.2" in args


# ---------------------------------------------------------------------------
# tmpfs mount syntax
# ---------------------------------------------------------------------------


class TestTmpfsArgs:
    def test_base_uses_plain_tmpfs_flag(self):
        """The base-class default; PodmanRuntime overrides it."""
        assert ContainerRuntime.tmpfs_args(PodmanRuntime(), "/x") == ["--tmpfs", "/x"]

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

    def test_isolated_interface_is_eth1(self):
        assert PodmanRuntime().proxy_isolated_interface == "eth1"

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

    def test_vm_runtime_sets_ip_forwarding(self):
        """Podman sets net.ipv4.ip_forward=1 for proxy NAT."""
        assert PodmanRuntime().proxy_extra_run_args() == ["--sysctl", "net.ipv4.ip_forward=1"]


# ---------------------------------------------------------------------------
# IPv6 run-time --sysctl policy
# ---------------------------------------------------------------------------


class TestIpv6RunArgs:
    def test_vm_disables_ipv6_when_off(self):
        assert PodmanRuntime().ipv6_run_args(enabled=False) == ["--sysctl", "net.ipv6.conf.all.disable_ipv6=1"]

    def test_vm_enables_forwarding_for_proxy(self):
        assert PodmanRuntime().ipv6_run_args(enabled=True, forwarding=True) == [
            "--sysctl",
            "net.ipv6.conf.all.forwarding=1",
        ]

    def test_vm_agent_needs_no_flag_when_on(self):
        """An enabled endpoint container (agent, forwarding=False) needs no flag:
        the default is IPv6-on, no-forwarding — exactly right."""
        assert PodmanRuntime().ipv6_run_args(enabled=True, forwarding=False) == []


# ---------------------------------------------------------------------------
# Upstream network IPv6 config inspection (option 2)
# ---------------------------------------------------------------------------


class TestUpstreamNetworkHasIpv6:
    def test_podman_ipv6_enabled_flag(self):
        assert PodmanRuntime()._network_entry_has_ipv6({"ipv6_enabled": True}) is True

    def test_podman_subnet_v6(self):
        entry = {"ipv6_enabled": False, "subnets": [{"subnet": "fd00::/64"}]}
        assert PodmanRuntime()._network_entry_has_ipv6(entry) is True

    def test_podman_v4_only(self):
        entry = {"ipv6_enabled": False, "subnets": [{"subnet": "10.89.0.0/24"}]}
        assert PodmanRuntime()._network_entry_has_ipv6(entry) is False

    def test_base_returns_unknown(self):
        """Base class returns None (unknown); a runtime overrides when its JSON format is known."""
        assert ContainerRuntime._network_entry_has_ipv6(PodmanRuntime(), {"anything": True}) is None

    def test_inspect_command_failure_returns_none(self):
        from unittest.mock import MagicMock, patch

        rt = PodmanRuntime()
        failed = MagicMock(returncode=1, stdout="")
        with patch("runtimes.subprocess.run", return_value=failed):
            assert rt.upstream_network_has_ipv6() is None

    def test_inspect_parses_json(self):
        from unittest.mock import MagicMock, patch

        rt = PodmanRuntime()
        completed = MagicMock(returncode=0, stdout='[{"ipv6_enabled": true}]')
        with patch("runtimes.subprocess.run", return_value=completed):
            assert rt.upstream_network_has_ipv6() is True


# ---------------------------------------------------------------------------
# Nested containers (config.yaml nested_containers)
# ---------------------------------------------------------------------------

_STORE = "/home/pi/.local/share/containers"


def _nested(**overrides) -> dict:
    """A nested_containers config dict, as read_nested_containers_config returns."""
    return {
        "enabled": True,
        "storage": "volume",
        "security": "disable",
        "ports": {"expose": "localhost", "publish": []},
        **overrides,
    }


class TestNestedContainerArgs:
    def test_disabled_adds_nothing(self):
        """Off by default: no devices, no relaxed label, no store, no env."""
        rt = PodmanRuntime()
        assert rt.nested_container_args(_nested(enabled=False), "abcdef1234") == []
        assert rt.nested_container_args({}, "abcdef1234") == []

    def test_enabled_grants_devices_and_env(self):
        args = PodmanRuntime().nested_container_args(_nested(), "abcdef1234")
        assert args.count("--device") == 2
        assert "/dev/fuse" in args
        assert "/dev/net/tun" in args
        assert "XDG_RUNTIME_DIR=/run/user/1000" in args
        assert "NESTED_CONTAINERS=true" in args

    def test_enabled_grants_sys_admin_and_unmask(self):
        """Both were measured as necessary, not predicted: without unmask=ALL the
        inner runtime cannot write its sysctls (podman's read-only /proc binds are
        locked in the nested userns), and without CAP_SYS_ADMIN podman's default
        seccomp profile refuses sethostname/mount."""
        args = PodmanRuntime().nested_container_args(_nested(), "abcdef1234")
        assert "unmask=ALL" in args
        assert args[args.index("--cap-add") + 1] == "SYS_ADMIN"

    def test_enabled_does_not_grant_privileges(self):
        """The load-bearing negative: nesting must not need --privileged, an
        unconfined seccomp profile, a userns override, or a host socket."""
        args = PodmanRuntime().nested_container_args(_nested(), "abcdef1234")
        assert "--privileged" not in args
        assert "--userns" not in args
        assert not any("seccomp" in a for a in args)
        assert not any("docker.sock" in a or "podman.sock" in a for a in args)

    def test_disabled_grants_neither_sys_admin_nor_unmask(self):
        """The relaxations must be scoped to nesting being explicitly enabled."""
        args = PodmanRuntime().nested_container_args(_nested(enabled=False), "abcdef1234")
        assert args == []

    def test_volume_storage_mounts_named_volume(self):
        args = PodmanRuntime().nested_container_args(_nested(storage="volume"), "abcdef1234")
        assert args[args.index("--volume") + 1] == f"pi-nested-abcdef1234:{_STORE}"
        assert "--mount" not in args

    def test_tmpfs_storage_mounts_tmpfs(self):
        args = PodmanRuntime().nested_container_args(_nested(storage="tmpfs"), "abcdef1234")
        assert "--volume" not in args
        assert any(a.startswith("type=tmpfs") and a.endswith(f"destination={_STORE}") for a in args)

    def test_security_disable_is_the_default_label(self):
        args = PodmanRuntime().nested_container_args(_nested(security="disable"), "abcdef1234")
        assert args[args.index("--security-opt") + 1] == "label=disable"

    def test_security_engine_t_keeps_agent_confined(self):
        args = PodmanRuntime().nested_container_args(_nested(security="engine_t"), "abcdef1234")
        assert args[args.index("--security-opt") + 1] == "label=type:container_engine_t"

    def test_unknown_security_falls_back_to_disable(self):
        args = PodmanRuntime().nested_container_args(_nested(security="whatever"), "abcdef1234")
        assert args[args.index("--security-opt") + 1] == "label=disable"

    def test_volume_name_is_per_project(self):
        assert PodmanRuntime.nested_volume_name("abcdef1234") == "pi-nested-abcdef1234"
        assert PodmanRuntime.nested_volume_name("0123456789") != PodmanRuntime.nested_volume_name("abcdef1234")


class TestNestedPortArgs:
    """``-p`` flags republishing a nested container's UI port to the host.

    A nested container's own ``-p`` binds inside the agent's netns only; the agent
    container has to publish it onward, and published ports are fixed at start.
    """

    def test_nothing_published_by_default(self):
        assert PodmanRuntime().nested_port_args(_nested()) == []

    def test_disabled_publishes_nothing_even_if_listed(self):
        """The relaxation and the inbound surface are both scoped to nesting being on."""
        cfg = _nested(enabled=False, ports={"expose": "localhost", "publish": [(3000, 3000)]})
        assert PodmanRuntime().nested_port_args(cfg) == []

    def test_localhost_scope_binds_loopback_only(self):
        cfg = _nested(ports={"expose": "localhost", "publish": [(3000, 3000)]})
        assert PodmanRuntime().nested_port_args(cfg) == ["-p", "127.0.0.1:3000:3000"]

    def test_lan_scope_binds_every_interface(self):
        cfg = _nested(ports={"expose": "lan", "publish": [(3000, 3000)]})
        assert PodmanRuntime().nested_port_args(cfg) == ["-p", "3000:3000"]

    def test_remapped_port_is_host_then_agent(self):
        cfg = _nested(ports={"expose": "localhost", "publish": [(18080, 8080)]})
        assert PodmanRuntime().nested_port_args(cfg) == ["-p", "127.0.0.1:18080:8080"]

    def test_multiple_ports_each_get_a_flag(self):
        cfg = _nested(ports={"expose": "localhost", "publish": [(3000, 3000), (5173, 5173)]})
        args = PodmanRuntime().nested_port_args(cfg)
        assert args == ["-p", "127.0.0.1:3000:3000", "-p", "127.0.0.1:5173:5173"]

    def test_missing_ports_key_is_not_fatal(self):
        """A config read by an older path (or a hand-edited one) must not crash."""
        cfg = {"enabled": True, "storage": "volume", "security": "disable"}
        assert PodmanRuntime().nested_port_args(cfg) == []

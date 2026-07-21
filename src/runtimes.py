import sys

sys.dont_write_bytecode = True

"""Container-runtime abstraction layer.

``run.py`` orchestrates four moving parts — the host ``llama-server``, the
``pi-coding-agent`` container, the ``pi-coding-agent-proxy`` container and the
container network that isolates them. The two supported runtimes (``podman`` and
``docker``) differ in CLI flags and networking semantics. Every one of those
differences is encapsulated in a :class:`ContainerRuntime` subclass so
``run.py`` stays runtime-agnostic.

Shared network model (identical across all runtimes)
----------------------------------------------------
* The isolated network is created ``--internal`` (no external gateway). A
  container attached only to it therefore has **no default route** — verified
  on ``podman`` and ``docker``.
* The **proxy** attaches to two networks: the upstream network (``eth0`` →
  internet, NAT/MASQUERADE) and the isolated network (``eth1`` → agent).
* The **agent** attaches only to the isolated network and has its default route
  and DNS pointed at the proxy's ``eth1`` IP. Because the isolated network has
  no gateway of its own, the default route is injected via the ``DEFAULT_ROUTE``
  env var + ``NET_ADMIN`` (the entrypoint runs ``ip route replace default``).
  This applies to **all** runtimes — the previous podman code omitted it, which
  is why the agent was never actually routed through the proxy.

Per-runtime differences (what the subclasses own)
-------------------------------------------------
* upstream network name / host bridge interface name
* how the proxy attaches to a second network (multiple ``--network`` flags vs.
  post-run ``network connect``) and how the isolated interface is named ``eth1``
* tmpfs mount flag syntax
* how the proxy reaches the host ``llama-server`` (``LLAMA_HOST_ADDR``):
  ``podman``/``docker`` run inside a VM on macOS and reach the host loopback
  through ``host.containers.internal`` / ``host.docker.internal`` (gvproxy),
  so no socat is needed.
"""

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


def _vm_ipv6_run_args(enabled: bool, forwarding: bool) -> list[str]:
    """``--sysctl`` flags enforcing the IPv6 policy for VM runtimes.

    Rootless podman/docker namespaces forbid writing ``net.*`` sysctls from
    inside the container, so the policy is pinned at ``run`` time:

    * disabled → ``net.ipv6.conf.all.disable_ipv6=1`` tears down IPv6 entirely.
    * enabled + forwarding → ``net.ipv6.conf.all.forwarding=1`` lets the proxy
      route v6 traffic (mirrors the v4 ``ip_forward`` flag). An enabled endpoint
      container (the agent) needs no flag — the default (IPv6 on, no forwarding)
      is exactly right.
    """
    if not enabled:
        return ["--sysctl", "net.ipv6.conf.all.disable_ipv6=1"]
    if forwarding:
        return ["--sysctl", "net.ipv6.conf.all.forwarding=1"]
    return []


class ContainerRuntime(ABC):
    """Base class encapsulating the differences between container runtimes.

    Subclasses set the class-level attributes and override the handful of
    methods where the runtimes genuinely diverge.
    """

    #: The CLI binary name (also the value stored in ``CONTAINER_RUNTIME``).
    name: str = ""
    #: Host bridge interface where ``llama-server`` is exposed via socat.
    default_bridge_interface: str = ""
    #: The pre-existing runtime network used for outbound/internet access.
    default_upstream_network: str = ""
    #: Interface name the isolated network gets *inside* the proxy container.
    proxy_isolated_interface: str = "eth1"

    def __init__(
        self,
        bridge_interface: str | None = None,
        upstream_network: str | None = None,
    ) -> None:
        # Explicit env overrides win; otherwise use the runtime's default.
        self.bridge_interface: str = bridge_interface or self.default_bridge_interface
        self.upstream_network: str = upstream_network or self.default_upstream_network

    # ── Factory ──────────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        runtime_name: str,
        bridge_interface: str | None = None,
        upstream_network: str | None = None,
    ) -> ContainerRuntime:
        registry: dict[str, type[ContainerRuntime]] = {
            "podman": PodmanRuntime,
            "docker": DockerRuntime,
        }
        try:
            runtime_cls = registry[runtime_name]
        except KeyError:
            raise ValueError(
                f"Unsupported container runtime '{runtime_name}'. Supported: {', '.join(sorted(registry))}."
            ) from None
        return runtime_cls(bridge_interface=bridge_interface, upstream_network=upstream_network)

    # ── Isolated network lifecycle ───────────────────────────────────────
    def _ipv6_network_flags(self) -> list[str]:
        """``network create`` flags that give the isolated net an IPv6 subnet.

        podman/docker accept ``--ipv6`` and auto-assign a ULA subnet.
        """
        return ["--ipv6"]

    def create_isolated_network_argv(self, network_name: str, ipv6: bool = False) -> list[str]:
        """Argv (after the CLI binary) that creates the internal isolated network.

        All three runtimes accept ``network create --internal <name>``. When
        ``ipv6`` is set, the runtime-specific IPv6 flags (see
        :meth:`_ipv6_network_flags`) are appended so the network gets an IPv6
        subnet; otherwise the network is IPv4-only.
        """
        argv = ["network", "create", "--internal"]
        if ipv6:
            argv += self._ipv6_network_flags()
        argv.append(network_name)
        return argv

    # ── Upstream IPv6 capability (option 2: inspect network config) ──────
    def upstream_network_has_ipv6(self) -> bool | None:
        """Whether the upstream network is *configured* for IPv6 (best-effort).

        Runs ``network inspect <upstream>`` and delegates parsing of the
        (runtime-specific) JSON to :meth:`_network_entry_has_ipv6`. Returns
        ``True``/``False``, or ``None`` when it cannot be determined (command
        failed, unparseable output, or the runtime's format is unknown).

        Note this only reflects the network's *configuration* — it does not
        prove packets actually egress to the v6 internet (that is confirmed
        post-start against the proxy's real ``eth0``).
        """
        try:
            result = subprocess.run(
                [self.name, "network", "inspect", self.upstream_network],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
        except Exception as e:
            logger.warning(f"Could not inspect upstream network {self.upstream_network}: {e}")
            return None

        entry = data[0] if isinstance(data, list) and data else data
        if not isinstance(entry, dict):
            return None
        return self._network_entry_has_ipv6(entry)

    def _network_entry_has_ipv6(self, entry: dict[str, Any]) -> bool | None:
        """Parse a ``network inspect`` entry for IPv6 enablement.

        Base returns ``None`` (unknown); runtimes whose JSON format is known
        override this. Only called for runtimes assumed to have egress
        (:attr:`ipv6_upstream_egress`).
        """
        return None

    def delete_isolated_network_argv(self, network_name: str) -> list[str] | None:
        """Argv that removes the isolated network on shutdown (``None`` to skip).

        ``network rm`` is understood by podman and docker.
        """
        return ["network", "rm", network_name]

    # ── Proxy container networking ───────────────────────────────────────
    @abstractmethod
    def proxy_network_args(self, isolated_network: str) -> list[str]:
        """``run`` flags attaching the proxy to upstream (eth0) + isolated (eth1)."""

    def proxy_secondary_connect_argv(self, proxy_name: str, isolated_network: str) -> list[str] | None:
        """Post-``run`` argv to connect a second network (``None`` if attached at run).

        Only Docker needs this: ``docker run`` attaches a single network, so the
        isolated network is connected afterwards with ``network connect``.
        """
        return None

    def proxy_extra_run_args(self) -> list[str]:
        """Extra runtime-specific ``run`` flags for the proxy container."""
        return []

    def ipv6_run_args(self, enabled: bool, forwarding: bool = False) -> list[str]:
        """``run`` flags that enforce the IPv6 policy for a container.

        The base class returns nothing: VM runtimes (podman/docker) override
        this because their rootless network namespaces forbid writing ``net.*``
        sysctls from inside the container, so the toggle must be set at ``run``
        time via ``--sysctl``.

        ``forwarding`` is only meaningful for the proxy (which routes traffic);
        endpoint containers like the agent leave it False.
        """
        return []

    # ── Agent container networking (identical across runtimes) ───────────
    def agent_network_args(self, isolated_network: str, proxy_isolated_ip: str) -> list[str]:
        """``run`` flags for the agent: isolated network only, routed via the proxy.

        The isolated network has no gateway, so the default route and DNS are
        both pointed at the proxy's ``eth1`` IP. ``NET_ADMIN`` lets the entrypoint
        run ``ip route replace default via $DEFAULT_ROUTE``.
        """
        return [
            "--network",
            isolated_network,
            "--dns",
            proxy_isolated_ip,
            "--cap-add",
            "NET_ADMIN",
            "--env",
            f"DEFAULT_ROUTE={proxy_isolated_ip}",
        ]

    # ── Mounts ───────────────────────────────────────────────────────────
    def tmpfs_args(self, destination: str) -> list[str]:
        """Flags mounting a writable tmpfs at ``destination``."""
        return ["--tmpfs", destination]

    # ── Host llama-server reachability (podman/docker only) ─────────────
    # These are overridden by PodmanRuntime and DockerRuntime to return the
    # hostname that resolves the host loopback (gvproxy on macOS).
    # The proxy uses this as LLAMA_HOST_ADDR for DNAT.


class PodmanRuntime(ContainerRuntime):
    """Podman (netavark). On macOS it runs inside a Linux VM.

    Multiple networks attach at ``run`` time and interface names are pinned via
    ``<net>:interface_name=<if>`` so the isolated network is deterministically
    ``eth1`` (matching the proxy entrypoint). The mac host loopback — where
    ``llama-server`` listens — is reachable via ``host.containers.internal``
    (gvproxy forwards to it), so no host socat is needed.
    """

    name = "podman"
    default_bridge_interface = "podman0"
    default_upstream_network = "podman"

    #: Hostname podman maps to the host loopback (via gvproxy on macOS).
    HOST_INTERNAL_HOSTNAME = "host.containers.internal"
    #: gvproxy's fixed host address on a podman machine; used as a last resort
    #: when the hostname cannot be resolved via a probe container.
    HOST_INTERNAL_FALLBACK_IP = "192.168.127.254"

    def create_isolated_network_argv(self, network_name: str, ipv6: bool = False) -> list[str]:
        # --disable-dns stops podman's aardvark-dns from occupying the network's
        # .1 address and shadowing the agent's resolver. Without it the agent's
        # resolv.conf points at aardvark instead of the proxy, so the "llama"
        # hostname (served by the proxy's mitmproxy DNS) never resolves and
        # traffic is not intercepted.
        argv = ["network", "create", "--internal", "--disable-dns"]
        if ipv6:
            argv += self._ipv6_network_flags()
        argv.append(network_name)
        return argv

    def proxy_network_args(self, isolated_network: str) -> list[str]:
        # Attach both networks at run time and pin interface names. Podman does
        # not name interfaces by --network order, so pinning is required to keep
        # the isolated network on eth1 (matching the proxy entrypoint).
        return [
            "--network",
            f"{self.upstream_network}:interface_name=eth0",
            "--network",
            f"{isolated_network}:interface_name=eth1",
        ]

    def tmpfs_args(self, destination: str) -> list[str]:
        # notmpcopyup: leave the tmpfs empty rather than copying up the content
        # of the underlying directory (image layer or, for a nested mount, the
        # bind volume beneath it). The tmpfs always
        # starts empty, so a mount like /workspace/.venv is a clean scratch dir
        # on both runtimes instead of a copy of the host's (macOS) .venv.
        return ["--mount", f"type=tmpfs,tmpfs-mode=1777,notmpcopyup,destination={destination}"]

    def _network_entry_has_ipv6(self, entry: dict[str, Any]) -> bool | None:
        # netavark inspect: `ipv6_enabled` bool, plus `subnets[].subnet`.
        if entry.get("ipv6_enabled") is True:
            return True
        for sub in entry.get("subnets", []) or []:
            if isinstance(sub, dict) and ":" in str(sub.get("subnet", "")):
                return True
        return False

    def proxy_extra_run_args(self) -> list[str]:
        # Rootless podman forbids writing net.ipv4.ip_forward from inside the
        # container, so set it at run time instead.
        return ["--sysctl", "net.ipv4.ip_forward=1"]

    def ipv6_run_args(self, enabled: bool, forwarding: bool = False) -> list[str]:
        # Rootless podman forbids writing net.ipv6.* sysctls from inside the
        # container, so the IPv6 policy is set at run time.
        return _vm_ipv6_run_args(enabled, forwarding)

    def llama_host_addr(self) -> str | None:
        return self.HOST_INTERNAL_HOSTNAME

    def resolve_llama_host_addr(self, probe_image: str | None = None) -> str | None:
        """Resolve ``host.containers.internal`` to a numeric IP.

        Podman drops the ``host.containers.internal`` /etc/hosts entry when a
        container is attached to multiple networks (as the proxy is), so the
        entrypoint cannot resolve the hostname itself. We resolve it here — from
        a throwaway single-network probe container where the entry is present —
        and inject the numeric IP, falling back to gvproxy's fixed address.
        """
        if probe_image:
            try:
                result = subprocess.run(
                    [
                        self.name,
                        "run",
                        "--rm",
                        "--network",
                        self.upstream_network,
                        "--entrypoint",
                        "getent",
                        probe_image,
                        "hosts",
                        self.HOST_INTERNAL_HOSTNAME,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                tokens = result.stdout.split()
                if tokens:
                    logger.info(f"Resolved {self.HOST_INTERNAL_HOSTNAME} → {tokens[0]}")
                    return tokens[0]
            except Exception as e:
                logger.warning(f"Could not probe {self.HOST_INTERNAL_HOSTNAME}: {e}")

        logger.info(f"Falling back to {self.HOST_INTERNAL_FALLBACK_IP} for llama host address")
        return self.HOST_INTERNAL_FALLBACK_IP


class DockerRuntime(ContainerRuntime):
    """Docker Engine. Untested here (no docker binary on this host).

    ``docker run`` attaches only one network, so the proxy is started on the
    upstream network and the isolated network is connected afterwards via
    ``network connect`` (which yields ``eth1`` as the second interface). Docker
    does not expose a per-attachment interface-name option, so ordering is
    relied upon. On macOS the host loopback is reached via
    ``host.docker.internal``.
    """

    name = "docker"
    default_bridge_interface = "docker0"
    default_upstream_network = "bridge"

    def proxy_network_args(self, isolated_network: str) -> list[str]:
        # Only the upstream network is attached at run time; the isolated
        # network is connected post-run (see proxy_secondary_connect_argv).
        return ["--network", self.upstream_network]

    def proxy_secondary_connect_argv(self, proxy_name: str, isolated_network: str) -> list[str] | None:
        return ["network", "connect", isolated_network, proxy_name]

    def _network_entry_has_ipv6(self, entry: dict[str, Any]) -> bool | None:
        # docker inspect: `EnableIPv6` bool, plus a v6 subnet in IPAM.Config.
        if entry.get("EnableIPv6") is True:
            return True
        ipam = entry.get("IPAM", {}) or {}
        return any(isinstance(cfg, dict) and ":" in str(cfg.get("Subnet", "")) for cfg in ipam.get("Config", []) or [])

    def proxy_extra_run_args(self) -> list[str]:
        return ["--sysctl", "net.ipv4.ip_forward=1"]

    def ipv6_run_args(self, enabled: bool, forwarding: bool = False) -> list[str]:
        return _vm_ipv6_run_args(enabled, forwarding)

    def llama_host_addr(self) -> str | None:
        return "host.docker.internal"

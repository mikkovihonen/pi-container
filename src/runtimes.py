import sys
sys.dont_write_bytecode = True

"""Container-runtime abstraction layer.

``run.py`` orchestrates four moving parts — the host ``llama-server``, the
``pi-coding-agent`` container, the ``pi-coding-agent-proxy`` container and the
container network that isolates them. The three supported runtimes (Apple
``container``, ``podman`` and ``docker``) differ in CLI flags and networking
semantics. Every one of those differences is encapsulated in a
:class:`ContainerRuntime` subclass so ``run.py`` stays runtime-agnostic.

Shared network model (identical across all runtimes)
----------------------------------------------------
* The isolated network is created ``--internal`` (no external gateway). A
  container attached only to it therefore has **no default route** — verified
  on both Apple ``container`` and ``podman``.
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
* how the proxy reaches the host ``llama-server`` (``LLAMA_HOST_ADDR``): Apple
  ``container`` shares an L2 bridge with the host and uses a host-side ``socat``
  bound to the bridge IP; ``podman``/``docker`` run inside a VM on macOS and
  reach the host loopback through ``host.containers.internal`` /
  ``host.docker.internal`` (gvproxy), so no socat is needed.
"""

import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Type

logger = logging.getLogger(__name__)


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
        bridge_interface: Optional[str] = None,
        upstream_network: Optional[str] = None,
    ) -> None:
        # Explicit env overrides win; otherwise use the runtime's default.
        self.bridge_interface: str = bridge_interface or self.default_bridge_interface
        self.upstream_network: str = upstream_network or self.default_upstream_network

    # ── Factory ──────────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        runtime_name: str,
        bridge_interface: Optional[str] = None,
        upstream_network: Optional[str] = None,
    ) -> "ContainerRuntime":
        registry: Dict[str, Type["ContainerRuntime"]] = {
            "container": AppleContainerRuntime,
            "podman": PodmanRuntime,
            "docker": DockerRuntime,
        }
        try:
            runtime_cls = registry[runtime_name]
        except KeyError:
            raise ValueError(
                f"Unsupported container runtime '{runtime_name}'. "
                f"Supported: {', '.join(sorted(registry))}."
            )
        return runtime_cls(bridge_interface=bridge_interface, upstream_network=upstream_network)

    # ── Isolated network lifecycle ───────────────────────────────────────
    def create_isolated_network_argv(self, network_name: str) -> List[str]:
        """Argv (after the CLI binary) that creates the internal isolated network.

        All three runtimes accept ``network create --internal <name>``.
        """
        return ["network", "create", "--internal", network_name]

    def delete_isolated_network_argv(self, network_name: str) -> Optional[List[str]]:
        """Argv that removes the isolated network on shutdown (``None`` to skip).

        ``network rm`` is understood by podman and docker, and accepted by Apple
        ``container`` as an alias for ``delete``.
        """
        return ["network", "rm", network_name]

    # ── Proxy container networking ───────────────────────────────────────
    @abstractmethod
    def proxy_network_args(self, isolated_network: str) -> List[str]:
        """``run`` flags attaching the proxy to upstream (eth0) + isolated (eth1)."""

    def proxy_secondary_connect_argv(
        self, proxy_name: str, isolated_network: str
    ) -> Optional[List[str]]:
        """Post-``run`` argv to connect a second network (``None`` if attached at run).

        Only Docker needs this: ``docker run`` attaches a single network, so the
        isolated network is connected afterwards with ``network connect``.
        """
        return None

    def proxy_extra_run_args(self) -> List[str]:
        """Extra runtime-specific ``run`` flags for the proxy container."""
        return []

    # ── Agent container networking (identical across runtimes) ───────────
    def agent_network_args(self, isolated_network: str, proxy_isolated_ip: str) -> List[str]:
        """``run`` flags for the agent: isolated network only, routed via the proxy.

        The isolated network has no gateway, so the default route and DNS are
        both pointed at the proxy's ``eth1`` IP. ``NET_ADMIN`` lets the entrypoint
        run ``ip route replace default via $DEFAULT_ROUTE``.
        """
        return [
            "--network", isolated_network,
            "--dns", proxy_isolated_ip,
            "--cap-add", "NET_ADMIN",
            "--env", f"DEFAULT_ROUTE={proxy_isolated_ip}",
        ]

    # ── Mounts ───────────────────────────────────────────────────────────
    def tmpfs_args(self, destination: str) -> List[str]:
        """Flags mounting a writable tmpfs at ``destination``."""
        return ["--tmpfs", destination]

    # ── Host llama-server reachability ───────────────────────────────────
    def llama_host_addr(self) -> Optional[str]:
        """Static hint for the address the proxy should DNAT llama traffic to.

        ``None`` means "let the proxy resolve its own default gateway" (the
        Apple ``container`` case, where the gateway is the host bridge IP that
        socat listens on).
        """
        return None

    def resolve_llama_host_addr(self, probe_image: Optional[str] = None) -> Optional[str]:
        """Concrete value to inject as the proxy's ``LLAMA_HOST_ADDR``.

        Defaults to the static :meth:`llama_host_addr`. Runtimes whose host
        hostname is not reliably resolvable inside the proxy override this to
        return a numeric IP instead. ``probe_image`` is a container image the
        implementation may run to perform the lookup.
        """
        return self.llama_host_addr()

    def needs_host_socat(self) -> bool:
        """Whether ``llama-server`` must be re-exposed on the host bridge via socat."""
        return False


class AppleContainerRuntime(ContainerRuntime):
    """Apple's ``container`` CLI (macOS default).

    Containers share the host ``bridge100`` L2 network, so the host reaches them
    directly and ``llama-server`` is re-exposed on the bridge IP via socat. The
    proxy resolves that bridge IP as its own default gateway, so no explicit
    ``LLAMA_HOST_ADDR`` is injected.
    """

    name = "container"
    default_bridge_interface = "bridge100"
    default_upstream_network = "default"

    def proxy_network_args(self, isolated_network: str) -> List[str]:
        # First --network becomes eth0, second becomes eth1.
        return ["--network", self.upstream_network, "--network", isolated_network]

    def needs_host_socat(self) -> bool:
        return True


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

    def create_isolated_network_argv(self, network_name: str) -> List[str]:
        # --disable-dns stops podman's aardvark-dns from occupying the network's
        # .1 address and shadowing the agent's resolver. Without it the agent's
        # resolv.conf points at aardvark instead of the proxy, so the "llama"
        # hostname (served by the proxy's mitmproxy DNS) never resolves and
        # traffic is not intercepted.
        return ["network", "create", "--internal", "--disable-dns", network_name]

    def proxy_network_args(self, isolated_network: str) -> List[str]:
        # Attach both networks at run time and pin interface names. Podman does
        # not name interfaces by --network order, so pinning is required to keep
        # the isolated network on eth1 (matching the proxy entrypoint).
        return [
            "--network", f"{self.upstream_network}:interface_name=eth0",
            "--network", f"{isolated_network}:interface_name=eth1",
        ]

    def tmpfs_args(self, destination: str) -> List[str]:
        return ["--mount", f"type=tmpfs,tmpfs-mode=1777,destination={destination}"]

    def proxy_extra_run_args(self) -> List[str]:
        # Rootless podman forbids writing net.ipv4.ip_forward from inside the
        # container, so set it at run time instead.
        return ["--sysctl", "net.ipv4.ip_forward=1"]

    def llama_host_addr(self) -> Optional[str]:
        return self.HOST_INTERNAL_HOSTNAME

    def resolve_llama_host_addr(self, probe_image: Optional[str] = None) -> Optional[str]:
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
                    [self.name, "run", "--rm", "--network", self.upstream_network,
                     "--entrypoint", "getent", probe_image, "hosts", self.HOST_INTERNAL_HOSTNAME],
                    capture_output=True, text=True, timeout=20,
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

    def proxy_network_args(self, isolated_network: str) -> List[str]:
        # Only the upstream network is attached at run time; the isolated
        # network is connected post-run (see proxy_secondary_connect_argv).
        return ["--network", self.upstream_network]

    def proxy_secondary_connect_argv(
        self, proxy_name: str, isolated_network: str
    ) -> Optional[List[str]]:
        return ["network", "connect", isolated_network, proxy_name]

    def proxy_extra_run_args(self) -> List[str]:
        return ["--sysctl", "net.ipv4.ip_forward=1"]

    def llama_host_addr(self) -> Optional[str]:
        return "host.docker.internal"

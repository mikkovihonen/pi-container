import sys

sys.dont_write_bytecode = True

"""Container-runtime abstraction layer.

``run.py`` orchestrates four moving parts — the host ``llama-server``, the
``pi-coding-agent`` container, the ``pi-coding-agent-proxy`` container and the
container network that isolates them. Every runtime-specific CLI flag and
networking decision is encapsulated in a :class:`ContainerRuntime` subclass so
``run.py`` stays runtime-agnostic.

``podman`` is the only supported runtime. :class:`ContainerRuntime` is kept as
the seam a future runtime would attach to; :class:`PodmanRuntime` is currently
its single subclass. Docker was dropped deliberately — see
``docs/design/nested-containers.md`` ("Dropping Docker"): stock Docker gives the
agent container no user namespace, so container uid 0 *is* host uid 0, and the
flag set nested containers need (``/dev/fuse``, ``/dev/net/tun``, relaxed
SELinux, a nested container engine) is only well-contained under a userns.

Network model
-------------
* The isolated network is created ``--internal`` (no external gateway). A
  container attached only to it therefore has **no default route** — verified.
* The **proxy** attaches to two networks: the upstream network (``eth0`` →
  internet, NAT/MASQUERADE) and the isolated network (``eth1`` → agent).
* The **agent** attaches only to the isolated network and has its default route
  and DNS pointed at the proxy's ``eth1`` IP. Because the isolated network has
  no gateway of its own, the default route is injected via the ``DEFAULT_ROUTE``
  env var + ``NET_ADMIN`` (the entrypoint runs ``ip route replace default``).
* The proxy reaches the host ``llama-server`` (``LLAMA_HOST_ADDR``) through
  ``host.containers.internal``: podman runs inside a Linux VM on macOS and
  gvproxy forwards that name to the host loopback, so no socat is needed.
"""

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


def _vm_ipv6_run_args(enabled: bool, forwarding: bool) -> list[str]:
    """``--sysctl`` flags enforcing the IPv6 policy for a VM runtime.

    Rootless podman namespaces forbid writing ``net.*`` sysctls from inside the
    container, so the policy is pinned at ``run`` time:

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
    #: Whether the runtime's upstream network is assumed to route IPv6 to the
    #: internet at all. False short-circuits the IPv6 preflight in
    #: ``network.py::_preflight_ipv6_egress`` with a clear warning.
    ipv6_upstream_egress: bool = True

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

        podman accepts ``--ipv6`` and auto-assigns a ULA subnet.
        """
        return ["--ipv6"]

    def create_isolated_network_argv(self, network_name: str, ipv6: bool = False) -> list[str]:
        """Argv (after the CLI binary) that creates the internal isolated network.

        When ``ipv6`` is set, the runtime-specific IPv6 flags (see
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
        """Argv that removes the isolated network on shutdown (``None`` to skip)."""
        return ["network", "rm", network_name]

    # ── Proxy container networking ───────────────────────────────────────
    @abstractmethod
    def proxy_network_args(self, isolated_network: str) -> list[str]:
        """``run`` flags attaching the proxy to upstream (eth0) + isolated (eth1)."""

    def proxy_extra_run_args(self) -> list[str]:
        """Extra runtime-specific ``run`` flags for the proxy container."""
        return []

    def ipv6_run_args(self, enabled: bool, forwarding: bool = False) -> list[str]:
        """``run`` flags that enforce the IPv6 policy for a container.

        The base class returns nothing: a VM runtime like podman overrides this
        because its rootless network namespace forbids writing ``net.*`` sysctls
        from inside the container, so the toggle must be set at ``run`` time via
        ``--sysctl``.

        ``forwarding`` is only meaningful for the proxy (which routes traffic);
        endpoint containers like the agent leave it False.
        """
        return []

    # ── Agent container networking ───────────────────────────────────────
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

    # ── Nested containers (config.yaml nested_containers) ────────────────
    # These are plain OCI ``run`` flags, so they live on the base class; a future
    # runtime whose nesting story differs overrides them.
    def nested_container_args(self, cfg: dict, project_hash: str) -> list[str]:
        """``run`` flags letting the agent run its own rootless podman inside itself.

        Returns ``[]`` when ``nested_containers.enabled`` is false (the default),
        so nesting costs nothing but the toolchain's disk footprint in the image.

        When enabled, containers the agent starts are **children** — in its mount
        and network namespaces — so they inherit its routing (hence the proxy) and
        can bind-mount its view of ``/workspace`` directly. The flags are:

        * ``--device /dev/fuse`` — ``fuse-overlayfs`` fallback when native
          rootless overlay is unavailable.
        * ``--device /dev/net/tun`` — ``pasta`` needs it to create
          the inner tap device. This grants no egress: the only route off the
          isolated network is still the proxy, whose FORWARD policy is DROP.
        * ``--security-opt label=...`` — see :meth:`_nested_security_opt`.
        * ``--security-opt unmask=ALL`` + ``--cap-add SYS_ADMIN`` — the two
          requirements that were *measured*, not predicted (see below).
        * the nested image store at ``/home/pi/.local/share/containers``, either a
          persistent per-project named volume (``storage: volume``) or a tmpfs
          (``storage: tmpfs``). It cannot live on the agent's default paths:
          ``/home/pi`` is tmpfs, and the container rootfs is overlayfs (podman
          would degrade to the full-copy ``vfs`` driver on overlay-on-overlay).
        * ``XDG_RUNTIME_DIR`` — rootless podman's lock/pid directory, created by
          the entrypoint (which is gated on ``NESTED_CONTAINERS``).

        Why ``unmask=ALL`` and ``SYS_ADMIN`` are both needed — each was isolated
        by starting an inner ``alpine`` container from the agent image:

        * without ``unmask=ALL`` → ``crun: open /proc/sys/net/ipv4/ping_group_range:
          Read-only file system``. Podman bind-mounts parts of ``/proc`` read-only
          (and masks others) in the agent container; those mounts are *locked* in
          the nested user namespace, so the inner runtime cannot set its sysctls.
        * without ``SYS_ADMIN`` → ``crun: sethostname: Operation not permitted``.
          Podman's default seccomp profile allows ``mount``/``sethostname``/
          ``umount2``/``pivot_root`` only when ``CAP_SYS_ADMIN`` is in the
          container's capability set, and a seccomp filter cannot be relaxed from
          inside a nested user namespace — even though the nested namespace's own
          capability set is full (measured: ``CapEff: 000001ffffffffff``).

        ``CAP_SYS_ADMIN`` here is **namespaced**: the agent container's user
        namespace maps container uid 0 to an unprivileged host user, so the
        capability confers power only over what that namespace owns — not over the
        host. Still absent: ``--privileged``, ``seccomp=unconfined``, ``--userns``
        overrides, and any host socket. Egress interception is unaffected —
        verified: with the agent on the ``--internal`` network, an inner container
        cannot reach even a raw IP.
        """
        if not cfg.get("enabled"):
            return []
        store = "/home/pi/.local/share/containers"
        storage_args = (
            self.tmpfs_args(store)
            if cfg.get("storage") == "tmpfs"
            else ["--volume", f"{self.nested_volume_name(project_hash)}:{store}"]
        )
        return [
            "--device",
            "/dev/fuse",
            "--device",
            "/dev/net/tun",
            "--security-opt",
            self._nested_security_opt(str(cfg.get("security") or "disable")),
            "--security-opt",
            "unmask=ALL",
            "--cap-add",
            "SYS_ADMIN",
            *storage_args,
            "--env",
            "XDG_RUNTIME_DIR=/run/user/1000",
            "--env",
            "NESTED_CONTAINERS=true",
        ]

    def nested_port_args(self, cfg: dict) -> list[str]:
        """``-p`` flags publishing nested-container UI ports to the host.

        A nested container's own ``-p 3000:3000`` binds inside the **agent's**
        network namespace, which the host cannot reach — the agent container has
        to publish the same port onward, and a container's published ports are
        fixed at start. Hence the explicit ``nested_containers.ports.publish``
        list rather than anything discovered at runtime.

        Two things had to be true for this to work, and both were measured with
        the agent on the ``--internal`` network:

        * The nested container must be on a **netavark bridge**, not podman 6's
          rootless default of ``pasta``. Under pasta the outer port forward
          reaches the agent netns and the handshake even completes, then the flow
          stalls: the connection arrives carrying the agent's own address as its
          source, which is also the address pasta hands the nested guest. Under
          bridge, ``rootlessport`` binds a real socket in the agent netns and the
          outer ``-p`` publishes it. That default is set image-side, in the
          ``containers.conf`` drop-in (``[containers] netns = "bridge"``).
        * Publishing works at all on an ``--internal`` network — it has no
          gateway, but ``-p`` forwards within the runtime's own namespace and
          never needed one.

        This is **inbound only** and creates no egress: the proxy is still the
        agent's only route out, verified unchanged with a nested container on a
        bridge. It does widen what can reach the agent, which is why ``expose``
        defaults to ``localhost`` and ``publish`` ships empty.
        """
        if not cfg.get("enabled"):
            return []
        ports = cfg.get("ports") or {}
        host_bind = "127.0.0.1:" if ports.get("expose", "localhost") != "lan" else ""
        return [flag for host, agent in ports.get("publish") or [] for flag in ("-p", f"{host_bind}{host}:{agent}")]

    @staticmethod
    def nested_volume_name(project_hash: str) -> str:
        """Name of this workspace's persistent nested-image-store volume."""
        return f"pi-nested-{project_hash}"

    @staticmethod
    def _nested_security_opt(security: str) -> str:
        """The agent container's SELinux label for nesting.

        On an SELinux-enforcing host ``/dev/net/tun`` is inaccessible under the
        default ``container_t`` label, so one of two labels is needed:

        * ``disable`` (default) → ``label=disable``. Nested containers need no
          special flags. The agent loses SELinux *type* confinement but keeps the
          user namespace, rootlessness, seccomp, the capability bounding set, the
          routing dead end, and the absence of any host socket.
        * ``engine_t`` → ``label=type:container_engine_t``. The agent stays
          confined, but **every** nested container must then be run with
          ``--security-opt unmask=all`` or it fails in ``crun`` (``mount tmpfs to
          proc/acpi: Permission denied``). That cannot be made transparent —
          ``unmask`` is not a ``containers.conf`` key — so it breaks the
          API-socket workflows this feature exists for.
        """
        return "label=type:container_engine_t" if security == "engine_t" else "label=disable"

    # ── Host llama-server reachability ──────────────────────────────────
    # PodmanRuntime overrides llama_host_addr()/resolve_llama_host_addr() to
    # return the hostname (or numeric IP) that resolves the host loopback
    # (gvproxy on macOS). The proxy uses this as LLAMA_HOST_ADDR for DNAT.


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

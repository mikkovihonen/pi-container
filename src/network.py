import sys

sys.dont_write_bytecode = True

"""Container network + proxy lifecycle management."""

import fcntl
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import yaml

from config import ADMIN_PASSWORD, CONFIG_DIR, REPO_ROOT
from runtimes import ContainerRuntime
from util import get_free_port, run_quiet

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Project config (.pi-container/config.yaml) ───────────────────────────
#
# The per-project orchestration config: container resource limits, transient
# tmpfs paths, flow-export toggle, and proxy egress policy — all in one file.
# (The proxy addon configs allowlist.yaml / token_replacer.yaml stay separate:
# they have their own schema and are bind-mounted into the proxy container.)

# Container resource limits applied when values are present (a falsy/null value
# omits the flag → unlimited). Defaults preserve the agent's historical 16g/8.
_DEFAULT_RESOURCES = {
    "agent": {"memory": "16g", "cpus": 8},
    "proxy": {"memory": "4g", "cpus": 4},
}

# Proxy egress policy: YAML key under ``egress.allow`` → the ``PROXY_ALLOW_*``
# env var the proxy entrypoint reads to open that protocol's (uninspected) rule.
_PROXY_FORWARD_FLAGS = {
    "ssh": "PROXY_ALLOW_SSH",
    "smtp": "PROXY_ALLOW_SMTP",
    "git": "PROXY_ALLOW_GIT",
    "ntp": "PROXY_ALLOW_NTP",
}
_PROXY_FORWARD_PORTS = {
    "tcp_ports": "PROXY_ALLOW_TCP_PORTS",
    "udp_ports": "PROXY_ALLOW_UDP_PORTS",
}


def load_project_config(config_dir: Path | None = None) -> dict:
    """Load ``.pi-container/config.yaml``. Returns ``{}`` if absent or malformed.

    The single source of truth for per-project orchestration settings. Individual
    accessors (``scan_tmpfs_paths``, ``read_flow_export_enabled``,
    ``read_proxy_forward_env``, ``read_resource_limits``) read their section from
    the returned mapping.
    """
    import yaml as _yaml

    config_path = (config_dir or CONFIG_DIR) / "config.yaml"
    if not config_path.exists():
        logger.debug(f"Project config not found at {config_path}; using defaults.")
        return {}

    try:
        with config_path.open("r") as f:
            return _yaml.safe_load(f) or {}
    except (OSError, _yaml.YAMLError) as e:
        logger.warning(f"Could not read project config {config_path}: {e}; using defaults.")
        return {}


def scan_tmpfs_paths(config_dir: Path | None = None) -> list[str]:
    """Transient tmpfs mount paths from config.yaml ``tmpfs.paths``.

    Returns a deduplicated, sorted list of absolute container paths to mount as
    tmpfs (volatile RAM disks). Empty when the section is absent.
    """
    tmpfs = load_project_config(config_dir).get("tmpfs") or {}
    paths = tmpfs.get("paths", []) or []
    return sorted({str(p) for p in paths})


def read_flow_export_enabled(config_dir: Path | None = None, default: bool = False) -> bool:
    """The mitmweb flow-export toggle from config.yaml ``flow_export.enabled``.

    When enabled, the proxy's captured HTTP/HTTPS flow history is exported
    (bucketed by UTC date) under ``.pi-container/exports/`` after the agent shuts
    down. Returns ``default`` when the section is absent (fail-safe: no capture
    unless a workspace opts in).
    """
    flow_export = load_project_config(config_dir).get("flow_export") or {}
    return bool(flow_export.get("enabled", default))


def _egress_truthy(value: object) -> bool:
    """Match the proxy entrypoint's truthiness (true/1/yes/on), YAML bools included."""
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def read_proxy_forward_env(config_dir: Path | None = None) -> dict[str, str]:
    """The proxy egress policy from config.yaml ``egress.allow`` → ``PROXY_ALLOW_*``.

    Returns the env dict passed to the proxy container, whose entrypoint opens the
    corresponding (UNINSPECTED) FORWARD rules. Only HTTP/HTTPS/DNS are intercepted
    by mitmproxy; every other protocol is denied by default, so an absent section
    yields ``{}`` (deny-all).

    Expected shape::

        egress:
          allow:
            ssh: true            # → PROXY_ALLOW_SSH=true
            tcp_ports: [2222]    # → PROXY_ALLOW_TCP_PORTS=2222

    Ports accept a list or a comma-separated string. Only truthy flags and
    non-empty port lists are emitted.
    """
    allow = (load_project_config(config_dir).get("egress") or {}).get("allow") or {}
    env: dict[str, str] = {}
    for key, var in _PROXY_FORWARD_FLAGS.items():
        if _egress_truthy(allow.get(key)):
            env[var] = "true"
    for key, var in _PROXY_FORWARD_PORTS.items():
        ports = allow.get(key) or []
        joined = ports.strip() if isinstance(ports, str) else ",".join(str(p).strip() for p in ports if str(p).strip())
        if joined:
            env[var] = joined
    return env


def read_resource_limits(config_dir: Path | None = None, which: str = "agent") -> dict:
    """Resource limits for ``which`` ('agent'|'proxy') from config.yaml ``resources``.

    Returns ``{"memory": ..., "cpus": ...}``, filling any missing value from
    :data:`_DEFAULT_RESOURCES`. A value set to null/empty in config.yaml is passed
    through as falsy so :func:`resource_limit_args` omits the flag (unlimited).
    """
    section = (load_project_config(config_dir).get("resources") or {}).get(which) or {}
    defaults = _DEFAULT_RESOURCES[which]
    return {
        "memory": section.get("memory", defaults["memory"]),
        "cpus": section.get("cpus", defaults["cpus"]),
    }


def resource_limit_args(limits: dict) -> list[str]:
    """Build ``--memory``/``--cpus`` run flags, omitting falsy (null) values."""
    args: list[str] = []
    if limits.get("memory"):
        args += ["--memory", str(limits["memory"])]
    if limits.get("cpus"):
        args += ["--cpus", str(limits["cpus"])]
    return args


_DEFAULT_LLAMA = {"startup_timeout": 180, "startup_attempts": 2}
_DEFAULT_NETWORK = {"ipv6": False, "dns": "1.1.1.1"}


def read_llama_config(config_dir: Path | None = None) -> dict:
    """llama-server startup tuning from config.yaml ``llama``.

    Returns ``{"startup_timeout": <seconds>, "startup_attempts": <int>}`` — how
    long to wait for ``/health`` and how many times to (re)launch a model before
    giving up. Missing values fall back to :data:`_DEFAULT_LLAMA`.
    """
    section = load_project_config(config_dir).get("llama") or {}
    return {
        "startup_timeout": int(section.get("startup_timeout", _DEFAULT_LLAMA["startup_timeout"])),
        "startup_attempts": int(section.get("startup_attempts", _DEFAULT_LLAMA["startup_attempts"])),
    }


def read_network_config(config_dir: Path | None = None) -> dict:
    """Network settings from config.yaml ``network``.

    Returns ``{"ipv6": <bool>, "dns": <str>}`` — whether to plumb IPv6 through the
    isolated network/proxy, and the upstream resolver the proxy uses. Missing
    values fall back to :data:`_DEFAULT_NETWORK`.
    """
    section = load_project_config(config_dir).get("network") or {}
    return {
        "ipv6": _egress_truthy(section.get("ipv6", _DEFAULT_NETWORK["ipv6"])),
        "dns": str(section.get("dns") or _DEFAULT_NETWORK["dns"]),
    }


def read_proxy_ui_expose(config_dir: Path | None = None) -> str:
    """Where the proxy's mitmweb UI is published, from config.yaml ``proxy.expose_ui``.

    ``"localhost"`` (default) binds the auto-assigned port to 127.0.0.1 only;
    ``"lan"`` binds 0.0.0.0 (reachable from other hosts). Unknown values fall back
    to ``"localhost"``.
    """
    section = load_project_config(config_dir).get("proxy") or {}
    value = str(section.get("expose_ui", "localhost")).strip().lower()
    return value if value in ("localhost", "lan") else "localhost"


def read_agent_extras(config_dir: Path | None = None) -> dict:
    """Extra agent-container env vars and bind mounts from config.yaml ``agent``.

    Returns ``{"env": {NAME: value, ...}, "mounts": ["host:container[:ro]", ...]}``
    — passed through verbatim as ``--env``/``--volume`` flags. Use absolute host
    paths for mounts. Empty when the section is absent.
    """
    section = load_project_config(config_dir).get("agent") or {}
    env = section.get("env") or {}
    mounts = section.get("mounts") or []
    return {
        "env": {str(k): str(v) for k, v in dict(env).items()},
        "mounts": [str(m) for m in mounts],
    }


# ─── Token Replacer Config Scanner (duplicated from pi-coding-agent-proxy/addons/token_replacer)
#
# run.py lives outside pi-coding-agent-proxy and must not import from it.
# This function is duplicated here so run.py can scan the host-side token
# replacer config for ${ENV:VAR} references and pull required secrets from
# the host environment / secret store before launching the container.
#
# Source of truth: addons/token_replacer/token_replacer.py
# ──────────────────────────────────────────────────────────────────────────

_TOKEN_REPLACER_ENV_VAR_REQUIRED_PATTERN = re.compile(r"^\$\{ENV:([^,}]+)\}$")


def scan_config_env_refs(config: dict) -> list[str]:
    """Scan a parsed config dict for required env-var references.

    Finds all ``${ENV:VAR}`` references (no default value) in
    ``replace_with.value`` fields across every rule. These are the values
    the host must pull from a secret store before launching the container.

    Args:
        config: The parsed YAML config dict (as returned by ``yaml.safe_load``).

    Returns:
        A deduplicated, sorted list of env var names that are required.
    """
    refs: set[str] = set()
    for rule in config.get("rules", []):
        replace = rule.get("replace_with", {}) or {}
        value = replace.get("value", "")
        m = _TOKEN_REPLACER_ENV_VAR_REQUIRED_PATTERN.match(str(value))
        if m:
            refs.add(m.group(1))
    return sorted(refs)


# ─── Container Network Manager ───────────────────────────────────────────


class ContainerNetworkManager:
    def __init__(
        self,
        container_runtime: str | ContainerRuntime,
        network_name: str,
        proxy_image: str,
        proxy_name: str = "proxy",
        config_dir: Path | None = None,
        llama_ports: str | None = None,
        ipv6: bool = False,
    ) -> None:
        # Accept either a runtime name (used by tests) or a ready ContainerRuntime.
        self.runtime: ContainerRuntime = (
            container_runtime
            if isinstance(container_runtime, ContainerRuntime)
            else ContainerRuntime.create(container_runtime)
        )
        self.container_runtime: str = self.runtime.name
        self.network_name: str = network_name
        self.proxy_image: str = proxy_image
        self.proxy_name: str = proxy_name
        self.config_dir: Path = config_dir or CONFIG_DIR
        self.llama_ports: str | None = llama_ports
        self.ipv6: bool = ipv6
        # Host port the proxy's mitmweb UI is published on. Auto-assigned when
        # this process starts the proxy; resolved from the running container when
        # attaching to an already-running one. See ``mitmweb_url``.
        self.mitmweb_port: int | None = None

        # Directory for cross-process synchronization. Kept under the repo (not a
        # user workspace) so lock files never pollute projects. The proxy is
        # per-project, so refcount files are keyed by proxy_name — each workspace
        # refcounts (and tears down) its own proxy independently.
        self.lock_dir: Path = REPO_ROOT / "pi-coding-agent-proxy" / ".locks"
        self.paths: dict[str, Path] = {
            "lock_dir": self.lock_dir,
            "ref_count_lock": self.lock_dir / f".{self.proxy_name}.lock",
            "ref_count_file": self.lock_dir / f".{self.proxy_name}.refcount",
        }

    def _pull_secrets_from_config(self) -> dict[str, str]:
        """Pull secrets required by the mounted token_replacer config.

        Scans the config file for ``${ENV:VAR}`` references (no default value)
        in ``replace_with.value`` fields, then resolves each one from the host
        environment. Returns a dict of var_name -> value for non-empty values.

        Override this method to integrate with a host secret store
        (Vault, AWS Secrets Manager, Azure Key Vault, etc.).
        """
        config_path = self.config_dir / "token_replacer.yaml"
        if not config_path.exists():
            logger.warning(
                f"Token replacer config not found at {config_path}; "
                f"no secrets will be injected into the proxy container."
            )
            return {}

        with config_path.open("r") as f:
            config = yaml.safe_load(f) or {}

        required_vars = scan_config_env_refs(config)
        if not required_vars:
            logger.info("No required env-var secrets found in token replacer config.")
            return {}

        secrets: dict[str, str] = {}
        for var in required_vars:
            value = os.environ.get(var, "")
            if value:
                secrets[var] = value
            else:
                logger.warning(
                    f"Required secret '{var}' (referenced by token replacer config) "
                    f"is not set in the host environment. The container will use "
                    f"the config's fallback default, if any."
                )
        return secrets

    def _env_flags(self, secrets: dict[str, str]) -> list[str]:
        """Convert a secrets dict into ``--env KEY=VALUE`` flag pairs."""
        flags: list[str] = []
        for k, v in sorted(secrets.items()):
            flags.extend(["--env", f"{k}={v}"])
        return flags

    def _preflight_ipv6_egress(self) -> None:
        """Warn if IPv6 was requested but the runtime likely can't egress it.

        Layered, cheapest first:
          1. Static per-runtime capability (``ipv6_upstream_egress``) — short-
             circuit for runtimes known to NAT IPv4 only (e.g. Apple container).
          2. Inspect the upstream network's config for IPv6 enablement.
        The definitive check against the proxy's real ``eth0`` happens after the
        proxy starts (in run.py).
        """
        # (1) Static: runtime known to lack IPv6 egress entirely.
        if not self.runtime.ipv6_upstream_egress:
            logger.warning(
                f"network.ipv6=true but runtime '{self.container_runtime}' provides no "
                f"IPv6 egress on the upstream network (it NATs IPv4 only); the proxy will "
                f"have no IPv6 route out, so agent IPv6 connections will fail. Set "
                f"network.ipv6=false in .pi-container/config.yaml unless you are on a "
                f"runtime/host with working IPv6 egress."
            )
            return

        # (2) Inspect the upstream network config.
        has_v6 = self.runtime.upstream_network_has_ipv6()
        if has_v6 is False:
            logger.warning(
                f"network.ipv6=true but the upstream network '{self.runtime.upstream_network}' "
                f"is not configured for IPv6; the proxy will likely have no IPv6 route out and "
                f"agent IPv6 connections may fail."
            )
        elif has_v6 is None:
            logger.info(
                f"Could not determine IPv6 config of upstream network "
                f"'{self.runtime.upstream_network}'; proceeding (egress is re-checked on the "
                f"running proxy)."
            )

    def warn_if_proxy_lacks_ipv6_egress(self, upstream_iface: str = "eth0") -> None:
        """Confirm the *running* proxy actually has IPv6 egress on its upstream NIC.

        This is the definitive, observed check: static capability and
        upstream-network config are checked preflight in :meth:`_preflight_ipv6_egress`,
        but only the live proxy tells us whether ``eth0`` got a global v6 address AND a
        v6 default route. Warns (does not fail) when either is missing, since IPv4
        still works and the agent's own IPv6 is disabled unless a route was found.
        """
        try:
            addr = subprocess.run(
                [self.container_runtime, "exec", self.proxy_name, "ip", "-6", "addr", "show", upstream_iface],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            route = subprocess.run(
                [self.container_runtime, "exec", self.proxy_name, "ip", "-6", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception as e:
            logger.warning(f"Could not verify proxy IPv6 egress: {e}")
            return

        has_global = bool(re.search(r"inet6\s+[0-9a-fA-F:]+/\d+\s+scope global", addr))
        has_default = bool(route.strip())
        if not (has_global and has_default):
            logger.warning(
                f"network.ipv6=true but the proxy has no working IPv6 egress on {upstream_iface} "
                f"(global address: {has_global}, default route: {has_default}); this runtime/host "
                f"does not route IPv6 to the internet, so agent IPv6 connections will fail. "
                f"Set network.ipv6=false in .pi-container/config.yaml to silence this."
            )
        else:
            logger.info(f"Proxy has IPv6 egress on {upstream_iface} (global address + default route present).")

    def __enter__(self) -> ContainerNetworkManager:
        self.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any | None) -> None:
        self.stop()

    def start(self) -> None:
        self.paths["lock_dir"].mkdir(exist_ok=True, parents=True)

        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            ref_count = self._get_ref_count()
            if ref_count == 0:
                self._actually_start()

            ref_count += 1
            self.paths["ref_count_file"].write_text(str(ref_count))

    def stop(self) -> None:
        should_full_cleanup = False
        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            ref_count = self._get_ref_count()
            if ref_count <= 1:
                self._actually_stop()
                should_full_cleanup = True
            else:
                ref_count -= 1
                self.paths["ref_count_file"].write_text(str(ref_count))

        if should_full_cleanup:
            self.paths["ref_count_file"].unlink(missing_ok=True)
            try:
                self.paths["ref_count_lock"].unlink(missing_ok=True)
                if self.paths["lock_dir"].exists() and not any(self.paths["lock_dir"].iterdir()):
                    self.paths["lock_dir"].rmdir()
            except OSError:
                pass

    def _get_ref_count(self) -> int:
        if self.paths["ref_count_file"].exists():
            try:
                return int(self.paths["ref_count_file"].read_text().strip())
            except ValueError:
                return 0
        return 0

    def _actually_start(self) -> None:
        # Pull secrets required by the mounted token_replacer config
        secrets = self._pull_secrets_from_config()

        # IPv6 can be plumbed on the isolated side, but it only works end-to-end
        # if the runtime also provides IPv6 egress on the upstream network.
        # Preflight this so we warn rather than fail confusingly later.
        if self.ipv6:
            self._preflight_ipv6_egress()

        # Create the isolated internal network (skip if it already exists).
        logger.info(f"Checking network {self.network_name}...")
        result = subprocess.run(
            [self.container_runtime, "network", "inspect", self.network_name], capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.info(f"Creating network {self.network_name} (ipv6={self.ipv6})...")
            run_quiet(
                [self.container_runtime, *self.runtime.create_isolated_network_argv(self.network_name, ipv6=self.ipv6)],
                label=f"create network {self.network_name}",
                logger=logger,
            )
        else:
            logger.info(f"Network {self.network_name} already exists, skipping creation.")

        # Start proxy container. It attaches to the upstream network (eth0 →
        # internet) and the isolated network (eth1 → agent). LLAMA_HOST_ADDR
        # tells the proxy where to DNAT llama-server traffic; when unset the
        # proxy falls back to resolving its own default gateway.
        logger.info(f"Starting proxy container {self.proxy_name} from {self.proxy_image}...")
        llama_host_addr = self.runtime.resolve_llama_host_addr(self.proxy_image)

        # Publish the mitmweb UI on an auto-assigned host port so multiple
        # per-project proxies can coexist (the old fixed 8081 could bind once).
        self.mitmweb_port = get_free_port()

        # Mount the host addon configs over the image's baked defaults. Each is
        # only mounted if present so a missing host config falls back to the
        # (fail-closed) default baked into the image.
        config_mounts: list[str] = []
        for host_name, container_path in (
            ("token_replacer.yaml", "/home/mitmproxy/config/token_replacer.yaml"),
            ("allowlist.yaml", "/home/mitmproxy/config/allowlist.yaml"),
        ):
            host_path = self.config_dir / host_name
            if host_path.exists():
                config_mounts += ["--volume", f"{host_path}:{container_path}:ro"]
            else:
                logger.warning(f"Addon config {host_path} not found; using the image default.")

        # Mount a host directory for flow exports. The flow_export addon appends
        # each flow to a per-client-IP JSON Lines file (flows-<ip>.jsonl) under
        # /home/mitmproxy/exports inside the container; this mount makes them
        # accessible to run.py on the host after the session. The host directory
        # must exist first: podman refuses to auto-create a bind-mount source and
        # the container would fail to start (surfacing as a 30s health-probe
        # hang, since the run error → DEVNULL).
        exports_host_dir = self.config_dir / "exports"
        exports_container_path = "/home/mitmproxy/exports"
        # Only mount the exports volume and enable the addon when the user has
        # opted in (flow_export.enabled = true). When disabled, the addon stays
        # silent (see FLOW_EXPORT_ENABLED below) so no raw flows-<ip>.jsonl files
        # pollute the host-side exports directory.
        flow_export_enabled = read_flow_export_enabled(self.config_dir)
        exports_mounts: list[str]
        if flow_export_enabled:
            exports_host_dir.mkdir(parents=True, exist_ok=True)
            exports_mounts = [
                "--volume",
                f"{exports_host_dir}:{exports_container_path}",
            ]
        else:
            exports_mounts = []

        cmd = [
            self.container_runtime,
            "run",
            "-d",
            "--rm",
            "--name",
            self.proxy_name,
            *self.runtime.proxy_network_args(self.network_name),
            *self.runtime.proxy_extra_run_args(),
            "--cap-add",
            "NET_ADMIN",
            # Upstream resolver for the proxy (config.yaml network.dns).
            "--dns",
            read_network_config(self.config_dir)["dns"],
            # Resource limits for this project's proxy (config.yaml resources.proxy).
            *resource_limit_args(read_resource_limits(self.config_dir, "proxy")),
            # Publish the mitmweb UI on the auto-assigned port; bind scope from
            # config.yaml proxy.expose_ui (localhost by default, or lan → 0.0.0.0).
            *self._mitmweb_publish_args(),
            # IPv6 policy: --sysctl toggle (VM runtimes) + env flag the proxy
            # entrypoint reads to mirror (or tear down) v6 firewall rules.
            *self.runtime.ipv6_run_args(self.ipv6, forwarding=True),
            "--env",
            f"IPV6_ENABLED={str(self.ipv6).lower()}",
            "--env",
            f"ADMIN_PASSWORD={ADMIN_PASSWORD}",
            *(["--env", f"LLAMA_PORTS={self.llama_ports}"] if self.llama_ports else []),
            *(["--env", f"LLAMA_HOST_ADDR={llama_host_addr}"] if llama_host_addr else []),
            # Per-protocol forwarding opt-ins (uninspected protocols), read from
            # this project's config.yaml (egress.allow).
            *[flag for k, v in read_proxy_forward_env(self.config_dir).items() for flag in ("--env", f"{k}={v}")],
            *config_mounts,
            *exports_mounts,
            # Flow export toggle — forwarded to the proxy addon so it skips
            # capture entirely when the user has disabled it in config.yaml.
            "--env",
            f"FLOW_EXPORT_ENABLED={str(flow_export_enabled).lower()}",
            *self._env_flags(secrets),
            self.proxy_image,
        ]
        # Label (not the argv) is what surfaces on failure — cmd carries
        # ADMIN_PASSWORD and must never reach logs/tracebacks.
        run_quiet(cmd, label=f"start proxy container {self.proxy_name}", logger=logger)

        # Some runtimes (Docker) attach the isolated network only after run.
        connect_argv = self.runtime.proxy_secondary_connect_argv(self.proxy_name, self.network_name)
        if connect_argv:
            run_quiet(
                [self.container_runtime, *connect_argv],
                label=f"connect {self.proxy_name} to {self.network_name}",
                logger=logger,
            )

        # Wait for proxy to be ready via health probe
        self._wait_for_proxy_health(timeout=30)

    def _actually_stop(self) -> None:
        logger.info(f"Stopping proxy container {self.proxy_name}...")
        # Teardown is best-effort: a container/network that is already gone must
        # not abort cleanup, so check=False (log a warning, don't raise).
        run_quiet(
            [self.container_runtime, "stop", self.proxy_name],
            check=False,
            label=f"stop proxy container {self.proxy_name}",
            logger=logger,
        )

        delete_argv = self.runtime.delete_isolated_network_argv(self.network_name)
        if delete_argv:
            logger.info(f"Removing network {self.network_name}...")
            run_quiet(
                [self.container_runtime, *delete_argv],
                check=False,
                label=f"remove network {self.network_name}",
                logger=logger,
            )

    def _wait_for_proxy_health(self, timeout: int = 30) -> None:
        """Wait for the proxy container's mitmweb UI to become healthy.

        Polls the mitmweb HTTP endpoint every 2 seconds until a response is
        received (any status code means the container is alive and responding).

        Raises:
            RuntimeError: If the proxy does not become healthy within timeout.
        """
        url = f"http://127.0.0.1:{self.mitmweb_port}"
        elapsed = 0
        while elapsed < timeout:
            try:
                urllib.request.urlopen(url, timeout=2)
                logger.info(f"Proxy container is healthy ({elapsed}s)")
                return
            except urllib.error.HTTPError as e:
                # HTTP error means the server is alive, just returning a status code
                if 400 <= e.code < 500:
                    logger.info(f"Proxy container is healthy (HTTP {e.code})")
                    return
            except Exception:
                pass
            time.sleep(2)
            elapsed += 2

        raise RuntimeError(
            f"Proxy container did not become healthy within {timeout}s. "
            f"Check proxy logs: {self.container_runtime} logs {self.proxy_name}"
        )

    def _mitmweb_publish_args(self) -> list[str]:
        """``-p`` flag publishing the mitmweb UI, scoped per config.yaml proxy.expose_ui.

        ``localhost`` (default) binds 127.0.0.1 only; ``lan`` binds all interfaces.
        Either way the port is reachable on host loopback, so the health probe and
        :meth:`mitmweb_url` keep working.
        """
        host = "127.0.0.1:" if read_proxy_ui_expose(self.config_dir) == "localhost" else ""
        return ["-p", f"{host}{self.mitmweb_port}:8081"]

    def mitmweb_url(self) -> str | None:
        """Return the ``http://127.0.0.1:<port>`` URL of this project's mitmweb UI.

        When this process started the proxy, the port is already known. When it
        merely attached to an already-running proxy (refcount > 0), the port is
        discovered from the running container. Best-effort: returns None if the
        port can't be determined.
        """
        if self.mitmweb_port is None:
            self.mitmweb_port = self._query_published_port()
        return f"http://127.0.0.1:{self.mitmweb_port}" if self.mitmweb_port else None

    def _query_published_port(self) -> int | None:
        """Resolve the host port the running proxy publishes container port 8081 on.

        Parses ``<runtime> port <proxy_name> 8081/tcp`` (e.g. ``127.0.0.1:49732``
        or ``0.0.0.0:49732``). Best-effort — returns None on any error or if the
        runtime does not support the ``port`` subcommand.
        """
        try:
            out = subprocess.run(
                [self.container_runtime, "port", self.proxy_name, "8081/tcp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return None
        if out.returncode != 0 or not out.stdout.strip():
            return None
        match = re.search(r":(\d+)\s*$", out.stdout.strip().splitlines()[0])
        return int(match.group(1)) if match else None

import sys

sys.dont_write_bytecode = True

"""Container network + proxy lifecycle management."""

import fcntl
import logging
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import yaml

from config import ADMIN_PASSWORD, CONFIG_DIR, REPO_ROOT
from runtimes import ContainerRuntime
from util import run_quiet

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Tmpfs Config Scanner ─────────────────────────────────────────────────


def scan_tmpfs_paths(config_dir: Path | None = None) -> list[str]:
    """Scan the tmpfs config for transient paths to mount as tmpfs.

    Reads ``.pi-container/tmpfs.yaml`` and returns a deduplicated, sorted
    list of absolute container paths that should be mounted as tmpfs.

    Args:
        config_dir: Override the default CONFIG_DIR. Defaults to CONFIG_DIR.

    Returns:
        A deduplicated, sorted list of tmpfs mount paths.
    """
    import yaml as _yaml

    config_path = (config_dir or CONFIG_DIR) / "tmpfs.yaml"
    if not config_path.exists():
        logger.debug(f"Tmpfs config not found at {config_path}; no tmpfs mounts will be added.")
        return []

    with config_path.open("r") as f:
        config = _yaml.safe_load(f) or {}

    paths = config.get("paths", []) or []
    # Deduplicate and sort for deterministic output
    return sorted({str(p) for p in paths})


def read_flow_export_enabled(config_dir: Path | None = None, default: bool = False) -> bool:
    """Read the per-project mitmweb flow-export toggle.

    Reads the ``enabled`` flag from ``.pi-container/flow_export.yaml``. When
    enabled, the proxy's captured HTTP/HTTPS flow history is exported (bucketed by
    UTC date) under ``.pi-container/exports/`` after the agent shuts down. This is
    per-project so each workspace decides whether to keep an audit trail.

    Returns ``default`` when the file is absent or malformed (fail-safe: no
    capture unless a workspace opts in).
    """
    import yaml as _yaml

    config_path = (config_dir or CONFIG_DIR) / "flow_export.yaml"
    if not config_path.exists():
        logger.debug(f"Flow-export config not found at {config_path}; defaulting to enabled={default}.")
        return default

    try:
        with config_path.open("r") as f:
            config = _yaml.safe_load(f) or {}
    except (OSError, _yaml.YAMLError) as e:
        logger.warning(f"Could not read flow-export config {config_path}: {e}; defaulting to enabled={default}.")
        return default

    return bool(config.get("enabled", default))


# Proxy egress policy: YAML key under ``allow:`` → the ``PROXY_ALLOW_*`` env var
# the proxy entrypoint reads to open that protocol's (uninspected) FORWARD rule.
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


def _egress_truthy(value: object) -> bool:
    """Match the proxy entrypoint's truthiness (true/1/yes/on), YAML bools included."""
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def read_proxy_forward_env(config_dir: Path | None = None) -> dict[str, str]:
    """Read the per-project proxy egress policy into ``PROXY_ALLOW_*`` env vars.

    Reads ``.pi-container/egress.yaml`` and returns the env dict passed to the
    proxy container, whose entrypoint opens the corresponding (UNINSPECTED)
    FORWARD rules. Only HTTP/HTTPS/DNS are intercepted by mitmproxy; every other
    protocol is denied by default, so an absent/empty file yields ``{}`` (deny).

    Expected shape::

        allow:
          ssh: true            # → PROXY_ALLOW_SSH=true
          smtp: false
          git: false
          ntp: false
          tcp_ports: [2222]    # → PROXY_ALLOW_TCP_PORTS=2222
          udp_ports: []

    Ports accept a list or a comma-separated string. Only truthy flags and
    non-empty port lists are emitted.
    """
    import yaml as _yaml

    config_path = (config_dir or CONFIG_DIR) / "egress.yaml"
    if not config_path.exists():
        return {}

    try:
        with config_path.open("r") as f:
            config = _yaml.safe_load(f) or {}
    except (OSError, _yaml.YAMLError) as e:
        logger.warning(f"Could not read egress config {config_path}: {e}; defaulting to deny-all.")
        return {}

    allow = config.get("allow") or {}
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


def _find_free_port() -> int:
    """Ask the OS for a currently-free TCP port on the loopback interface.

    Used to publish each per-project proxy's mitmweb UI on its own host port so
    multiple projects' proxies can run at once (the old fixed 8081 is a singleton).
    There is a small TOCTOU window between closing the socket and the runtime
    binding the port; acceptable for a local dev UI.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
                f"IPV6_ENABLED=true but runtime '{self.container_runtime}' provides no "
                f"IPv6 egress on the upstream network (it NATs IPv4 only); the proxy will "
                f"have no IPv6 route out, so agent IPv6 connections will fail. Set "
                f"IPV6_ENABLED=false unless you are on a runtime/host with working IPv6 egress."
            )
            return

        # (2) Inspect the upstream network config.
        has_v6 = self.runtime.upstream_network_has_ipv6()
        if has_v6 is False:
            logger.warning(
                f"IPV6_ENABLED=true but the upstream network '{self.runtime.upstream_network}' "
                f"is not configured for IPv6; the proxy will likely have no IPv6 route out and "
                f"agent IPv6 connections may fail."
            )
        elif has_v6 is None:
            logger.info(
                f"Could not determine IPv6 config of upstream network "
                f"'{self.runtime.upstream_network}'; proceeding (egress is re-checked on the "
                f"running proxy)."
            )

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
        self.mitmweb_port = _find_free_port()

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
        exports_host_dir.mkdir(parents=True, exist_ok=True)
        exports_container_path = "/home/mitmproxy/exports"
        exports_mounts: list[str] = [
            "--volume",
            f"{exports_host_dir}:{exports_container_path}",
        ]

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
            "--dns",
            "1.1.1.1",
            "-p",
            f"{self.mitmweb_port}:8081",
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
            # this project's .pi-container/egress.yaml.
            *[flag for k, v in read_proxy_forward_env(self.config_dir).items() for flag in ("--env", f"{k}={v}")],
            *config_mounts,
            *exports_mounts,
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

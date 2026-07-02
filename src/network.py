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
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from config import ADMIN_PASSWORD, CONFIG_DIR, PROXY_FORWARD_ENV, REPO_ROOT
from runtimes import ContainerRuntime

logger = logging.getLogger(__name__)


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
        container_runtime: 'str | ContainerRuntime',
        network_name: str,
        proxy_image: str,
        proxy_name: str = "proxy",
        config_dir: Optional[Path] = None,
        llama_ports: Optional[str] = None,
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
        self.llama_ports: Optional[str] = llama_ports
        self.ipv6: bool = ipv6

        # Shared directory for synchronization across different run.py processes
        self.lock_dir: Path = REPO_ROOT / "pi-coding-agent-proxy" / ".locks"
        self.paths: Dict[str, Path] = {
            "lock_dir": self.lock_dir,
            "ref_count_lock": self.lock_dir / ".network_manager.lock",
            "ref_count_file": self.lock_dir / ".network_manager.refcount",
        }

    def _pull_secrets_from_config(self) -> Dict[str, str]:
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

        secrets: Dict[str, str] = {}
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

    def _env_flags(self, secrets: Dict[str, str]) -> List[str]:
        """Convert a secrets dict into ``--env KEY=VALUE`` flag pairs."""
        flags: List[str] = []
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

    def __enter__(self) -> 'ContainerNetworkManager':
        self.start()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[Any]) -> None:
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
            [self.container_runtime, "network", "inspect", self.network_name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.info(f"Creating network {self.network_name} (ipv6={self.ipv6})...")
            subprocess.run(
                [self.container_runtime, *self.runtime.create_isolated_network_argv(self.network_name, ipv6=self.ipv6)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            logger.info(f"Network {self.network_name} already exists, skipping creation.")

        # Start proxy container. It attaches to the upstream network (eth0 →
        # internet) and the isolated network (eth1 → agent). LLAMA_HOST_ADDR
        # tells the proxy where to DNAT llama-server traffic; when unset the
        # proxy falls back to resolving its own default gateway.
        logger.info(f"Starting proxy container {self.proxy_name} from {self.proxy_image}...")
        llama_host_addr = self.runtime.resolve_llama_host_addr(self.proxy_image)

        # Mount the host addon configs over the image's baked defaults. Each is
        # only mounted if present so a missing host config falls back to the
        # (fail-closed) default baked into the image.
        config_mounts: List[str] = []
        for host_name, container_path in (
            ("token_replacer.yaml", "/home/mitmproxy/config/token_replacer.yaml"),
            ("allowlist.yaml", "/home/mitmproxy/config/allowlist.yaml"),
        ):
            host_path = self.config_dir / host_name
            if host_path.exists():
                config_mounts += ["--volume", f"{host_path}:{container_path}:ro"]
            else:
                logger.warning(f"Addon config {host_path} not found; using the image default.")

        cmd = [
            self.container_runtime,
            "run", "-d", "--rm", "--name", self.proxy_name,
            *self.runtime.proxy_network_args(self.network_name),
            *self.runtime.proxy_extra_run_args(),
            "--cap-add", "NET_ADMIN",
            "--dns", "1.1.1.1",
            "-p", "8081:8081",
            # IPv6 policy: --sysctl toggle (VM runtimes) + env flag the proxy
            # entrypoint reads to mirror (or tear down) v6 firewall rules.
            *self.runtime.ipv6_run_args(self.ipv6, forwarding=True),
            "--env", f"IPV6_ENABLED={str(self.ipv6).lower()}",
            "--env", f"ADMIN_PASSWORD={ADMIN_PASSWORD}",
            *(["--env", f"LLAMA_PORTS={self.llama_ports}"] if self.llama_ports else []),
            *(["--env", f"LLAMA_HOST_ADDR={llama_host_addr}"] if llama_host_addr else []),
            # Per-protocol forwarding opt-ins (uninspected protocols).
            *[flag for k, v in PROXY_FORWARD_ENV.items() for flag in ("--env", f"{k}={v}")],
            *config_mounts,
            *self._env_flags(secrets),
            self.proxy_image,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Some runtimes (Docker) attach the isolated network only after run.
        connect_argv = self.runtime.proxy_secondary_connect_argv(self.proxy_name, self.network_name)
        if connect_argv:
            subprocess.run(
                [self.container_runtime, *connect_argv],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        # Wait for proxy to be ready via health probe
        self._wait_for_proxy_health(timeout=30)

    def _actually_stop(self) -> None:
        logger.info(f"Stopping proxy container {self.proxy_name}...")
        subprocess.run([self.container_runtime, "stop", self.proxy_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        delete_argv = self.runtime.delete_isolated_network_argv(self.network_name)
        if delete_argv:
            logger.info(f"Removing network {self.network_name}...")
            subprocess.run([self.container_runtime, *delete_argv], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _wait_for_proxy_health(self, timeout: int = 30) -> None:
        """Wait for the proxy container's mitmweb UI to become healthy.

        Polls the mitmweb HTTP endpoint every 2 seconds until a response is
        received (any status code means the container is alive and responding).

        Raises:
            RuntimeError: If the proxy does not become healthy within timeout.
        """
        url = "http://127.0.0.1:8081"
        elapsed = 0
        while elapsed < timeout:
            try:
                resp = urllib.request.urlopen(url, timeout=2)
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

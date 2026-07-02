import sys

sys.dont_write_bytecode = True

import json
import logging
import re
import signal
import subprocess
from contextlib import ExitStack
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from config import (
    ADMIN_PASSWORD,
    BRIDGE_INTERFACE_ENV,
    CONFIG_DIR,
    IMAGE_TAG,
    IPV6_ENABLED,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from models import Model, ModelConfig, ServerConfig  # noqa: F401  (re-exported for callers/tests)
from network import ContainerNetworkManager, scan_config_env_refs, scan_tmpfs_paths  # noqa: F401
from runtimes import ContainerRuntime
from server import Server
from util import (
    EnvironmentError,
    get_sanitized_git_config_json,
    handle_signal,
    validate_environment,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _warn_if_proxy_lacks_ipv6_egress(runtime_bin: str, upstream_iface: str = "eth0") -> None:
    """Confirm the *running* proxy actually has IPv6 egress on its upstream NIC.

    This is the definitive, observed check (option 5): static capability and
    upstream-network config are checked preflight in ContainerNetworkManager,
    but only the live proxy tells us whether eth0 got a global v6 address AND a
    v6 default route. Warns (does not fail) when either is missing, since IPv4
    still works and the agent's own IPv6 is disabled unless a route was found.
    """
    try:
        addr = subprocess.run(
            [runtime_bin, "exec", "proxy", "ip", "-6", "addr", "show", upstream_iface],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        route = subprocess.run(
            [runtime_bin, "exec", "proxy", "ip", "-6", "route", "show", "default"],
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
            f"IPV6_ENABLED=true but the proxy has no working IPv6 egress on {upstream_iface} "
            f"(global address: {has_global}, default route: {has_default}); this runtime/host "
            f"does not route IPv6 to the internet, so agent IPv6 connections will fail. "
            f"Set IPV6_ENABLED=false to silence this."
        )
    else:
        logger.info(f"Proxy has IPv6 egress on {upstream_iface} (global address + default route present).")


# ─── Startup validation ───────────────────────────────────────────────────

if not ADMIN_PASSWORD or ADMIN_PASSWORD == "CHANGEME":
    logger.error(
        "ERROR: ADMIN_PASSWORD must be set to a non-default value. Update .env with a strong password before running."
    )
    sys.exit(1)

try:
    CONTAINER_RUNTIME = validate_environment(LLAMA_BIN)
except EnvironmentError as e:
    logger.error(f"Environment Error: {e}")
    sys.exit(1)

# Encapsulate all runtime-specific behaviour (network flags, mount syntax,
# host-reachability, etc.) in a ContainerRuntime instance. Explicit env
# overrides win; otherwise the runtime supplies its own defaults.
RUNTIME: ContainerRuntime = ContainerRuntime.create(
    CONTAINER_RUNTIME,
    bridge_interface=BRIDGE_INTERFACE_ENV,
    upstream_network=PROXY_UPSTREAM_NETWORK_ENV,
)
BRIDGE_INTERFACE: str = RUNTIME.bridge_interface
PROXY_UPSTREAM_NETWORK: str = RUNTIME.upstream_network


# ─── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    config_path: Path = REPO_ROOT / "pi-coding-agent" / "home" / ".pi" / "agent" / "models.json"
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    with config_path.open("r") as file:
        data = json.load(file)
        server_configs = []
        for name, val in data["providers"].items():
            if isinstance(val, dict) and "serverCustomParameters" in val:
                server_config = ServerConfig.from_dict(val["serverCustomParameters"])
                server_configs.append({"name": name, "config": server_config, "baseUrl": val.get("baseUrl")})

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        with ExitStack() as stack:
            servers: list[Server] = []
            for item in server_configs:
                base_url = item["baseUrl"]
                container_port = urlparse(base_url).port if base_url else None

                server = Server(
                    config=item["config"],
                    models_dir=MODELS_DIR,
                    llama_bin=LLAMA_BIN,
                    bridge_interface=BRIDGE_INTERFACE,
                    lock_dir=LLAMA_SERVER_LOCK_DIR,
                    repo_root=REPO_ROOT,
                    server_id=item["name"],
                    container_port=container_port,
                    use_host_socat=RUNTIME.needs_host_socat(),
                )
                stack.enter_context(server)
                servers.append(server)

            portconfig = json.dumps([{"cp": server.container_port, "hp": server.port} for server in servers])

            with ContainerNetworkManager(
                RUNTIME,
                "isolated-net",
                "pi-coding-agent-proxy:local",
                config_dir=CONFIG_DIR,
                llama_ports=portconfig,
                ipv6=IPV6_ENABLED,
            ) as _:
                proxy_isolated_ip: str | None = None
                proxy_isolated_ip6: str | None = None
                try:
                    result_ip = subprocess.run(
                        [CONTAINER_RUNTIME, "exec", "proxy", "ip", "addr", "show", RUNTIME.proxy_isolated_interface],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    )
                    match_ip = re.search(r"inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+", result_ip.stdout)
                    if match_ip:
                        proxy_isolated_ip = match_ip.group(1)
                        logger.info(f"Found proxy {RUNTIME.proxy_isolated_interface} IP address: {proxy_isolated_ip}")
                    if IPV6_ENABLED:
                        # Global-scope v6 address only (skip fe80:: link-local).
                        match_ip6 = re.search(r"inet6\s+([0-9a-fA-F:]+)/\d+\s+scope global", result_ip.stdout)
                        if match_ip6:
                            proxy_isolated_ip6 = match_ip6.group(1)
                            logger.info(
                                f"Found proxy {RUNTIME.proxy_isolated_interface} IPv6 address: {proxy_isolated_ip6}"
                            )
                        else:
                            logger.warning(
                                f"IPV6_ENABLED but no global IPv6 address found on proxy "
                                f"{RUNTIME.proxy_isolated_interface}; the agent will have no IPv6 default route."
                            )
                except Exception as e:
                    logger.warning(f"Could not retrieve proxy network info: {e}")

                if not proxy_isolated_ip:
                    raise RuntimeError(
                        f"Could not determine proxy's {RUNTIME.proxy_isolated_interface} IP; "
                        f"the agent cannot be routed through the proxy."
                    )

                if IPV6_ENABLED:
                    _warn_if_proxy_lacks_ipv6_egress(CONTAINER_RUNTIME)

                # Scan tmpfs config for transient paths to mount as tmpfs.
                tmpfs_paths = scan_tmpfs_paths(CONFIG_DIR)

                pi_container_cmd = [
                    CONTAINER_RUNTIME,
                    "run",
                    "--rm",
                    "--interactive",
                    "--tty",
                    *RUNTIME.agent_network_args("isolated-net", proxy_isolated_ip),
                    # IPv6 policy for the agent: --sysctl toggle (VM runtimes) +
                    # env flag its entrypoint reads. When enabled, DEFAULT_ROUTE6
                    # points the v6 default route at the proxy's eth1 v6 address.
                    *RUNTIME.ipv6_run_args(IPV6_ENABLED),
                    "--env",
                    f"IPV6_ENABLED={str(IPV6_ENABLED).lower()}",
                    *(["--env", f"DEFAULT_ROUTE6={proxy_isolated_ip6}"] if proxy_isolated_ip6 else []),
                    *RUNTIME.tmpfs_args("/home/pi/"),
                    "--volume",
                    f"{REPO_ROOT}/pi-coding-agent/home/.pi:/home/pi/.pi",
                    *RUNTIME.tmpfs_args("/home/pi/.pi/agent/bin"),
                    # Transient tmpfs mounts for build artifacts, caches, etc.
                    *[flag for path in tmpfs_paths for flag in ("--tmpfs", path)],
                    "--volume",
                    f"{PROJECT_DIR}:/workspace",
                    "--workdir",
                    "/workspace",
                    "--env",
                    f"LLAMA_PORTS={portconfig}",
                    "--env",
                    f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                    "--memory",
                    "16g",
                    "--cpus",
                    "8",
                    IMAGE_TAG,
                    *sys.argv[1:],
                ]

                result = subprocess.run(pi_container_cmd)

            if result.returncode != 0:
                sys.exit(result.returncode)
    except Exception:
        logger.exception("An error occurred")
        sys.exit(1)
    finally:
        Model.cleanup_download_lock_dir(LLAMA_SERVER_LOCK_DIR / "model_download")


if __name__ == "__main__":
    main()

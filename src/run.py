import sys
sys.dont_write_bytecode = True

import json
import logging
import re
import signal
import subprocess
from urllib.parse import urlparse
from pathlib import Path
from contextlib import ExitStack
from typing import List, Optional

from util import (
    validate_environment,
    handle_signal,
    get_sanitized_git_config_json,
    EnvironmentError,
)
from runtimes import ContainerRuntime
from config import (
    ADMIN_PASSWORD,
    BRIDGE_INTERFACE_ENV,
    CONFIG_DIR,
    IMAGE_TAG,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from models import Model, ModelConfig, ServerConfig  # noqa: F401  (re-exported for callers/tests)
from network import ContainerNetworkManager, scan_config_env_refs  # noqa: F401
from server import Server

logger = logging.getLogger(__name__)

# ─── Startup validation ───────────────────────────────────────────────────

if not ADMIN_PASSWORD or ADMIN_PASSWORD == 'CHANGEME':
    logger.error(
        "ERROR: ADMIN_PASSWORD must be set to a non-default value. "
        "Update .env with a strong password before running."
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

    with config_path.open('r') as file:
        data = json.load(file)
        server_configs = []
        for name, val in data["providers"].items():
            if isinstance(val, dict) and "serverCustomParameters" in val:
                server_config = ServerConfig.from_dict(val["serverCustomParameters"])
                server_configs.append({
                    "name": name,
                    "config": server_config,
                    "baseUrl": val.get("baseUrl")
                })

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        with ExitStack() as stack:
            servers: List[Server] = []
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

            portconfig = json.dumps(
                [{"cp": server.container_port, "hp": server.port} for server in servers]
            )

            with ContainerNetworkManager(
                RUNTIME,
                "isolated-net",
                "pi-coding-agent-proxy:local",
                config_dir=CONFIG_DIR,
                llama_ports=portconfig,
            ) as _:
                proxy_isolated_ip: Optional[str] = None
                try:
                    result_ip = subprocess.run(
                        [CONTAINER_RUNTIME, "exec", "proxy", "ip", "addr", "show", RUNTIME.proxy_isolated_interface],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5
                    )
                    match_ip = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+', result_ip.stdout)
                    if match_ip:
                        proxy_isolated_ip = match_ip.group(1)
                        logger.info(f"Found proxy {RUNTIME.proxy_isolated_interface} IP address: {proxy_isolated_ip}")
                except Exception as e:
                    logger.warning(f"Could not retrieve proxy network info: {e}")

                if not proxy_isolated_ip:
                    raise RuntimeError(
                        f"Could not determine proxy's {RUNTIME.proxy_isolated_interface} IP; "
                        f"the agent cannot be routed through the proxy."
                    )

                pi_container_cmd = [
                    CONTAINER_RUNTIME, "run",
                    "--rm",
                    "--interactive",
                    "--tty",
                    *RUNTIME.agent_network_args("isolated-net", proxy_isolated_ip),
                    *RUNTIME.tmpfs_args("/home/pi/"),
                    "--volume", f"{REPO_ROOT}/pi-coding-agent/home/.pi:/home/pi/.pi",
                    *RUNTIME.tmpfs_args("/home/pi/.pi/agent/bin"),
                    "--volume", f"{PROJECT_DIR}:/workspace",
                    "--workdir", "/workspace",
                    "--env", f"LLAMA_PORTS={portconfig}",
                    "--env", f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                    "--memory", "16g",
                    "--cpus", "8",
                    IMAGE_TAG,
                    *sys.argv[1:]
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

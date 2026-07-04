import sys

sys.dont_write_bytecode = True

import hashlib
import json
import logging
import re
import shutil
import signal
import subprocess
import threading
import uuid
from contextlib import ExitStack
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from config import (
    ADMIN_PASSWORD,
    BRIDGE_INTERFACE_ENV,
    IMAGE_TAG,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from config_schema import validate_config, validate_models
from flow_export import export_mitmweb_flows, poll_agent_container_ips
from models import Model, ServerConfig
from network import (
    ContainerNetworkManager,
    read_agent_extras,
    read_flow_export_enabled,
    read_llama_config,
    read_network_config,
    read_resource_limits,
    resource_limit_args,
    scan_tmpfs_paths,
)
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


# ─── Startup validation (deferred: only run when this is the entrypoint, not when imported by tests) ──


def _init_runtime() -> None:
    """Validate environment and create the runtime instance.

    Called only from ``if __name__ == \"__main__\"`` so that test imports of
    this module do not trigger subprocess calls or environment checks.
    """
    if not ADMIN_PASSWORD or ADMIN_PASSWORD == "CHANGEME":
        logger.error(
            "ERROR: ADMIN_PASSWORD must be set to a non-default value. Update .env with a strong password before running."
        )
        sys.exit(1)

    try:
        _CONTAINER_RUNTIME = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)

    global CONTAINER_RUNTIME, RUNTIME, BRIDGE_INTERFACE, PROXY_UPSTREAM_NETWORK
    RUNTIME = ContainerRuntime.create(
        _CONTAINER_RUNTIME,
        bridge_interface=BRIDGE_INTERFACE_ENV,
        upstream_network=PROXY_UPSTREAM_NETWORK_ENV,
    )
    CONTAINER_RUNTIME = _CONTAINER_RUNTIME
    BRIDGE_INTERFACE = RUNTIME.bridge_interface
    PROXY_UPSTREAM_NETWORK = RUNTIME.upstream_network


# ─── Per-project configuration ───────────────────────────────────────────────


# Project-level config seeded into ``{PROJECT_DIR}/.pi-container``. Each is
# per-project: ``config.yaml`` holds orchestration settings (resource limits,
# tmpfs paths, flow-export toggle, egress policy); the proxy mounts its own
# allowlist/token_replacer; and llama-server loads chat templates from the
# workspace's own copy (models.json flags reference ``.pi-container/chat-templates/...``
# relative to the launch dir). config.yaml's tmpfs list ships empty on purpose —
# seeding the repo's own paths would create those dirs in every foreign workspace.
_PROJECT_CONFIG_DIRS = ("agent", "chat-templates")
_PROJECT_CONFIG_FILES = ("config.yaml", "allowlist.yaml", "token_replacer.yaml")


def _project_scope(project_dir: Path) -> tuple[str, str]:
    """Return ``(proxy_name, network_name)`` unique to this workspace.

    Keyed by a hash of the absolute project path so each workspace gets its own
    isolated network + proxy container, while repeated (or concurrent) runs from
    the same workspace resolve to the same pair and share it via refcount.
    """
    key = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return f"pi-proxy-{key}", f"pi-isolated-net-{key}"


def _ensure_project_config() -> Path:
    """Seed the per-project ``.pi-container`` config from the repo template if absent.

    Seeds ``{PROJECT_DIR}/.pi-container`` from ``{REPO_ROOT}/pi-coding-agent/default``:
    the ``agent/`` and ``chat-templates/`` subtrees plus ``config.yaml`` and the proxy
    addon configs ``allowlist.yaml``/``token_replacer.yaml``. It also seeds
    ``entrypoint.sh`` into ``.pi-container/agent/`` so the container's entrypoint can
    invoke a user-customizable script before ``pi`` starts. Each item is only seeded
    when missing, so existing (user-edited) files are never overwritten and a
    partially-populated ``.pi-container`` is completed.

    Returns the agent launch-config dir (``{PROJECT_DIR}/.pi-container/agent``).
    """
    template_root = REPO_ROOT / "pi-coding-agent" / "default"
    if not template_root.is_dir():
        raise FileNotFoundError(f"Project config template not found: {template_root}")

    project_root = PROJECT_DIR / ".pi-container"

    for name in _PROJECT_CONFIG_DIRS:
        src, dst = template_root / name, project_root / name
        if src.is_dir() and not dst.exists():
            logger.info(f"Seeding {dst} from {src}.")
            shutil.copytree(src, dst)

    for name in _PROJECT_CONFIG_FILES:
        src, dst = template_root / name, project_root / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Seeding {dst} from {src}.")
            shutil.copy2(src, dst)

    # Seed entrypoint.sh into {PROJECT_DIR}/.pi-container/agent/ so it gets
    # bind-mounted to /home/pi/.pi/agent/ inside the container. The container's
    # own entrypoint.sh (built into the image) calls /home/pi/.pi/agent/entrypoint.sh
    # before launching pi; seeding the template gives users a working copy to
    # customize without editing the repo or losing changes across rebuilds.
    ep_src = template_root / "entrypoint.sh"
    ep_dst = project_root / "agent" / "entrypoint.sh"
    if ep_src.exists() and not ep_dst.exists():
        logger.info(f"Seeding {ep_dst} from {ep_src}.")
        shutil.copy2(ep_src, ep_dst)

    return project_root / "agent"


# ─── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    agent_config_dir = _ensure_project_config()
    config_path: Path = agent_config_dir / "models.json"
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    # Validate per-project config schema and version compatibility.
    pi_container_dir = PROJECT_DIR / ".pi-container"
    config_yaml_path = pi_container_dir / "config.yaml"
    is_valid, errors, _ = validate_config(config_yaml_path)
    if not is_valid:
        logger.error("Configuration incompatible with this version of pi-container:")
        for error in errors:
            logger.error(error)
        logger.error(
            "\nFix: delete .pi-container in this workspace and re-run to re-seed, "
            "or update schema_version in .pi-container/config.yaml to match the "
            "current pi-container version (see latest git tag)."
        )
        sys.exit(1)

    # Validate models.json schema.
    models_path = pi_container_dir / "agent" / "models.json"
    models_valid, models_errors = validate_models(models_path)
    if not models_valid:
        logger.error("Models configuration invalid:")
        for error in models_errors:
            logger.error(error)
        logger.error("\nFix: update .pi-container/agent/models.json to match the expected schema.")
        sys.exit(1)
    flow_export_enabled = read_flow_export_enabled(pi_container_dir)
    ipv6_enabled = read_network_config(pi_container_dir)["ipv6"]
    llama_cfg = read_llama_config(pi_container_dir)
    agent_extras = read_agent_extras(pi_container_dir)

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
                    startup_timeout=llama_cfg["startup_timeout"],
                    startup_attempts=llama_cfg["startup_attempts"],
                )
                stack.enter_context(server)
                servers.append(server)

            portconfig = json.dumps([{"cp": server.container_port, "hp": server.port} for server in servers])

            # Unique name for this run's agent container. The proxy's flow_export
            # addon partitions captured traffic by client IP; naming the agent
            # container lets run.py look up its isolated-net IP (below) to find
            # the matching flows-<ip>.jsonl file.
            run_id = uuid.uuid4().hex[:12]
            agent_container_name = f"pi-coding-agent-{run_id}"

            # Per-project isolation: each workspace gets its own isolated network
            # and proxy container (auto-assigned mitmweb port, project-sourced
            # allowlist/token_replacer). Concurrent runs in the same workspace
            # resolve to the same names and share the proxy via refcount.
            proxy_name, network_name = _project_scope(PROJECT_DIR)

            with ContainerNetworkManager(
                RUNTIME,
                network_name,
                "pi-coding-agent-proxy:local",
                proxy_name=proxy_name,
                config_dir=pi_container_dir,
                llama_ports=portconfig,
                ipv6=ipv6_enabled,
            ) as netmgr:
                mitmweb_url = netmgr.mitmweb_url()
                if mitmweb_url:
                    logger.info(f"mitmweb UI for this project: {mitmweb_url}")
                proxy_isolated_ip: str | None = None
                proxy_isolated_ip6: str | None = None
                try:
                    result_ip = subprocess.run(
                        [CONTAINER_RUNTIME, "exec", proxy_name, "ip", "addr", "show", RUNTIME.proxy_isolated_interface],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    )
                    match_ip = re.search(r"inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+", result_ip.stdout)
                    if match_ip:
                        proxy_isolated_ip = match_ip.group(1)
                        logger.info(f"Found proxy {RUNTIME.proxy_isolated_interface} IP address: {proxy_isolated_ip}")
                    if ipv6_enabled:
                        # Global-scope v6 address only (skip fe80:: link-local).
                        match_ip6 = re.search(r"inet6\s+([0-9a-fA-F:]+)/\d+\s+scope global", result_ip.stdout)
                        if match_ip6:
                            proxy_isolated_ip6 = match_ip6.group(1)
                            logger.info(
                                f"Found proxy {RUNTIME.proxy_isolated_interface} IPv6 address: {proxy_isolated_ip6}"
                            )
                        else:
                            logger.warning(
                                f"network.ipv6 is enabled but no global IPv6 address found on proxy "
                                f"{RUNTIME.proxy_isolated_interface}; the agent will have no IPv6 default route."
                            )
                except Exception as e:
                    logger.warning(f"Could not retrieve proxy network info: {e}")

                if not proxy_isolated_ip:
                    raise RuntimeError(
                        f"Could not determine proxy's {RUNTIME.proxy_isolated_interface} IP; "
                        f"the agent cannot be routed through the proxy."
                    )

                if ipv6_enabled:
                    netmgr.warn_if_proxy_lacks_ipv6_egress()

                # Transient tmpfs paths (config.yaml tmpfs.paths) — all under
                # /workspace (the mounted PROJECT_DIR), so the config that declares
                # them is per-project. Reading the repo's list for a foreign
                # workspace would mkdir this repo's mountpoints (e.g.
                # pi-coding-agent-proxy/*) inside that workspace.
                tmpfs_paths = scan_tmpfs_paths(pi_container_dir)

                pi_container_cmd = [
                    CONTAINER_RUNTIME,
                    "run",
                    "--rm",
                    "--name",
                    agent_container_name,
                    "--interactive",
                    "--tty",
                    *RUNTIME.agent_network_args(network_name, proxy_isolated_ip),
                    # IPv6 policy for the agent: --sysctl toggle (VM runtimes) +
                    # env flag its entrypoint reads. When enabled, DEFAULT_ROUTE6
                    # points the v6 default route at the proxy's eth1 v6 address.
                    *RUNTIME.ipv6_run_args(ipv6_enabled),
                    "--env",
                    f"IPV6_ENABLED={str(ipv6_enabled).lower()}",
                    *(["--env", f"DEFAULT_ROUTE6={proxy_isolated_ip6}"] if proxy_isolated_ip6 else []),
                    *RUNTIME.tmpfs_args("/home/pi/"),
                    "--volume",
                    f"{agent_config_dir}:/home/pi/.pi/agent",
                    "--volume",
                    f"{PROJECT_DIR}:/workspace",
                    *RUNTIME.tmpfs_args("/home/pi/.pi/agent/bin"),
                    *RUNTIME.tmpfs_args("/workspace/.pi-container/exports"),
                    "--workdir",
                    "/workspace",
                    # Transient tmpfs mounts for build artifacts, caches, etc.
                    *[flag for path in tmpfs_paths for flag in RUNTIME.tmpfs_args(path)],
                    "--env",
                    f"LLAMA_PORTS={portconfig}",
                    "--env",
                    f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                    # Extra agent env vars + bind mounts (config.yaml agent.env/agent.mounts).
                    *[flag for k, v in agent_extras["env"].items() for flag in ("--env", f"{k}={v}")],
                    *[flag for m in agent_extras["mounts"] for flag in ("--volume", m)],
                    # Resource limits for this project's agent (config.yaml resources.agent).
                    *resource_limit_args(read_resource_limits(pi_container_dir, "agent")),
                    IMAGE_TAG,
                    *sys.argv[1:],
                ]

                # Discover the agent's isolated-net IPs (IPv4 and/or IPv6) in the
                # background so we can attribute its captured flows (partitioned by
                # client IP in the proxy) after it exits. A daemon thread keeps the
                # interactive TTY handoff to subprocess.run untouched; the exec
                # probes it runs use their own pipes, not the agent's terminal.
                ip_holder: dict[str, list[str]] = {}
                ip_stop = threading.Event()
                ip_thread: threading.Thread | None = None
                if flow_export_enabled:

                    def _discover_agent_ips() -> None:
                        ips = poll_agent_container_ips(CONTAINER_RUNTIME, agent_container_name, ip_stop)
                        if ips:
                            ip_holder["ips"] = ips

                    ip_thread = threading.Thread(target=_discover_agent_ips, daemon=True)
                    ip_thread.start()

                result = subprocess.run(pi_container_cmd)

                # Export mitmweb flow history for this session. The flow_export
                # addon appends per-client-IP files as flows complete; run.py reads
                # this agent's file(s) here (after it exits, before the
                # ContainerNetworkManager context exits and stops the proxy).
                if flow_export_enabled:
                    ip_stop.set()
                    if ip_thread is not None:
                        ip_thread.join(timeout=2)
                    export_mitmweb_flows(
                        sessions_dir=agent_config_dir / "sessions",
                        client_ips=ip_holder.get("ips"),
                        exports_dir=pi_container_dir / "exports",
                    )

            if result.returncode != 0:
                sys.exit(result.returncode)
    except Exception:
        logger.exception("An error occurred")
        sys.exit(1)
    finally:
        Model.cleanup_download_lock_dir(LLAMA_SERVER_LOCK_DIR / "model_download")


if __name__ == "__main__":
    _init_runtime()
    main()

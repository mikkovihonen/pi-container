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
    IPV6_ENABLED,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from flow_export import export_mitmweb_flows, poll_agent_container_ips
from models import Model, ServerConfig
from network import (
    ContainerNetworkManager,
    read_flow_export_enabled,
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


# Project-level config files seeded into ``{PROJECT_DIR}/.pi-container`` alongside
# the ``agent/`` subtree. Each is per-project: the proxy for this workspace mounts
# its own allowlist/token_replacer, and the agent container reads its own tmpfs
# list. The tmpfs template ships empty on purpose — seeding the repo's own list
# (which references pi-container-internal paths) would create those dirs in every
# foreign workspace.
_PROJECT_CONFIG_FILES = ("allowlist.yaml", "token_replacer.yaml", "tmpfs.yaml", "flow_export.yaml", "egress.yaml")


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
    the whole ``agent/`` subtree (models.json, settings.json, sessions, …) plus the
    project-level ``allowlist.yaml``/``token_replacer.yaml``/``tmpfs.yaml``. Each
    item is only seeded when missing, so existing (user-edited) files are never
    overwritten and a partially-populated ``.pi-container`` is completed.

    Returns the agent launch-config dir (``{PROJECT_DIR}/.pi-container/agent``).
    """
    template_root = REPO_ROOT / "pi-coding-agent" / "default"
    if not template_root.is_dir():
        raise FileNotFoundError(f"Project config template not found: {template_root}")

    project_root = PROJECT_DIR / ".pi-container"
    agent_config_dir = project_root / "agent"

    if not agent_config_dir.exists():
        logger.info(f"No agent config at {agent_config_dir}; seeding from {template_root / 'agent'}.")
        shutil.copytree(template_root / "agent", agent_config_dir)

    for name in _PROJECT_CONFIG_FILES:
        src, dst = template_root / name, project_root / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Seeding {dst} from {src}.")
            shutil.copy2(src, dst)

    return agent_config_dir


# ─── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    agent_config_dir = _ensure_project_config()
    config_path: Path = agent_config_dir / "models.json"
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    # Per-project mitmweb flow-export toggle (.pi-container/flow_export.yaml).
    flow_export_enabled = read_flow_export_enabled(PROJECT_DIR / ".pi-container")

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
                config_dir=PROJECT_DIR / ".pi-container",
                llama_ports=portconfig,
                ipv6=IPV6_ENABLED,
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
                    netmgr.warn_if_proxy_lacks_ipv6_egress()

                # Scan tmpfs config for transient paths to mount as tmpfs. These
                # paths are all under /workspace (the mounted PROJECT_DIR), so the
                # config that declares them is per-project — read it from the
                # project's own .pi-container, not the repo's. Reading the repo's
                # list here would mkdir this repo's transient mountpoints (e.g.
                # pi-coding-agent-proxy/*) inside every foreign workspace.
                tmpfs_paths = scan_tmpfs_paths(PROJECT_DIR / ".pi-container")

                # The project's apt dependency manifest, bind-mounted read-only
                # back over the tmpfs that hides the rest of .pi-container (see the
                # mounts below).
                deps_dir = PROJECT_DIR / ".pi-container" / "dependencies"

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
                    *RUNTIME.ipv6_run_args(IPV6_ENABLED),
                    "--env",
                    f"IPV6_ENABLED={str(IPV6_ENABLED).lower()}",
                    *(["--env", f"DEFAULT_ROUTE6={proxy_isolated_ip6}"] if proxy_isolated_ip6 else []),
                    *RUNTIME.tmpfs_args("/home/pi/"),
                    "--volume",
                    f"{agent_config_dir}:/home/pi/.pi/agent",
                    "--volume",
                    f"{PROJECT_DIR}:/workspace",
                    *RUNTIME.tmpfs_args("/home/pi/.pi/agent/bin"),
                    # Hide the whole .pi-container from the agent — its YAML configs
                    # (allowlist/token_replacer/tmpfs), flow-export captures and
                    # agent secrets — by shadowing it with an empty tmpfs.
                    *RUNTIME.tmpfs_args("/workspace/.pi-container"),
                    # ...then bind the dependency manifest back on top (read-only)
                    # so the entrypoint can still install the project's apt packages.
                    # Only mounted when present: podman refuses to auto-create a
                    # missing bind source and the container would fail to start.
                    *(
                        ["--volume", f"{deps_dir}:/workspace/.pi-container/dependencies:ro"]
                        if deps_dir.exists()
                        else []
                    ),
                    "--workdir",
                    "/workspace",
                    # Transient tmpfs mounts for build artifacts, caches, etc.
                    *[flag for path in tmpfs_paths for flag in RUNTIME.tmpfs_args(path)],
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
                        exports_dir=PROJECT_DIR / ".pi-container" / "exports",
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

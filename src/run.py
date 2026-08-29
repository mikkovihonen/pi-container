import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from contextlib import ExitStack
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

import containers
import images
import project
import volumes
from build import build_project_image
from config import (
    ADMIN_PASSWORD,
    BRIDGE_INTERFACE_ENV,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from config_schema import validate_config, validate_models, validate_project_yaml
from flow_export import export_mitmweb_flows, poll_agent_container_ips, recover_dangling_flows
from models import Model
from network import (
    ContainerNetworkManager,
    read_agent_extras,
    read_flow_export_enabled,
    read_llama_config,
    read_nested_containers_config,
    read_network_config,
    read_resource_limits,
    resource_limit_args,
    scan_tmpfs_paths,
    scan_volume_paths,
)
from runtimes import ContainerRuntime
from server import Server
from util import (
    EnvironmentError,
    extract_ipv4_from_ip_addr,
    get_sanitized_git_config_json,
    handle_signal,
    run_quiet,
    validate_environment,
    warn_missing_gitignore,
)

logger = logging.getLogger(__name__)


# ─── Startup validation ───────────────────────────────────────────────────────


def _init_runtime() -> None:
    """Validate environment and create the runtime instance.

    Called only from ``if __name__ == "__main__"`` so that test imports of
    this module do not trigger subprocess calls or environment checks.
    """
    if not ADMIN_PASSWORD or ADMIN_PASSWORD == "CHANGEME":
        logger.error(
            "ERROR: ADMIN_PASSWORD must be set to a non-default value. Update .env with a strong password before running."
        )
        sys.exit(1)
        return

    try:
        _CONTAINER_RUNTIME = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)
        return

    global CONTAINER_RUNTIME, RUNTIME, BRIDGE_INTERFACE, PROXY_UPSTREAM_NETWORK
    RUNTIME = ContainerRuntime.create(
        _CONTAINER_RUNTIME,
        bridge_interface=BRIDGE_INTERFACE_ENV,
        upstream_network=PROXY_UPSTREAM_NETWORK_ENV,
    )
    CONTAINER_RUNTIME = _CONTAINER_RUNTIME
    BRIDGE_INTERFACE = RUNTIME.bridge_interface
    PROXY_UPSTREAM_NETWORK = RUNTIME.upstream_network


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    agent_config_dir = project.ensure_project_config()
    warn_missing_gitignore(PROJECT_DIR, logger)
    config_path: Path = agent_config_dir / "models.json"
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    # Validate per-project config schema and version compatibility.
    pi_container_dir = PROJECT_DIR / ".pi-container"
    config_yaml_path = pi_container_dir / "config.yaml"
    is_valid, errors, schema_version = validate_config(config_yaml_path)
    if not is_valid:
        logger.error("Configuration incompatible with this version of pi-container:")
        for error in errors:
            logger.error(error)
        for line in project.config_fix_hint(errors, config_yaml_path):
            logger.error(line)
        sys.exit(1)

    yaml_errors = validate_project_yaml(pi_container_dir)
    if yaml_errors:
        logger.error("Configuration has duplicate YAML keys:")
        for error in yaml_errors:
            logger.error(error)
        logger.error(
            "\nYAML keeps only the last occurrence of a repeated key, so the earlier "
            "value was silently discarded. Delete the duplicate line and re-run."
        )
        sys.exit(1)

    # Validate models.json schema.
    models_path = pi_container_dir / "agent" / "models.json"
    models_valid, models_errors = validate_models(models_path)
    if not models_valid:
        logger.error("Configuration error in models.json:")
        for err in models_errors:
            logger.error(err)
        logger.error("\nFix: update .pi-container/agent/models.json to match the expected schema.")
        sys.exit(1)
    flow_export_enabled = read_flow_export_enabled(pi_container_dir)
    ipv6_enabled = read_network_config(pi_container_dir)["ipv6"]
    llama_cfg = read_llama_config(pi_container_dir)
    agent_extras = read_agent_extras(pi_container_dir)

    # Crash recovery: reap orphaned servers and agent containers from prior crashed runs
    containers.sweep_orphaned_servers(LLAMA_SERVER_LOCK_DIR)
    containers.cleanup_orphaned_agent_containers(CONTAINER_RUNTIME, project.project_key(PROJECT_DIR))
    if flow_export_enabled:
        recover_dangling_flows(
            exports_dir=pi_container_dir / "exports",
            sessions_dir=agent_config_dir / "sessions",
        )

    nested_cfg = read_nested_containers_config(pi_container_dir)
    nested_ports = nested_cfg["ports"]
    if nested_cfg["enabled"]:
        logger.info(
            f"Nested containers enabled (storage={nested_cfg['storage']}, security={nested_cfg['security']}): "
            f"the agent can run its own rootless podman. Its traffic still egresses through the proxy, "
            f"but the agent container's SELinux confinement is relaxed."
        )
        containers.warn_about_registry_allowlist(pi_container_dir)
        if nested_ports["publish"]:
            taken = containers.unavailable_host_ports(nested_ports["publish"], nested_ports["expose"])
            if taken:
                logger.error(f"Host port(s) already in use: {', '.join(str(p) for p in taken)}")
                holders = containers.port_holders(CONTAINER_RUNTIME, taken, project.project_key(PROJECT_DIR))
                for port in taken:
                    holder = holders.get(port)
                    logger.error(f"  {port} → {holder}" if holder else f"  {port} → not held by a container")
                logger.error(
                    f"Free them, or change nested_containers.ports.publish in "
                    f"{config_yaml_path} (an entry may be written 'HOSTPORT:AGENTPORT' to remap)."
                )
                sys.exit(1)
            bind = "127.0.0.1" if nested_ports["expose"] != "lan" else "all interfaces"
            logger.info(
                "Publishing nested-container ports on "
                + f"{bind}: "
                + ", ".join(f"{host}→agent {agent}" for host, agent in nested_ports["publish"])
                + ". A nested container must publish the agent-side port itself "
                + "(e.g. `podman run -p 3000:3000`) for the host port to serve anything."
            )
    elif nested_ports["publish"]:
        logger.warning(
            f"nested_containers.ports.publish lists port(s) "
            f"{', '.join(str(host) for host, _ in nested_ports['publish'])} but "
            f"nested_containers.enabled is false; nothing is published."
        )

    with config_path.open("r") as file:
        data = json.load(file)
        server_configs, llama_hostnames = containers.extract_server_configs(data)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, handle_signal)
    if hasattr(signal, "SIGQUIT"):
        signal.signal(signal.SIGQUIT, handle_signal)

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
                    lock_dir=LLAMA_SERVER_LOCK_DIR,
                    repo_root=REPO_ROOT,
                    server_id=item["name"],
                    container_port=container_port,
                    startup_timeout=llama_cfg["startup_timeout"],
                    startup_attempts=llama_cfg["startup_attempts"],
                )
                stack.enter_context(server)
                servers.append(server)

            portconfig = json.dumps([{"cp": server.container_port, "hp": server.port} for server in servers])

            run_id = uuid.uuid4().hex[:12]
            agent_container_name = f"pi-coding-agent-{run_id}"
            proxy_name, network_name = project.project_scope(PROJECT_DIR)

            with ContainerNetworkManager(
                RUNTIME,
                network_name,
                "pi-coding-agent-proxy:local",
                proxy_name=proxy_name,
                config_dir=pi_container_dir,
                llama_ports=portconfig,
                llama_hostnames=" ".join(sorted(llama_hostnames)),
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
                    proxy_isolated_ip = extract_ipv4_from_ip_addr(result_ip.stdout)
                    if proxy_isolated_ip:
                        logger.info(f"Found proxy {RUNTIME.proxy_isolated_interface} IP address: {proxy_isolated_ip}")
                    if ipv6_enabled:
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

                # ─── Resolve agent image (shared vs. project-specific) ──────
                agent_image_tag, is_project_specific = images.resolve_agent_image(PROJECT_DIR)
                if is_project_specific:
                    label_hash = images.compute_image_hash(PROJECT_DIR)
                    project_hash, _ = project.project_scope(PROJECT_DIR)
                    project_path = str(PROJECT_DIR.resolve())

                    orphaned = images.cleanup_orphaned_project_images(CONTAINER_RUNTIME)
                    if orphaned:
                        logger.info(f"Removed {len(orphaned)} orphaned project image(s): {', '.join(orphaned)}")

                    removed = images.cleanup_stale_project_images(
                        CONTAINER_RUNTIME,
                        project_hash,
                        label_hash,
                    )
                    if removed:
                        logger.info(f"Removed {len(removed)} stale project image(s): {', '.join(removed)}")

                    newest_shared = images.newest_shared_image_time()
                    if newest_shared is None:
                        sys.exit(1)
                    shared_tag, shared_ts = newest_shared

                    reason = images.project_image_build_reason(
                        PROJECT_DIR, agent_image_tag, label_hash, shared_ts, shared_tag
                    )
                    if reason is not None:
                        logger.info(f"Building project-specific agent image: {agent_image_tag} ({reason})")
                        root_commands_path = str(
                            PROJECT_DIR / ".pi-container" / "dependencies" / "root" / "commands.sh"
                        )
                        build_project_image(
                            CONTAINER_RUNTIME,
                            root_commands_path,
                            agent_image_tag,
                            label_hash,
                            project_hash=project_hash,
                            project_path=project_path,
                            build_timestamp=images.now_iso(),
                        )
                    else:
                        logger.info(f"Using cached project-specific image: {agent_image_tag}")
                else:
                    logger.info(f"Using shared image: {agent_image_tag}")

                # ─── Nested-container image store ───────────────────────────
                nested_args: list[str] = []
                if nested_cfg["enabled"]:
                    project_key = project.project_key(PROJECT_DIR)
                    if nested_cfg["storage"] == "volume":
                        orphaned_volumes = volumes.cleanup_orphaned_nested_volumes(CONTAINER_RUNTIME)
                        if orphaned_volumes:
                            logger.info(
                                f"Removed {len(orphaned_volumes)} orphaned nested-storage volume(s): "
                                f"{', '.join(orphaned_volumes)}"
                            )
                        if not volumes.ensure_nested_volume(
                            CONTAINER_RUNTIME,
                            RUNTIME.nested_volume_name(project_key),
                            project_hash=proxy_name,
                            project_path=str(PROJECT_DIR.resolve()),
                        ):
                            logger.warning("Falling back to a tmpfs nested image store for this run.")
                            nested_cfg = {**nested_cfg, "storage": "tmpfs"}
                    nested_args = RUNTIME.nested_container_args(nested_cfg, project_key)

                # Transient tmpfs paths (config.yaml tmpfs.paths)
                tmpfs_paths = scan_tmpfs_paths(pi_container_dir)

                # Persistent named shadow volumes (config.yaml volumes.paths)
                volume_paths = scan_volume_paths(pi_container_dir)
                active_volume_map = {
                    path: volumes.project_volume_name(project.project_key(PROJECT_DIR), path) for path in volume_paths
                }
                orphaned_project_vols = volumes.cleanup_orphaned_project_volumes(CONTAINER_RUNTIME)
                if orphaned_project_vols:
                    logger.info(
                        f"Removed {len(orphaned_project_vols)} orphaned project volume(s): "
                        f"{', '.join(orphaned_project_vols)}"
                    )
                stale_project_vols = volumes.cleanup_stale_project_volumes(
                    CONTAINER_RUNTIME,
                    project_hash=proxy_name,
                    active_volume_names=set(active_volume_map.values()),
                )
                if stale_project_vols:
                    logger.info(
                        f"Removed {len(stale_project_vols)} stale project volume(s): {', '.join(stale_project_vols)}"
                    )
                project_path_str = str(PROJECT_DIR.resolve())
                for dest_path, vol_name in active_volume_map.items():
                    volumes.ensure_project_volume(
                        CONTAINER_RUNTIME,
                        vol_name,
                        dest_path=dest_path,
                        project_hash=proxy_name,
                        project_path=project_path_str,
                    )
                volume_args = [
                    arg
                    for dest_path, vol_name in active_volume_map.items()
                    for arg in ("--volume", f"{vol_name}:{dest_path}")
                ]

                pi_container_cmd = [
                    CONTAINER_RUNTIME,
                    "run",
                    "--rm",
                    "--name",
                    agent_container_name,
                    "--label",
                    "pi-container.type=agent",
                    "--label",
                    f"pi-container.project.hash={project.project_key(PROJECT_DIR)}",
                    "--label",
                    f"pi-container.launcher_pid={os.getpid()}",
                    "--label",
                    f"pi-container.run_id={run_id}",
                    "--interactive",
                    "--tty",
                    *RUNTIME.agent_network_args(network_name, proxy_isolated_ip),
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
                    *volume_args,
                    *nested_args,
                    *RUNTIME.nested_port_args(nested_cfg),
                    "--workdir",
                    "/workspace",
                    *[flag for path in tmpfs_paths for flag in RUNTIME.tmpfs_args(path)],
                    "--env",
                    f"CONTAINER_CHOWN_PATHS={':'.join(sorted(set(tmpfs_paths + list(active_volume_map.keys()) + ['/home/pi/.pi/agent/bin', '/workspace/.pi-container/exports'])))}",
                    "--env",
                    f"LLAMA_PORTS={portconfig}",
                    "--env",
                    f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                    *(["--env", f"PI_CONTAINER_VERSION={schema_version}"] if schema_version else []),
                    *[flag for k, v in agent_extras["env"].items() for flag in ("--env", f"{k}={v}")],
                    *[flag for m in agent_extras["mounts"] for flag in ("--volume", m)],
                    *[flag for c in agent_extras["capabilities"] for flag in ("--cap-add", c)],
                    *[flag for d in agent_extras["devices"] for flag in ("--device", d)],
                    *resource_limit_args(read_resource_limits(pi_container_dir, "agent")),
                    agent_image_tag,
                    *sys.argv[1:],
                ]

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

                try:
                    result = subprocess.run(pi_container_cmd)
                finally:
                    run_quiet(
                        [CONTAINER_RUNTIME, "rm", "-f", agent_container_name],
                        check=False,
                        label=f"cleanup agent container {agent_container_name}",
                        logger=logger,
                    )

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

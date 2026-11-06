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
from build import DEFAULT_ROOT_COMMANDS, build_agent, build_project_image, build_proxy
from config import (
    ADMIN_PASSWORD,
    BRIDGE_INTERFACE_ENV,
    LLAMA_BIN,
    LLAMA_SERVER_LOCK_DIR,
    LOGS_DIR,
    MODELS_DIR,
    PROJECT_DIR,
    PROXY_IMAGE_TAG,
    PROXY_LOCK_DIR,
    PROXY_UPSTREAM_NETWORK_ENV,
    REPO_ROOT,
)
from config_schema import validate_config, validate_models, validate_project_yaml
from flow_export import export_mitmweb_flows, poll_agent_container_ips, recover_dangling_flows
from models import Model
from network import (
    ContainerNetworkManager,
    read_agent_extras,
    read_agent_mode,
    read_agent_ports,
    read_flow_export_enabled,
    read_git_config,
    read_llama_config,
    read_nested_containers_config,
    read_network_config,
    read_read_only_pi_container,
    read_resource_limits,
    resource_limit_args,
    scan_tmpfs_paths,
    scan_volume_paths,
)
from runtimes import ContainerRuntime
from security import read_security_config, validate_mount_paths
from server import Server
from source_control import read_source_control_config, resolve_source_control_tokens
from util import (
    EnvironmentError,
    extract_ipv4_from_ip_addr,
    get_host_external_ip,
    get_sanitized_git_config_json,
    handle_signal,
    run_quiet,
    validate_environment,
    warn_missing_gitignore,
)

logger = logging.getLogger(__name__)

CONTAINER_RUNTIME: str = "podman"


# ─── Startup validation ───────────────────────────────────────────────────────


def _init_runtime() -> None:
    """Validate environment and create the runtime instance.

    Called only from ``if __name__ == "__main__"`` so that test imports of
    this module do not trigger subprocess calls or environment checks.
    """
    if not ADMIN_PASSWORD or ADMIN_PASSWORD == "CHANGEME":
        logger.error(
            "ERROR: ADMIN_PASSWORD must be set to a non-default value in the OS keyring "
            "(uv run keyring set pi-container ADMIN_PASSWORD). "
            "Host environment variables are not accepted."
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


def process_gateway_configs(gateway_configs: list[dict], pi_container_dir: Path) -> None:
    """Preflight, authenticate, and validate allowlist egress for external model gateways."""
    for gw in gateway_configs:
        gw_def = gw["gatewayDef"]
        if not gw_def.auth:
            continue
        if gw_def.auth.type == "openid":
            from auth import ensure_gateway_auth

            token = ensure_gateway_auth(gw["name"], gw_def.auth)
            var_name = gw_def.auth.env_var or f"{gw['name'].upper().replace('-', '_')}_TOKEN"
            os.environ[var_name] = token
        elif gw_def.auth.type == "api-key":
            from secret_store import DEFAULT_KEYRING_SERVICE, resolve_secret

            var_name = gw_def.auth.env_var or f"{gw['name'].upper().replace('-', '_')}_API_KEY"
            service = gw_def.auth.keyring_service or DEFAULT_KEYRING_SERVICE
            account = gw_def.auth.keyring_account or var_name

            token = resolve_secret(var_name, service=service, account=account)
            if not token:
                logger.error(
                    f"Gateway '{gw['name']}' requires secret '{var_name}'.\n"
                    f'Set via environment:  export {var_name}="<your-api-key>"\n'
                    f"Or save in OS keyring: uv run keyring set {service} {account}"
                )
                sys.exit(1)
                return

            os.environ[var_name] = token

    containers.warn_about_gateway_allowlist(pi_container_dir, gateway_configs)


def ensure_proxy_image(runtime: str, repo_root: Path = REPO_ROOT) -> bool:
    """Ensure the proxy container image is built and current with its definition files.

    Returns True if the image was rebuilt, False if it was already current.
    """
    if not images.proxy_image_is_current(repo_root=repo_root, runtime=runtime):
        logger.info("Proxy image definition changed or missing; rebuilding proxy image...")
        build_proxy(runtime)
        return True
    return False


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    agent_config_dir = project.ensure_project_config()
    warn_missing_gitignore(PROJECT_DIR, logger)
    pi_container_dir = PROJECT_DIR / ".pi-container"
    models_path: Path = agent_config_dir / "models.json"
    if not models_path.exists():
        logger.error(f"Config file not found: {models_path}")
        sys.exit(1)

    # Validate per-project config schema and version compatibility.
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
    read_only_pi_container = read_read_only_pi_container(pi_container_dir)
    security_cfg = read_security_config(pi_container_dir)
    git_cfg = read_git_config(pi_container_dir)
    sc_cfg = read_source_control_config(pi_container_dir)
    sc_tokens = resolve_source_control_tokens(pi_container_dir, log=logger)

    mount_errors = validate_mount_paths(agent_extras["mounts"], security_cfg["blocked_mount_paths"])
    if mount_errors:
        logger.error("Security policy violation in agent.mounts:")
        for err in mount_errors:
            logger.error(f"  ✗ {err}")
        logger.error("\nFix: remove the forbidden host mount paths from config.yaml under agent.mounts.")
        sys.exit(1)

    # Crash recovery: reap orphaned servers, proxies, and agent containers from prior crashed runs
    containers.sweep_orphaned_servers(LLAMA_SERVER_LOCK_DIR)
    containers.sweep_orphaned_proxies(CONTAINER_RUNTIME, PROXY_LOCK_DIR)
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

    cli_t3 = False
    cli_t3_advertise_host: str | None = None
    cli_lan = False
    cli_t3_tunnel: str | None = None
    cli_t3_tunnel_domain: str | None = None
    cli_t3_tunnel_quick: bool = False
    cli_t3_tunnel_runner: str = "auto"
    cli_t3_tunnel_keyring_account: str = "CLOUDFLARE_TUNNEL_TOKEN"
    cli_t3_tunnel_tailscale_mode: str = "serve"
    cli_t3_public_url: str | None = None
    container_argv: list[str] = []
    _arg_i = 0
    _raw_args = sys.argv[1:]
    while _arg_i < len(_raw_args):
        _arg = _raw_args[_arg_i]
        if _arg == "--t3":
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-lan", "--lan"):
            cli_lan = True
            _arg_i += 1
            continue
        if _arg in ("--t3-external-ip", "--t3-advertise-external-ip"):
            cli_t3_advertise_host = "external"
            _arg_i += 1
            continue
        if _arg == "--t3-advertise-host" and _arg_i + 1 < len(_raw_args):
            cli_t3_advertise_host = _raw_args[_arg_i + 1]
            _arg_i += 2
            continue
        if _arg.startswith("--t3-advertise-host="):
            cli_t3_advertise_host = _arg.split("=", 1)[1]
            _arg_i += 1
            continue
        if _arg in ("--t3-tunnel", "--tunnel") and _arg_i + 1 < len(_raw_args):
            cli_t3_tunnel = _raw_args[_arg_i + 1]
            cli_t3 = True
            _arg_i += 2
            continue
        if _arg.startswith("--t3-tunnel=") or _arg.startswith("--tunnel="):
            cli_t3_tunnel = _arg.split("=", 1)[1]
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-tunnel-domain", "--tunnel-domain") and _arg_i + 1 < len(_raw_args):
            cli_t3_tunnel_domain = _raw_args[_arg_i + 1]
            cli_t3 = True
            _arg_i += 2
            continue
        if _arg.startswith("--t3-tunnel-domain=") or _arg.startswith("--tunnel-domain="):
            cli_t3_tunnel_domain = _arg.split("=", 1)[1]
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-tunnel-quick", "--tunnel-quick"):
            cli_t3_tunnel_quick = True
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-tunnel-runner", "--tunnel-runner") and _arg_i + 1 < len(_raw_args):
            cli_t3_tunnel_runner = _raw_args[_arg_i + 1]
            cli_t3 = True
            _arg_i += 2
            continue
        if _arg.startswith("--t3-tunnel-runner=") or _arg.startswith("--tunnel-runner="):
            cli_t3_tunnel_runner = _arg.split("=", 1)[1]
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-tunnel-keyring-account", "--tunnel-keyring-account") and _arg_i + 1 < len(_raw_args):
            cli_t3_tunnel_keyring_account = _raw_args[_arg_i + 1]
            cli_t3 = True
            _arg_i += 2
            continue
        if _arg.startswith("--t3-tunnel-keyring-account=") or _arg.startswith("--tunnel-keyring-account="):
            cli_t3_tunnel_keyring_account = _arg.split("=", 1)[1]
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-tunnel-tailscale-mode", "--tunnel-tailscale-mode") and _arg_i + 1 < len(_raw_args):
            cli_t3_tunnel_tailscale_mode = _raw_args[_arg_i + 1]
            cli_t3 = True
            _arg_i += 2
            continue
        if _arg.startswith("--t3-tunnel-tailscale-mode=") or _arg.startswith("--tunnel-tailscale-mode="):
            cli_t3_tunnel_tailscale_mode = _arg.split("=", 1)[1]
            cli_t3 = True
            _arg_i += 1
            continue
        if _arg in ("--t3-public-url", "--public-url") and _arg_i + 1 < len(_raw_args):
            cli_t3_public_url = _raw_args[_arg_i + 1]
            cli_t3 = True
            _arg_i += 2
            continue
        if _arg.startswith("--t3-public-url=") or _arg.startswith("--public-url="):
            cli_t3_public_url = _arg.split("=", 1)[1]
            cli_t3 = True
            _arg_i += 1
            continue
        container_argv.append(_arg)
        _arg_i += 1

    if cli_t3_tunnel:
        from tunnel import TunnelValidationError, validate_tunnel_prerequisites

        try:
            validate_tunnel_prerequisites(
                tunnel_type=cli_t3_tunnel,
                domain=cli_t3_tunnel_domain,
                quick=cli_t3_tunnel_quick,
                runner=cli_t3_tunnel_runner,
                keyring_account=cli_t3_tunnel_keyring_account,
                tailscale_mode=cli_t3_tunnel_tailscale_mode,
                runtime=CONTAINER_RUNTIME,
            )
        except TunnelValidationError as err:
            logger.error(str(err))
            sys.exit(1)

    is_t3_mode = cli_t3 or (read_agent_mode(pi_container_dir) == "t3")
    raw_adv_host = cli_t3_advertise_host or os.environ.get("T3_ADVERTISE_HOST") or "localhost"
    if raw_adv_host.strip().lower() in ("external", "external_ip", "auto"):
        t3_advertise_host = get_host_external_ip()
    else:
        t3_advertise_host = raw_adv_host.strip()

    agent_ports = read_agent_ports(pi_container_dir)
    if cli_lan:
        agent_ports["expose"] = "lan"

    if is_t3_mode:
        claimed_agent_ports = {agent for _, agent in agent_ports["publish"]}
        if 3773 not in claimed_agent_ports:
            agent_ports["publish"].append((3773, 3773))
        has_active_tunnel = bool(cli_t3_tunnel or cli_t3_public_url)
        if (
            not has_active_tunnel
            and t3_advertise_host not in ("localhost", "127.0.0.1", "::1")
            and agent_ports.get("expose") != "lan"
        ):
            logger.warning(
                f"T3 Code advertised host is an external address ({t3_advertise_host}), "
                f"but agent.ports.expose is 'localhost'. Inbound connections from other devices "
                f"or external links may be refused unless agent.ports.expose is set to 'lan' in config.yaml "
                f"or --t3-lan is passed."
            )

    if agent_ports["publish"]:
        taken = containers.unavailable_host_ports(agent_ports["publish"], agent_ports["expose"])
        if taken:
            logger.error(f"Host port(s) already in use: {', '.join(str(p) for p in taken)}")
            holders = containers.port_holders(CONTAINER_RUNTIME, taken, project.project_key(PROJECT_DIR))
            for port in taken:
                holder = holders.get(port)
                logger.error(f"  {port} → {holder}" if holder else f"  {port} → not held by a container")
            logger.error(f"Free them, or change agent.ports.publish in {config_yaml_path}.")
            sys.exit(1)
        bind = "127.0.0.1" if agent_ports["expose"] != "lan" else "all interfaces"
        logger.info(
            "Publishing agent container port(s) on "
            + f"{bind}: "
            + ", ".join(f"{host}→agent {agent}" for host, agent in agent_ports["publish"])
            + "."
        )

    if is_t3_mode:
        t3_bind = "127.0.0.1" if agent_ports["expose"] != "lan" else "0.0.0.0"
        logger.info(
            f"T3 Code mode enabled (https://t3.codes):"
            f"\n  Inbound server port: {t3_bind}:3773"
            f"\n  Advertised host: {t3_advertise_host}"
            f"\n  Starting T3 Code server for Pi inside container..."
            f"\n  Once initialized, open https://t3.codes and use the pairing link displayed below."
        )

    with models_path.open("r") as file:
        data = json.load(file)
        server_configs, llama_hostnames, gateway_configs = containers.extract_server_configs(data)

    process_gateway_configs(gateway_configs, pi_container_dir)

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
                    logs_dir=LOGS_DIR,
                )
                servers.append(server)

            run_id = uuid.uuid4().hex[:12]
            agent_container_name = f"pi-coding-agent-{run_id}"
            proxy_name, network_name = project.project_scope(PROJECT_DIR)

            t3_public_url = cli_t3_public_url
            if is_t3_mode and cli_t3_tunnel:
                ttype = cli_t3_tunnel.strip().lower()
                if ttype == "tailscale":
                    from tunnel import TailscaleTunnelManager

                    ts_mgr = TailscaleTunnelManager(mode=cli_t3_tunnel_tailscale_mode, port=3773)
                    pub_url, adv_host = ts_mgr.start(stack)
                    t3_public_url = t3_public_url or pub_url
                    t3_advertise_host = adv_host
                elif ttype == "cloudflare":
                    from tunnel import CloudflareTunnelManager

                    cf_mgr = CloudflareTunnelManager(
                        domain=cli_t3_tunnel_domain,
                        quick=cli_t3_tunnel_quick,
                        runner=cli_t3_tunnel_runner,
                        keyring_account=cli_t3_tunnel_keyring_account,
                        port=3773,
                        runtime=CONTAINER_RUNTIME,
                        run_id=run_id,
                    )
                    pub_url, adv_host = cf_mgr.start(stack)
                    t3_public_url = t3_public_url or pub_url
                    t3_advertise_host = adv_host

            if is_t3_mode and t3_public_url:
                from tunnel import normalize_tunnel_url

                t3_public_url = normalize_tunnel_url(t3_public_url)
                if not cli_t3_advertise_host and not cli_t3_tunnel:
                    t3_advertise_host = urlparse(t3_public_url).hostname or t3_advertise_host

                logger.info(
                    f"\n=================================================================="
                    f"\n  T3 Code Remote Access Tunnel Active"
                    f"\n  Public URL: {t3_public_url}"
                    f"\n  Protocol:   HTTPS (WSS) - Standard Port (443)"
                    f"\n=================================================================="
                )

            ensure_proxy_image(CONTAINER_RUNTIME, repo_root=REPO_ROOT)

            def _make_netmgr(portconfig: str) -> ContainerNetworkManager:
                return ContainerNetworkManager(
                    RUNTIME,
                    network_name,
                    PROXY_IMAGE_TAG,
                    proxy_name=proxy_name,
                    config_dir=pi_container_dir,
                    llama_ports=portconfig,
                    llama_hostnames=" ".join(sorted(llama_hostnames)),
                    ipv6=ipv6_enabled,
                )

            netmgr = containers.start_dependencies_parallel(servers, _make_netmgr, stack)
            portconfig = netmgr.llama_ports or "[]"

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

                newest_shared = images.newest_shared_image_time(runtime=CONTAINER_RUNTIME)
                if newest_shared is None:
                    sys.exit(1)
                shared_tag, shared_ts = newest_shared

                reason = images.project_image_build_reason(
                    PROJECT_DIR, agent_image_tag, label_hash, shared_ts, shared_tag, runtime=CONTAINER_RUNTIME
                )
                if reason is not None:
                    logger.info(f"Building project-specific agent image: {agent_image_tag} ({reason})")
                    root_commands_file = PROJECT_DIR / ".pi-container" / "dependencies" / "root" / "commands.sh"
                    if root_commands_file.exists() and root_commands_file.stat().st_size > 0:
                        root_commands_path = str(root_commands_file)
                    else:
                        root_commands_path = str(DEFAULT_ROOT_COMMANDS)
                    project_tools = images.read_project_tools(PROJECT_DIR)
                    images.ensure_tool_images(project_tools, CONTAINER_RUNTIME, repo_root=REPO_ROOT)
                    build_project_image(
                        CONTAINER_RUNTIME,
                        root_commands_path,
                        agent_image_tag,
                        label_hash,
                        project_hash=project_hash,
                        project_path=project_path,
                        build_timestamp=images.now_iso(),
                        tools=project_tools,
                    )
                else:
                    logger.info(f"Using cached project-specific image: {agent_image_tag}")
            else:
                agent_ts = images.get_image_build_time(agent_image_tag, runtime=CONTAINER_RUNTIME)
                newest_shared = images.newest_shared_image_time(runtime=CONTAINER_RUNTIME)
                if newest_shared is not None and agent_ts is not None:
                    shared_tag, shared_ts = newest_shared
                    if shared_ts > agent_ts:
                        logger.info(
                            f"Shared base image {shared_tag} is newer than shared agent image {agent_image_tag}; "
                            "rebuilding shared agent image..."
                        )
                        build_agent(CONTAINER_RUNTIME)
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
                for arg in ("--volume", f"{vol_name}:{dest_path}:nodev,nosuid")
            ]

            git_hooks_dir = PROJECT_DIR / ".git" / "hooks"
            git_hooks_mount: list[str] = []
            if security_cfg.get("read_only_git_hooks", True) and (PROJECT_DIR / ".git").is_dir():
                git_hooks_dir.mkdir(parents=True, exist_ok=True)
                git_hooks_mount = ["--volume", f"{git_hooks_dir}:/workspace/.git/hooks:ro"]

            pi_container_cmd = [
                CONTAINER_RUNTIME,
                "run",
                "--rm",
                "--name",
                agent_container_name,
                *(["--security-opt", "no-new-privileges"] if not nested_cfg["enabled"] else []),
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
                *(
                    [
                        "--env",
                        "PI_CONTAINER_MODE=t3",
                        "--env",
                        f"T3_ADVERTISE_HOST={t3_advertise_host}",
                        *(["--env", f"T3_PUBLIC_URL={t3_public_url}"] if t3_public_url else []),
                        *(["--env", "T3_LAN=true"] if agent_ports["expose"] == "lan" else []),
                    ]
                    if is_t3_mode
                    else []
                ),
                *RUNTIME.tmpfs_args("/home/pi/"),
                "--volume",
                f"{agent_config_dir}:/home/pi/.pi/agent",
                "--volume",
                f"{PROJECT_DIR}:/workspace",
                *(["--volume", f"{pi_container_dir}:/workspace/.pi-container:ro"] if read_only_pi_container else []),
                *git_hooks_mount,
                *RUNTIME.tmpfs_args("/home/pi/.pi/agent/bin"),
                *RUNTIME.tmpfs_args("/workspace/.pi-container/exports"),
                *volume_args,
                *nested_args,
                *RUNTIME.nested_port_args(nested_cfg),
                *RUNTIME.port_publish_args(agent_ports),
                "--workdir",
                "/workspace",
                *[flag for path in tmpfs_paths for flag in RUNTIME.tmpfs_args(path)],
                "--env",
                f"CONTAINER_CHOWN_PATHS={':'.join(sorted(set(tmpfs_paths + list(active_volume_map.keys()) + ['/home/pi/.pi/agent/bin', '/workspace/.pi-container/exports'])))}",
                "--env",
                f"LLAMA_PORTS={portconfig}",
                "--env",
                f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger, allowlist=security_cfg.get('git_config_allowlist'))}",
                "--env",
                f"GIT_SAFE_DIRECTORY={'true' if git_cfg['safe_directory'] else 'false'}",
                "--env",
                f"GIT_REWRITE_SSH={'true' if git_cfg['rewrite_ssh_to_https'] else 'false'}",
                "--env",
                f"SETUP_GIT_CREDENTIALS={'true' if sc_cfg['setup_git_credentials'] else 'false'}",
                *(["--env", f"PI_CONTAINER_VERSION={schema_version}"] if schema_version else []),
                *[flag for k, v in {**sc_tokens, **agent_extras["env"]}.items() for flag in ("--env", f"{k}={v}")],
                *[flag for m in agent_extras["mounts"] for flag in ("--volume", m)],
                *[flag for c in agent_extras["capabilities"] for flag in ("--cap-add", c)],
                *[flag for d in agent_extras["devices"] for flag in ("--device", d)],
                *resource_limit_args(read_resource_limits(pi_container_dir, "agent")),
                agent_image_tag,
                *container_argv,
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

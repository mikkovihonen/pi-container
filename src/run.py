import sys

sys.dont_write_bytecode = True

import json
import logging
import re
import signal
import subprocess
import threading
import uuid
from contextlib import ExitStack
from datetime import UTC
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
    MITMWEB_FLOW_EXPORT_ENABLED,
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


# ─── mitmweb flow export ─────────────────────────────────────────────────


def _sanitize_ip(ip: str) -> str:
    """Make an IP safe as a filename component (mirrors the flow_export addon).

    IPv4 is unchanged; IPv6 colons become ``-`` and surrounding brackets are
    stripped. Must stay in sync with ``_sanitize_ip`` in flow_export.py.
    """
    return ip.strip("[]").replace(":", "-")


def _get_agent_container_ips(runtime_bin: str, container_name: str) -> list[str]:
    """Return the agent container's global isolated-net IPs (IPv4 and/or IPv6).

    The agent joins only the isolated network. The proxy sees whichever family a
    given connection used as the client source address, so a dual-stack agent
    can produce both a ``flows-<v4>.jsonl`` and a ``flows-<v6>.jsonl``. This
    collects every global-scope (non-loopback, non-link-local) address across
    its interfaces. Best-effort: returns [] on any error (container not up yet,
    no `ip`, etc.).
    """
    try:
        out = subprocess.run(
            [runtime_bin, "exec", container_name, "ip", "-j", "addr"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return []
        ips: list[str] = []
        for entry in json.loads(out.stdout):
            if entry.get("ifname") == "lo":
                continue
            for addr in entry.get("addr_info", []):
                # scope "global" excludes IPv6 link-local (fe80::, scope "link").
                if addr.get("scope") == "global" and addr.get("local"):
                    ips.append(addr["local"])
        return ips
    except Exception:
        return []


def _poll_agent_container_ips(
    runtime_bin: str,
    container_name: str,
    stop: object,
    timeout: float = 20.0,
    interval: float = 0.3,
    settle: float = 1.5,
) -> list[str]:
    """Poll for the agent's isolated-net IPs until found, ``stop`` set, or timeout.

    Once the first address appears, keep polling for a short ``settle`` window to
    catch a late-arriving second family (IPv6 is briefly "tentative" during
    duplicate-address detection), then return the union. ``stop`` is a
    ``threading.Event``; polling ends early once the agent exits.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    found: set[str] = set()
    settle_deadline: float | None = None
    while _time.monotonic() < deadline and not stop.is_set():  # type: ignore[attr-defined]
        found.update(_get_agent_container_ips(runtime_bin, container_name))
        if found:
            if settle_deadline is None:
                settle_deadline = _time.monotonic() + settle
            elif _time.monotonic() >= settle_deadline:
                break
        stop.wait(interval)  # type: ignore[attr-defined]
    return sorted(found)


def _get_latest_session_file(sessions_dir: Path) -> Path | None:
    """Return the most recently modified .jsonl file under sessions/.

    Walks all subdirectories (one per workspace) and picks the file with the
    highest ``st_mtime``. Returns None if the directory does not exist or is
    empty — this is normal on a fresh install and should not be treated as an
    error.
    """
    if not sessions_dir.exists():
        return None
    jsonl_files = sorted(sessions_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonl_files[0] if jsonl_files else None


def _extract_session_id(session_file: Path) -> str:
    """Parse the first line of a pi session JSONL file to extract its ``id``.

    Each pi session file begins with a JSON line of the form
    ``{"type":"session","id":"...",...}``. The id field is the session UUID
    used to name the flow-export directory.
    """
    with session_file.open("r") as f:
        first_line = f.readline()
    data = json.loads(first_line)
    session_id = data.get("id")
    if not session_id:
        raise ValueError(f"Session file {session_file} has no 'id' in its first line")
    return session_id


def _load_flows_from_mount(
    exports_dir: Path | None = None,
    flows_filename: str = "flows.jsonl",
) -> list[dict] | None:
    """Load flow history from the proxy container's mounted exports directory.

    The flow_export addon appends one flow per line (JSON Lines) to
    ``/home/mitmproxy/exports/{flows_filename}`` inside the proxy container,
    which is bind-mounted to ``{REPO_ROOT}/.pi-container/exports/`` on the host.
    ``flows_filename`` is unique per agent container (see ``export_mitmweb_flows``).
    This function reads that file and returns the parsed flow list.

    Malformed lines are skipped rather than failing the whole read — an unclean
    proxy exit can leave a partially-written final line.

    Returns:
        A list of flow dicts, or None if the file does not exist or cannot be
        read.
    """
    if exports_dir is None:
        exports_dir = REPO_ROOT / ".pi-container" / "exports"

    flows_file = exports_dir / flows_filename
    if not flows_file.exists():
        logger.info(f"No flow export file found at {flows_file}; skipping.")
        return None

    try:
        raw = flows_file.read_text()
    except OSError as e:
        logger.warning(f"Could not read flow export file {flows_file}: {e}")
        return None

    flows: list[dict] = []
    skipped = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            flows.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    if skipped:
        logger.warning(f"Skipped {skipped} malformed line(s) in {flows_file}.")
    return flows


def _resolve_flows_filenames(exports_dir: Path, client_ips: list[str] | None) -> list[str]:
    """Pick which raw ``flows-<ip>.jsonl`` file(s) to read for this session.

    A dual-stack agent can have both an IPv4 and IPv6 address, each with its own
    file, so this returns a list. Prefers the files for the agent container's own
    client IPs. If the IPs are unknown (discovery failed) but exactly one
    ``flows-*.jsonl`` file exists, use it — the common single-agent case stays
    robust. Returns [] when nothing can be attributed.
    """
    if client_ips:
        return [f"flows-{_sanitize_ip(ip)}.jsonl" for ip in client_ips]

    candidates = sorted(exports_dir.glob("flows-*.jsonl"))
    if len(candidates) == 1:
        logger.info(f"Agent IP unknown; using the only flow file present: {candidates[0].name}")
        return [candidates[0].name]
    if candidates:
        logger.warning(
            f"Agent IP unknown and {len(candidates)} flow files present; cannot attribute — skipping flow export."
        )
    return []


def export_mitmweb_flows(
    sessions_dir: Path | None = None,
    exports_dir: Path | None = None,
    client_ips: list[str] | None = None,
) -> Path | None:
    """Export mitmweb flow history to the exports directory, keyed by session.

    Reads the flows attributed to this agent container (by client IP, across both
    address families) from the proxy's mounted exports directory and writes a
    merged snapshot bucketed by UTC date under
    ``{exports_dir}/flows/{YYYY-MM-DD}/{HH-MM-SS-mmm}_{session-id}.json``.

    Args:
        sessions_dir: Where the pi session ``.jsonl`` files live — read only to
            determine the current session id.
        exports_dir: Base directory for the flow export (also where the raw
            ``flows-<ip>.jsonl`` files live). Defaults to ``.pi-container/exports``.
        client_ips: The agent container's isolated-net IPs (IPv4 and/or IPv6),
            used to select its ``flows-<ip>.jsonl`` files. See
            ``_resolve_flows_filenames`` for the unknown-IP fallback.

    Best-effort: never raises. Returns the path written or None if anything
    goes wrong.
    """
    from datetime import datetime

    if sessions_dir is None:
        sessions_dir = REPO_ROOT / "pi-coding-agent" / "home" / ".pi" / "agent" / "sessions"
    if exports_dir is None:
        exports_dir = REPO_ROOT / ".pi-container" / "exports"

    # 1. Determine the session ID from the most recent session file.
    latest = _get_latest_session_file(sessions_dir)
    if latest is None:
        logger.info("No pi session files found; skipping mitmweb flow export.")
        return None

    try:
        session_id = _extract_session_id(latest)
    except (ValueError, json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read session ID from {latest}: {e}")
        return None

    # 2. Load this agent container's flows from the proxy's mounted exports dir,
    #    merging its per-family (v4/v6) files. The flow_export addon appends
    #    per-client-IP files as flows complete; the volume mount makes them
    #    accessible to run.py on the host. Always create the session export file,
    #    even when no flows were captured — an empty export records that the
    #    session ran without traffic.
    flows: list[dict] = []
    consumed: list[Path] = []
    for filename in _resolve_flows_filenames(exports_dir, client_ips):
        part = _load_flows_from_mount(exports_dir, filename)
        if part is not None:
            flows.extend(part)
            consumed.append(exports_dir / filename)
    if not consumed:
        logger.info("No flow export file(s) found on the mount; writing an empty session export.")
    elif not flows:
        logger.info("mitmweb captured 0 flows; writing empty export.")
    # Merge into a single coherent timeline ordered by capture start time.
    flows.sort(key=lambda f: f.get("timestamp_start") or 0)

    # 3. Write the flow export as a timestamped JSON file, bucketed by UTC date.
    #    The millisecond-precision time plus the session id in the filename
    #    (e.g. 13-45-12-123_the-session-id.json) keeps exports sortable and
    #    unique even when the session id changes across the container's lifetime.
    now = datetime.now(UTC)
    date_dir = exports_dir / "flows" / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%H-%M-%S-") + f"{now.microsecond // 1000:03d}"
    export_path = date_dir / f"{timestamp}_{session_id}.json"
    try:
        export_path.write_text(
            json.dumps(
                {"session_id": session_id, "timestamp": now.isoformat(), "flows": flows},
                indent=2,
            )
        )
    except OSError as e:
        logger.warning(f"Could not write mitmweb flow export to {export_path}: {e}")
        return None

    # 4. The snapshot is now the durable copy — remove the raw per-IP file(s) we
    #    consumed so the same flows aren't stored twice. Only runs after a
    #    successful write; a failed write above returns early and keeps the raw
    #    files intact. The addon re-creates a file on the next flow from that IP.
    for raw_file in consumed:
        try:
            raw_file.unlink()
        except OSError as e:
            logger.warning(f"Could not remove consumed flow file {raw_file}: {e}")

    logger.info(f"Exported {len(flows)} flow(s) from mitmweb → {export_path}")
    return export_path


# ─── Startup validation ───────────────────────────────────────────────────


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

            # Unique name for this run's agent container. The proxy's flow_export
            # addon partitions captured traffic by client IP; naming the agent
            # container lets run.py look up its isolated-net IP (below) to find
            # the matching flows-<ip>.jsonl file.
            run_id = uuid.uuid4().hex[:12]
            agent_container_name = f"pi-coding-agent-{run_id}"

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
                    "--name",
                    agent_container_name,
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
                if MITMWEB_FLOW_EXPORT_ENABLED:

                    def _discover_agent_ips() -> None:
                        ips = _poll_agent_container_ips(CONTAINER_RUNTIME, agent_container_name, ip_stop)
                        if ips:
                            ip_holder["ips"] = ips

                    ip_thread = threading.Thread(target=_discover_agent_ips, daemon=True)
                    ip_thread.start()

                result = subprocess.run(pi_container_cmd)

                # Export mitmweb flow history for this session. The flow_export
                # addon appends per-client-IP files as flows complete; run.py reads
                # this agent's file(s) here (after it exits, before the
                # ContainerNetworkManager context exits and stops the proxy).
                if MITMWEB_FLOW_EXPORT_ENABLED:
                    ip_stop.set()
                    if ip_thread is not None:
                        ip_thread.join(timeout=2)
                    export_mitmweb_flows(
                        sessions_dir=REPO_ROOT / "pi-coding-agent" / "home" / ".pi" / "agent" / "sessions",
                        client_ips=ip_holder.get("ips"),
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

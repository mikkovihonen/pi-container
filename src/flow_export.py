import sys

sys.dont_write_bytecode = True

"""Host-side mitmweb flow export.

Reads the HTTP/HTTPS flows the proxy's ``flow_export`` addon staged (per client
IP, as JSON Lines) for a session and writes a merged, date-bucketed snapshot
under the project's ``.pi-container/exports/``. Also discovers the agent
container's isolated-net IPs so those raw files can be attributed to it.

This is the host counterpart of the container-side addon in
``pi-coding-agent-proxy/addons/flow_export/``.
"""

import json
import logging
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from config import PROJECT_DIR

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


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


def poll_agent_container_ips(
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
    which is bind-mounted to ``{PROJECT_DIR}/.pi-container/exports/`` on the host.
    ``flows_filename`` is unique per agent container (see ``export_mitmweb_flows``).
    This function reads that file and returns the parsed flow list.

    Malformed lines are skipped rather than failing the whole read — an unclean
    proxy exit can leave a partially-written final line.

    Returns:
        A list of flow dicts, or None if the file does not exist or cannot be
        read.
    """
    if exports_dir is None:
        exports_dir = PROJECT_DIR / ".pi-container" / "exports"

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
        exports_dir: The per-project exports directory — both where the proxy
            stages raw ``flows-<ip>.jsonl`` files (its bind mount) and where the
            session snapshot is written. Now that each workspace has its own
            proxy, this is per-project. Defaults to
            ``{PROJECT_DIR}/.pi-container/exports``.
        client_ips: The agent container's isolated-net IPs (IPv4 and/or IPv6),
            used to select its ``flows-<ip>.jsonl`` files. See
            ``_resolve_flows_filenames`` for the unknown-IP fallback.

    Best-effort: never raises. Returns the path written or None if anything
    goes wrong.
    """
    if sessions_dir is None:
        sessions_dir = PROJECT_DIR / ".pi-container" / "agent" / "sessions"
    if exports_dir is None:
        exports_dir = PROJECT_DIR / ".pi-container" / "exports"

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

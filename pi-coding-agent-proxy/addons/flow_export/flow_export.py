"""
mitmproxy addon: Flow Export

Appends every captured HTTP/HTTPS flow to a JSON Lines file as it completes,
one flow per line — partitioned by client IP so each connecting container's
traffic lands in its own ``flows-<client-ip>.jsonl`` file. This provides an
audit trail of all network traffic that passed through the proxy during a pi
coding agent session, attributable to the agent container it came from.

Attributing by client IP matters because a single proxy container is shared
across concurrent agent containers (each with a distinct isolated-net IP); a
single combined file could not tell their traffic apart.

Writing incrementally (on each flow's terminal hook) rather than all at once
on shutdown means the audit trail survives an unclean exit: if the proxy is
SIGKILL'd, crashes, or the ``done`` shutdown hook never runs, every flow seen
up to that point is already on disk.

The files are written to a shared volume mount so the host can read them after
the session ends. Each per-IP file is truncated the first time that IP is seen
in this proxy session, so a reused IP does not accumulate a previous session's
flows.

Terminal hooks and what they capture:
    response  — allowed flows (2xx/3xx/etc.) and the allowlist's synthetic 403s
    error     — flows that errored or were killed (e.g. allowlist 444)

Configuration:
    FLOW_EXPORT_DIR: Directory inside the proxy container to write per-IP files.
        Defaults to /home/mitmproxy/exports

Usage:
    mitmweb ... -s /home/mitmproxy/scripts/flow_export.py

Example loading (from mitmproxy command line):
    mitmweb --set scripts=flow_export.py
"""

import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mitmproxy import http

log = logging.getLogger(__name__)


def _safe_bytes(content: bytes | None) -> str:
    """Convert bytes to string, handling non-UTF8 gracefully."""
    if content is None:
        return ""
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return repr(content)


def _headers_to_dict(headers) -> dict:
    """Convert mitmproxy Headers object to a regular dict."""
    # Iterating a mitmproxy Headers object yields keys only; use .items() to get
    # (key, value) pairs. Later duplicate header names collapse to the last value.
    return {k: v for k, v in headers.items()}


def _flow_to_dict(flow) -> dict:
    """Convert a mitmproxy flow object to a JSON-serializable dict."""
    flow_dict: dict = {
        "id": getattr(flow, "id", ""),
        "type": getattr(flow, "type", "http"),
        "timestamp_start": getattr(flow, "timestamp_start", None),
        "timestamp_end": getattr(flow, "timestamp_end", None),
    }

    # Request
    if getattr(flow, "request", None) is not None:
        req = flow.request
        flow_dict["request"] = {
            "method": getattr(req, "method", ""),
            "url": str(getattr(req, "url", "")),
            "headers": _headers_to_dict(getattr(req, "headers", {})),
            "content": _safe_bytes(getattr(req, "content", None)),
            "content_type": req.headers.get("Content-Type", "") if hasattr(req, "headers") else "",
        }

    # Response
    if getattr(flow, "response", None) is not None:
        resp = flow.response
        flow_dict["response"] = {
            "status_code": getattr(resp, "status_code", 0),
            "headers": _headers_to_dict(getattr(resp, "headers", {})),
            "content": _safe_bytes(getattr(resp, "content", None)),
            "content_type": resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else "",
        }

    # Error
    error = getattr(flow, "error", None)
    if error is not None:
        flow_dict["error"] = str(error)

    return flow_dict


def _sanitize_ip(ip: str) -> str:
    """Make an IP safe as a filename component.

    IPv4 is unchanged; IPv6 colons become ``-`` (and surrounding brackets are
    stripped). run.py mirrors this exact transform to locate the file, so the
    two must stay in sync.
    """
    return ip.strip("[]").replace(":", "-")


def _client_ip(flow) -> str:
    """Return the source IP of the flow's client connection, or 'unknown'."""
    conn = getattr(flow, "client_conn", None)
    peername = getattr(conn, "peername", None) if conn is not None else None
    if peername and len(peername) >= 1 and peername[0]:
        return str(peername[0])
    return "unknown"


class FlowExporter:
    """mitmproxy addon that appends each flow to a per-client-IP JSON Lines file."""

    def __init__(self) -> None:
        self.export_dir: str = os.environ.get("FLOW_EXPORT_DIR", "/home/mitmproxy/exports")
        # Flow ids already written, so a flow that somehow reaches both terminal
        # hooks (response and error) is recorded at most once.
        self._seen: set[str] = set()
        # Client IPs whose file has been truncated for this session, so a reused
        # IP starts fresh rather than accumulating a prior session's flows.
        self._truncated: set[str] = set()
        self._count: int = 0
        # Controlled by the host via FLOW_EXPORT_ENABLED env var — when "false"
        # (or unset/anything else), the addon is a no-op so raw flows-<ip>.jsonl
        # files never pollute the bind-mounted exports directory.
        self._enabled: bool = os.environ.get("FLOW_EXPORT_ENABLED", "false").lower() == "true"
        if not self._enabled:
            log.info("[flow-export] Disabled (FLOW_EXPORT_ENABLED is not 'true'); skipping capture.")
            return

        try:
            os.makedirs(self.export_dir, exist_ok=True)
        except Exception as e:
            log.warning(f"[flow-export] Could not create export dir {self.export_dir}: {e}")

    def _path_for(self, client_ip: str) -> str:
        return os.path.join(self.export_dir, f"flows-{_sanitize_ip(client_ip)}.jsonl")

    def _append(self, flow: "http.HTTPFlow") -> None:
        """Append one flow as a single JSON line to its client's file. Deduped by
        flow id so a flow is never written twice. Best-effort: a write failure is
        logged, not raised, so it never disrupts the proxied request.
        """
        if not self._enabled:
            return
        flow_id = getattr(flow, "id", None) or str(id(flow))
        if flow_id in self._seen:
            return
        self._seen.add(flow_id)

        client_ip = _client_ip(flow)
        path = self._path_for(client_ip)
        # Truncate on first sighting of this IP this session, append thereafter.
        mode = "a" if client_ip in self._truncated else "w"
        self._truncated.add(client_ip)
        try:
            line = json.dumps(_flow_to_dict(flow), separators=(",", ":"))
            with open(path, mode) as f:
                f.write(line + "\n")
            self._count += 1
        except Exception as e:
            log.warning(f"[flow-export] Failed to append flow {flow_id} to {path}: {e}")

    def response(self, flow: "http.HTTPFlow") -> None:
        """Append allowed flows and the allowlist's synthetic 403 responses."""
        self._append(flow)

    def error(self, flow: "http.HTTPFlow") -> None:
        """Append flows that errored or were killed (e.g. allowlist 444)."""
        self._append(flow)

    def done(self) -> None:
        """Log a summary on shutdown. Flows are already persisted incrementally,
        so nothing is written here — this hook running is not required for a
        complete export."""
        log.info(
            f"[flow-export] Appended {self._count} flow(s) across "
            f"{len(self._truncated)} client(s) to {self.export_dir}"
        )


# Module-level addon instance for mitmproxy script loading
addon = FlowExporter()

# mitmproxy discovers addons via a module-level ``addons`` list.
addons = [addon]

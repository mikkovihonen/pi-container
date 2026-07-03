# Flow Export Proxy Addon

[← Documentation index](../../README.md) · [Proxy overview](overview.md) · [Allowlist](allowlist.md) · [Token replacer](token-replacer.md) · [Addon development](addon-development.md)

## Design Document

### Overview

This mitmproxy addon records every HTTP/HTTPS flow that passes through the transparent proxy during a pi coding agent session, **appending each flow to a [JSON Lines](https://jsonlines.org/) file as it completes** (one flow per line), **partitioned by client IP** into `flows-<client-ip>.jsonl`. This provides an **audit trail** of all network traffic the agent generated — including traffic the [allowlist](allowlist.md) blocked — attributable to the agent container it came from.

The files are written to a shared volume mount so `run.py` can read them on the host after the session ends.

### Why partition by client IP

A single proxy container is **shared across concurrent agent containers** (the proxy is ref-counted and reused). Each agent container has a distinct isolated-net IP, which the proxy sees as the client source address. Writing a single combined file could not tell one agent's traffic from another's; keying the file by client IP does. `run.py` looks up its own agent container's IP and reads the matching file.

### Why append per-flow instead of writing on shutdown

Writing incrementally means the audit trail **survives an unclean exit**. mitmproxy's `done` shutdown hook only runs on a *clean* stop (SIGTERM/SIGINT); if the proxy container is `SIGKILL`'d, crashes, or its process tree never forwards the signal, a write-on-shutdown design loses the entire session. Appending on each flow's terminal hook guarantees that every flow seen up to the moment of death is already on disk.

JSON Lines is the natural format for this: each line is a self-contained JSON object, so there is no array to keep open/close and a truncated final line (from a hard kill mid-write) costs at most one flow — the reader skips it.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    mitmproxy                                 │
│                                                             │
│  Flow → FlowExporter                                         │
│    │                                                        │
│    ├─► response(flow) ─┐                                    │
│    ├─► error(flow) ────┴─► _append(flow)                    │
│    │                        • dedupe by flow.id             │
│    │                        • ip = client_conn.peername[0]  │
│    │                        • path = flows-<ip>.jsonl       │
│    │                        • truncate on first sight of ip │
│    │                        • append json line thereafter   │
│    │                                                        │
│    └─► done()  → log summary only (not required for export) │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Components

| Component | File | Description |
|-----------|------|-------------|
| Addon | `flow_export.py` | Per-flow JSON serialization, client-IP partitioning, append |

There is no config file — the addon is driven entirely by one environment variable (below).

### Which hooks, and what they capture

The addon appends on the two **terminal** hooks, `response` and `error`. Every flow reaches exactly one of them, so each flow is written once (a `_seen` id set guards against the rare double). Verified against a live mitmproxy run:

| Flow outcome | Terminal hook | Appears in export as |
|--------------|--------------|----------------------|
| Allowed, completed | `response` | `response.status_code` set |
| Blocked (allowlist 403) | `response` | `response.status_code: 403` |
| Killed (allowlist 444 / `NO_RESPONSE`) | `error` | `error: "Connection killed."` |

A synthetic response set by the allowlist during its `request` hook **does** fire the `response` hook, which is why blocked-403 traffic still lands in the audit trail. A flow that is still in flight when the proxy dies (no response or error yet) is the only thing not captured — it is inherently incomplete.

### Configuration

The addon reads one environment variable at construction time:

| Variable | Default | Behavior |
|----------|---------|----------|
| `FLOW_EXPORT_DIR` | `/home/mitmproxy/exports` | Directory inside the container where per-client-IP files (`flows-<ip>.jsonl`) are written. Created if missing. Each per-IP file is truncated the first time that IP is seen in the session. |

IPv6 client IPs have their `:` replaced with `-` in the filename (e.g. `flows-fd00--2.jsonl`); `run.py` mirrors this transform to locate the file. Each line is written as compact JSON (no inter-token whitespace) — the line-per-flow structure makes it readable without indentation, and it keeps the file small.

### Export Format

The export is a JSON Lines file — one JSON object per line, **not** a JSON array. Example (two flows, formatted here for readability; on disk each is a single line):

```jsonc
{"id":"e4f1...","type":"http","timestamp_start":1719900000.123,"timestamp_end":1719900000.456,"request":{"method":"GET","url":"https://api.example.com/v1/models","headers":{"host":"api.example.com","authorization":"Bearer ..."},"content":"","content_type":""},"response":{"status_code":200,"headers":{"content-type":"application/json"},"content":"{\"ok\":true}","content_type":"application/json"}}
{"id":"a19c...","type":"http","timestamp_start":1719900001.0,"timestamp_end":null,"request":{"method":"POST","url":"https://blocked.example.com/","headers":{},"content":"","content_type":""},"error":"Connection killed."}
```

To read it back:

```python
import json
with open("flows-<ip>.jsonl") as f:
    flows = [json.loads(line) for line in f if line.strip()]
```

Notes on serialization:

- **`request` / `response` are omitted** when the flow has no request or no response respectively (e.g. a killed flow has no `response`).
- **`error`** is present only when the flow errored or was killed.
- **`headers`** are serialized with mitmproxy's `Headers.items()` (iterating a `Headers` object yields keys only, not pairs). Duplicate header names collapse to the last value.
- **`content`** is decoded as UTF-8 with `errors="replace"`; non-decodable bytes become the Unicode replacement character rather than failing the write. There is no size cap — large bodies are written in full.
- Appending is **best-effort**: any failure in `_append` is caught and logged as a warning so a serialization or I/O problem never disrupts the proxied request.

> **Security note:** the export contains full request/response bodies and headers, including any `Authorization` / cookie values that the [token_replacer](token-replacer.md) did **not** redact. Treat `flows-<ip>.jsonl` as sensitive.

### How It Works

1. **`__init__`** — resolves `FLOW_EXPORT_DIR` and creates it.
2. **`response(flow)`** — appends flows that received a response (including the allowlist's synthetic 403) to `flows-<client-ip>.jsonl`.
3. **`error(flow)`** — appends flows that errored or were killed.
4. Each per-IP file is **truncated the first time its IP is seen** this session (so a reused IP starts fresh), then appended to.
5. **`done()`** — logs a one-line summary on clean shutdown. Flows are already on disk, so this hook running is **not** required for a complete export.

### Integration with the Proxy Container

> **In this project the flow_export addon is already wired in and active.** The
> [Containerfile](../../pi-coding-agent-proxy/Containerfile) bakes the script and creates a
> `mitmproxy`-owned `/home/mitmproxy/exports` directory, and the
> [entrypoint](../../pi-coding-agent-proxy/entrypoint.sh) loads it with `-s`. `run.py` mounts the host
> export directory over `/home/mitmproxy/exports`. It names each run's agent
> container `pi-coding-agent-<run-id>`, looks up that container's isolated-net
> IPs (IPv4 **and** IPv6), and after the agent exits reads and merges the
> matching `flows-<ip>.jsonl` file(s) into a snapshot bucketed by UTC date under
> `.pi-container/exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.json`, then **deletes
> the raw file(s)** it consumed so the same flows aren't stored twice (only after
> the snapshot is written successfully). The steps below describe that wiring for
> reference / other proxies.
>
> **Note:** because the proxy is shared across runs, each agent's traffic is
> separated at capture time by client IP (rather than by a per-run filename that
> only the first run could set). A dual-stack agent produces one file per
> address family, which `run.py` merges (ordered by capture time). If `run.py`
> can't determine the agent's IPs but exactly one `flows-*.jsonl` file exists, it
> falls back to that file.

The addon is loaded as a mitmproxy script via `-s`. The script exposes a module-level `addons = [addon]` list, which is how mitmproxy discovers and registers it (a bare `addon = ...` variable would load but never register its hooks).

#### Step 1: Copy the script into the mitmproxy container

```dockerfile
COPY pi-coding-agent-proxy/addons/flow_export/flow_export.py \
     /home/mitmproxy/scripts/flow_export.py
```

#### Step 2: Provide a writable export directory

```dockerfile
RUN mkdir -p /home/mitmproxy/exports && chown mitmproxy:mitmproxy /home/mitmproxy/exports
```

Mount this directory from the host if you want to read the export after the session.

#### Step 3: Load the script via `-s`

```bash
mitmweb --mode transparent@8080 \
        -s /home/mitmproxy/scripts/flow_export.py \
        ...
```

Optionally override the default directory via the environment:

```bash
FLOW_EXPORT_DIR=/home/mitmproxy/exports mitmweb ...
```

#### Troubleshooting

- **No `flows-*.jsonl` files** — none of the traffic reached a terminal hook, or nothing connected. Check the mitmproxy logs for `[flow-export] Failed to append flow ...` or `[flow-export] Could not create export dir ...`; failures are logged as warnings, never raised.
- **`run.py` exports an empty snapshot** — it couldn't determine the agent container's IP (and either zero or >1 flow files were present, so it couldn't guess). Confirm the agent container came up with an isolated-net address.
- **Permission denied** — the `mitmproxy` user must own (or be able to write to) `FLOW_EXPORT_DIR`.
- **A truncated last line** — expected if the proxy was killed mid-write. Consumers should skip unparseable lines (`run.py`'s reader does).
- **No hooks fire / no `addons` list** — the script must define `addons = [addon]` at module level; without it the module imports but its hooks are never registered. See the [addon guide](addon-development.md#required-module-level-addons-list).

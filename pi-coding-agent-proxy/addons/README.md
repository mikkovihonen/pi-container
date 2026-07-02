# mitmproxy Addon Development Guide

This guide covers writing addons (scripts) for [mitmproxy](https://mitmproxy.org/) and [mitmweb](https://mitmproxy.org/).

## How mitmproxy Loads Addons

mitmproxy loads Python scripts via the `--set scripts=` command-line option (or the equivalent in `config.yaml`). The built-in `ScriptLoader` addon watches this option, loads each script file, and registers it as an addon.

The loading pipeline is:

```
mitmproxy starts
  → ScriptLoader.configure() detects "scripts" option
  → For each script path:
      → load_script(path)  — imports the Python module
      → AddonManager.register(module.addons)  — registers each addon in the module-level "addons" list
      → InvokeHook(LoadHook)  — calls addon.load(loader)
      → InvokeHook(ConfigureHook)  — calls addon.configure(updated_keys)
```

## Required: Module-Level `addons` List

Your script **must** expose a module-level `addons` **list** containing your addon instance(s). mitmproxy discovers addons via this list — a bare `addon = MyAddon()` variable is imported (so its `__init__` runs) but its event hooks are **never registered**, which silently disables the addon:

```python
# my_addon.py

class MyAddon:
    def request(self, flow: http.HTTPFlow) -> None:
        # inspect or modify the request
        pass

addon = MyAddon()

# CRITICAL: mitmproxy registers the objects in this list. Without it the
# script loads (config parses) but no hooks ever fire.
addons = [addon]
```

> **Why this matters:** if you only assign `addon = MyAddon()` and forget
> `addons = [addon]`, the module still imports and any config loading done in
> `__init__` runs — so it *looks* loaded — but `request`/`response` are never
> called and traffic passes through untouched.

## Addon Lifecycle Hooks

mitmproxy invokes the following methods on your addon instance at specific points:

| Hook | When Called | Purpose |
|------|-------------|---------|
| `load(loader)` | First registration | Register custom options via `loader.add_option(...)` |
| `configure(updated)` | After option change | React to option changes (`updated` is a set of changed keys) |
| `running()` | Proxy starts | Perform one-time startup tasks |
| `done()` | Proxy shuts down | Cleanup resources |
| `request(flow)` | HTTP request received | Inspect/modify request before forwarding |
| `response(flow)` | HTTP response received | Inspect/modify response before returning to client |
| `client_connected(client)` | Client connects to proxy | Track connected clients |
| `client_disconnected(client)` | Client disconnects | Cleanup per-client state |
| `server_connect(conn)`, `server_connected(conn)`, `server_disconnected(conn)` | Server connection events | Track upstream connections |
| `add_log(log_entry)` | New log entry | Process log messages |

Only the hooks your addon defines are called. Undeclared hooks are ignored — and because dispatch is by **exact method name**, a misnamed hook (e.g. `on_request` instead of `request`) is silently never invoked.

## The Flow Object

`request(flow)` and `response(flow)` receive an `http.HTTPFlow` instance:

```python
from mitmproxy import http

class MyAddon:
    def request(self, flow: http.HTTPFlow) -> None:
        # Hostname for policy decisions. Use pretty_host, NOT host:
        #   flow.request.host        → in TRANSPARENT mode this is the destination
        #                              IP (the client already resolved DNS itself)
        #   flow.request.pretty_host → the Host header / SNI hostname (what you want)
        host = flow.request.pretty_host

        # URL
        url = flow.request.url

        # Headers (case-insensitive lookup, mutable)
        auth = flow.request.headers.get("Authorization", "")
        flow.request.headers["X-Custom"] = "value"

        # Body (bytes)
        body = flow.request.get_content()
        flow.request.set_content(new_body)

        # Query string (tuple of (key, value) pairs)
        query = flow.request.query  # e.g., (("api_key", "abc123"), ("page", "1"))

        # Method and path
        method = flow.request.method
        path = flow.request.path

    def response(self, flow: http.HTTPFlow) -> None:
        # Same access pattern for flow.response
        response_body = flow.response.get_content()
```

### Modifying Bodies

When you modify a request or response body, you must:

1. **Set the new body**: `flow.request.set_content(new_bytes)`
2. **Update Content-Length**: `flow.request.headers["Content-Length"] = str(len(new_bytes))`
3. **Remove Transfer-Encoding**: If the original response had `Transfer-Encoding: chunked`, delete it — RFC 7230 requires `Content-Length` to be ignored when `Transfer-Encoding` is present, which would cause clients to misparse the modified body.

```python
def request(self, flow: http.HTTPFlow) -> None:
    body = flow.request.get_content()
    new_body = body.replace(b"secret_token", b"REDACTED")
    flow.request.set_content(new_body)
    flow.request.headers["Content-Length"] = str(len(new_body))
    if "Transfer-Encoding" in flow.request.headers:
        del flow.request.headers["Transfer-Encoding"]
```

### Modifying Headers

Headers support standard dict-like operations:

```python
# Case-insensitive lookup
value = flow.request.headers["authorization"]  # works

# Set (preserves insertion order in mitmproxy's MutableHeaders)
flow.request.headers["X-New-Header"] = "value"

# Delete
del flow.request.headers["X-Old-Header"]

# Check existence
if "Authorization" in flow.request.headers:
    ...
```

### Modifying Query Strings

Query strings are request-only (cannot appear in responses). They are accessed as a tuple of `(key, value)` pairs and modified via `_set_query()`:

```python
def request(self, flow: http.HTTPFlow) -> None:
    pairs = list(flow.request.query)  # [("api_key", "secret"), ("page", "1")]
    pairs = [(k, "REDACTED") if k == "api_key" else v for k, v in pairs]
    flow.request._set_query(pairs)
```

## Configuration

Load configuration from a YAML file at addon initialization:

```python
import yaml
import os

class MyAddon:
    def __init__(self, config_path: str = ""):
        self.config = {}
        self.rules = []
        self._load_config(config_path)

    def _load_config(self, config_path: str):
        if not config_path or not os.path.isfile(config_path):
            return
        with open(config_path) as f:
            self.config = yaml.safe_load(f) or {}
        self.rules = self.config.get("rules", [])
```

Support environment variable overrides:

```python
config_path = os.environ.get(
    "MY_ADDON_CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "config.yaml"),
)
addon = MyAddon(config_path=config_path)
```

Validate configuration at load time to catch errors early:

```python
def _validate_rule(self, rule: dict) -> None:
    name = rule.get("name", "<unnamed>")
    if "hostnames" not in rule:
        raise ValueError(f"Rule '{name}': missing 'hostnames'")
    if "content_patterns" not in rule or not isinstance(rule["content_patterns"], list):
        raise ValueError(f"Rule '{name}': 'content_patterns' must be a list")
    # ... more validation
```

## Design Patterns

### Three-Phase Processing (Detect → Apply → Log)

For addons that modify content based on multiple rules, use a three-phase approach to prevent one rule's replacement from being re-matched by another:

```python
def request(self, flow: http.HTTPFlow) -> None:
    hostname = flow.request.pretty_host  # Host header / SNI (see note above)

    # Phase 1: Detect — scan original data, collect findings, no modification
    findings = self._detect_matches(flow.request)

    # Phase 2: Apply — modify data in single passes per content type
    if not self.config.get("dry_run"):
        self._apply_modifications(flow.request, findings)

    # Phase 3: Log — emit console messages for each finding
    self._log_findings(findings)
```

### Grouping by Content Type

Parse each body type once, apply all matchers, write back once:

```python
def _apply_modifications(self, target, json_matchers, raw_matchers, header_matchers):
    # JSON: parse once, apply all, write back once
    if json_matchers:
        data = json.loads(target.get_content())
        for matcher in json_matchers:
            matcher.apply(data)
        target.set_content(json.dumps(data).encode())

    # Raw: collect all replacements from original, resolve overlaps, apply once
    if raw_matchers:
        body = target.get_content().decode()
        replacements = []
        for matcher in raw_matchers:
            for m in matcher.regex.finditer(body):
                replacements.append((m.start(), m.end(), matcher.strategy(m.group(0))))
        # resolve overlapping replacements, apply from end to start
        body = self._apply_replacements(body, replacements)
        target.set_content(body.encode())
```

### Capture-Group-Aware Header Replacement

When replacing tokens in headers with regexes that have capture groups (e.g., `(Bearer\s+)\S+`), preserve the capture groups while applying the replacement strategy to non-captured portions:

```python
def _replace_header_value(self, value: str, regex: re.Pattern, strategy: str) -> str:
    def _func(m):
        if m.groups():
            parts = []
            last_end = 0
            for i in range(1, len(m.groups()) + 1):
                gs, ge = m.start(i), m.end(i)
                if gs > last_end:
                    parts.append(replace_non_captured(m.group(0)[last_end:gs], strategy))
                parts.append(m.group(i))  # preserve capture group
                last_end = ge
            if last_end < len(m.group(0)):
                parts.append(replace_non_captured(m.group(0)[last_end:], strategy))
            return "".join(parts)
        return replace_full_match(m.group(0), strategy)

    return regex.sub(_func, value)
```

## Loading Your Addon

### From the command line:

```bash
mitmweb --set scripts=/path/to/your_addon.py
```

### In the Docker container (pi-coding-agent-proxy):

```dockerfile
COPY addons/your_addon/your_addon.py /home/mitmproxy/scripts/your_addon.py
COPY addons/your_addon/your_addon_config.yaml /home/mitmproxy/config/your_addon_config.yaml
```

Then in the container entrypoint:

```bash
mitmweb --listen-port 8080 \
        --set scripts=/home/mitmproxy/scripts/your_addon.py \
        ...
```

### From config.yaml:

```yaml
scripts:
  - /home/mitmproxy/scripts/your_addon.py
```

## Testing

Test your addon without running mitmproxy by importing the module directly and using `MagicMock` to simulate flow objects:

```python
from unittest.mock import MagicMock
from my_addon import MyAddon, addon

def test_request():
    a = MyAddon.__new__(MyAddon)
    a.config = {"rules": [...]}
    a._load_config = lambda *args: None  # skip config loading

    flow = MagicMock()
    # Set BOTH: the addon matches on pretty_host, host is the transparent-mode IP.
    flow.request.host = "api.example.com"
    flow.request.pretty_host = "api.example.com"
    flow.request.get_content = MagicMock(return_value=b'{"key": "secret"}')
    flow.request.set_content = MagicMock()
    flow.request.headers = MagicMock()
    flow.request.headers.keys = MagicMock(return_value=iter([]))

    a.request(flow)

    flow.request.set_content.assert_called()
    new_body = json.loads(flow.request.set_content.call_args[0][0])
    assert new_body["key"] == "REDACTED"
```

Run tests with pytest:

```bash
python -m pytest tests/test_my_addon.py -v
```

## Common Pitfalls

1. **Missing `addons` list**: The script must define `addons = [MyClass()]` at module level. A bare `addon = MyClass()` is imported (so `__init__`/config loading runs and it *looks* loaded) but its hooks are never registered, so it silently does nothing.

2. **Wrong hook name**: Hooks are dispatched by exact name — use `request(self, flow)` / `response(self, flow)`, **not** `on_request` / `on_response`. A misnamed hook is silently never called. Note that a unit test calling `addon.on_request(flow)` directly will still pass, hiding both this and the missing-`addons`-list bug — only an integration run through mitmproxy exercises real dispatch.

3. **Hostname is an IP in transparent mode**: For policy/matching use `flow.request.pretty_host` (Host header / SNI), not `flow.request.host` — in transparent mode the latter is the destination IP the client already resolved, so hostname rules never match. `pretty_host` has no port, so no port-stripping is needed.

4. **Transfer-Encoding conflict**: When you modify a body and set a new `Content-Length`, delete `Transfer-Encoding` if present. Otherwise, clients ignore `Content-Length` per RFC 7230.

5. **JSON formatting**: `json.dumps()` re-serializes parsed JSON, changing formatting and potentially key order. This is necessary for path-based matching but means the response body may differ from the upstream.

6. **Form body encoding**: Use `urllib.parse.parse_qsl()` to parse form bodies as a list of `(key, value)` tuples (preserves duplicate keys). Re-encode with `urllib.parse.urlencode()`.

7. **Non-UTF-8 bodies**: Use `body.decode("utf-8", errors="ignore")` when treating raw bytes as strings.

8. **Header case**: mitmproxy's `Headers` object is case-insensitive for lookups but preserves original casing. Use `headers[key.lower()]` for case-insensitive checks, but set via the original key name to preserve order.

9. **Query strings are request-only**: `body.query` patterns should only apply to requests. Check `is_response` or `hasattr(target, 'query')` before processing.

## mitmweb UI Extensions

**mitmweb addons cannot extend the web UI.** The mitmweb interface is built with Tornado, but its routes, templates, and static files are hardcoded in `mitmproxy/tools/web/app.py`. There is no API for addons to:

- Add new HTTP routes or endpoints to the web UI
- Inject custom templates or static files
- Add new tabs, panels, or columns to the flow list
- Extend the UI with custom JavaScript/CSS

The addon system only supports HTTP traffic manipulation (lifecycle hooks, flow modification, custom commands). If you need a custom web interface, build a **separate web server** (Flask, FastAPI, etc.) that communicates with mitmproxy via its HTTP API.

### Building a Separate Web UI for Your Addon

Use mitmproxy's built-in HTTP API to build a custom dashboard. The web server exposes JSON endpoints that your addon or external app can query:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/flows` | `GET` | List all flows |
| `/flows/{id}` | `GET` | Get flow details |
| `/flows/{id}/content.data` | `GET` | Get raw request/response body |
| `/flows/{id}/content/{view}.json` | `GET` | Get formatted content (e.g., `Auto`, `JSON`, `Text`) |
| `/flows/resume` | `POST` | Resume all intercepted flows |
| `/flows/kill` | `POST` | Kill all intercepted flows |
| `/flows/{id}/resume` | `POST` | Resume a specific flow |
| `/flows/{id}/kill` | `POST` | Kill a specific flow |
| `/flows/{id}/replay` | `POST` | Replay a flow |
| `/commands` | `GET` | List available commands |
| `/commands/{cmd}` | `POST` | Execute a command |
| `/options` | `GET` | Get current options |
| `/state` | `GET` | Get proxy server state |

Example — query mitmproxy from a FastAPI app:

```python
import httpx
from fastapi import FastAPI

app = FastAPI()
MITMPROXY_URL = "http://localhost:8081"  # mitmweb port

@app.get("/my-addon/status")
async def get_status():
    async with httpx.AsyncClient() as client:
        state = await client.get(f"{MITMPROXY_URL}/state")
        flows = await client.get(f"{MITMPROXY_URL}/flows")
        return {
            "servers": state.json()["servers"],
            "flow_count": len(flows.json()),
        }
```

### Using mitmproxy Commands

For programmatic interactions without a web UI, define custom commands in your addon:

```python
from mitmproxy import command, ctx
from mitmproxy.http import HTTPFlow
from collections.abc import Sequence

class MyAddon:
    @command.command("my.addon.list_tokens")
    def list_tokens(self, flows: Sequence[HTTPFlow]) -> None:
        """List all API keys found in the last 100 flows."""
        for f in flows[-100:]:
            body = f.request.get_content()
            if b"api_key" in body:
                ctx.log.info(f"Flow {f.id}: api_key found")
```

Invoke from the CLI:

```bash
mitmweb --set scripts=my_addon.py --listen-port 8080 -N "my.addon.list_tokens"
```

### Exporting Static Flow Data

mitmproxy provides a `web_static_viewer` option to export flows to a static HTML page (read-only, no interactivity):

```bash
mitmweb --set scripts=my_addon.py --set web_static_viewer=./export flows.dump
```

This writes `index.html`, `static/`, `flows.json`, and flow content to the specified directory.

## Existing Addons for Reference

| Addon | Path | Description |
|-------|------|-------------|
| Token Replacer | [`token_replacer/`](token_replacer/) | Replaces sensitive tokens in HTTP bodies/headers based on hostname + content patterns |
| Allowlist | [`allowlist/`](allowlist/) | Filters HTTP traffic by domain/IP allowlist or blocklist; supports glob, regex, CIDR ranges |
| Flow Export | [`flow_export/`](flow_export/) | Appends all flows (including blocked/killed) to a JSON Lines audit trail as they complete |

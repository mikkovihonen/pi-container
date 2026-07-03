# Token Replacement Proxy Addon

[← Documentation index](../../README.md) · [Proxy overview](overview.md) · [Allowlist](allowlist.md) · [Flow export](flow-export.md) · [Addon development](addon-development.md)

## Design Document

### Overview

This mitmproxy addon intercepts HTTP requests flowing through the transparent proxy and conditionally replaces sensitive token values (e.g., API keys, authorization bearer tokens, session cookies) in request bodies, headers, or query parameters.

The replacement is only applied when **both** conditions match:
1. **Hostname match** — the request's target hostname matches a configured pattern.
2. **Content match** — the request body or headers contain a token that matches a configured pattern.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    mitmproxy                                │
│                                                             │
│  Flow → TokenReplacerAddon (request / response)             │
│    │                                                        │
│    ├─► Phase 1: Detection                                   │
│    │     • Check hostname against allowlist                 │
│    │     • Run every matcher against original data          │
│    │     • Collect found tokens (no modifications)           │
│    │                                                        │
│    ├─► Phase 2: Application (if not dry_run)                │
│    │     • Group matchers by content type                   │
│    │     • Parse each body type ONCE from original data     │
│    │     • Apply all matchers of that type                  │
│    │     • Write back ONCE per type                         │
│    │                                                        │
│    └─► Phase 3: Logging                                     │
│          • Emit per-rule log entries for each match         │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Replacement strategies:                              │  │
│  │  • Static mask   • Hash (SHA-256)                     │  │
│  │  • Random UUID                                        │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Components

| Component | File | Description |
|-----------|------|-------------|
| Addon | `token_replacer.py` | Everything: matchers, rule engine, config loading |
| Config | `token_replacer.yaml` | YAML rules: hostnames, content patterns, replacements |
| Tests | `tests/test_token_replacer.py` | 61 unit tests covering all replacement paths |

### Replacing Sensitive Values from Environment Variables

When `replace_with.value` matches the pattern `${ENV:VAR_NAME}` or `${ENV:VAR_NAME,default}`, the value is resolved from the process environment at **config load time**. This allows secrets to stay out of the YAML config file:

```yaml
rules:
  - name: "API key in body"
    hostnames:
      - "api.example.com"
    content_patterns:
      - field: "body.json"
        path: "$.api_key"
        regex: "ak_[A-Za-z0-9]{20,}"
    replace_with:
      strategy: "static"
      value: "${ENV:REDACTION_TOKEN}"
```

**Syntax:**

| Syntax | Behavior |
|--------|----------|
| `${ENV:MY_VAR}` | Resolved from `os.environ["MY_VAR"]`. Raises ``KeyError`` at config load if unset. |
| `${ENV:MY_VAR,fallback}` | Resolved from `os.environ["MY_VAR"]`. Uses ``fallback`` if unset. |
| Any other string | Passed through unchanged (no resolution). |

Resolution happens at config load time — matchers always receive plain strings, so there is no runtime overhead per request. The resolved value is **never** logged; only the original (sensitive) token value is written to log output.

> **Security note:** Environment variables are already accessible to the process. This feature simply avoids hardcoding secret values in YAML files that may be committed to version control.

### Configuration Format (`token_replacer.yaml`)

```yaml
# Global settings
global:
  log_replacements: true            # Log replacement events to mitmproxy console
  dry_run: false                    # If true, only log without modifying

# Rule definitions
rules:
  - name: "API key in body"
    hostnames:
      - "api.example.com"
      - "auth.*.example.com"          # wildcard support
    content_patterns:
      - field: "body.json"            # target: body.json, body.form, body.raw, headers
        path: "$.api_key"             # JSONPath for body.json
        regex: "ak_[A-Za-z0-9]{20,}"  # additional regex filter on value
    replace_with:
      strategy: "static"              # static | hash | uuid
      value: "REDACTED_TOKEN"

  - name: "Bearer token in header"
    hostnames:
      - ".*\.example\.com"
    content_patterns:
      - field: "headers"
        header_name: "Authorization"
        regex: "(?<=Bearer\\s)\\S+"
    replace_with:
      strategy: "static"
      value: "REDACTED_BEARER"

  - name: "Session cookie"
    hostnames:
      - "session.example.com"
    content_patterns:
      - field: "headers"
        header_name: "Cookie"
        regex: "session=([a-f0-9]{32})"
    replace_with:
      strategy: "hash"                  # SHA-256 of original for audit

  - name: "API key in query string"
    hostnames:
      - "api.example.com"
    content_patterns:
      - field: "body.query"
        field_name: "api_key"
        regex: "ak_[A-Za-z0-9]{20,}"
    replace_with:
      strategy: "static"
      value: "REDACTED_API_KEY"
```

### Replacement Strategies

| Strategy | Description |
|----------|-------------|
| `static` | Replace with the literal string specified in `replace_with.value` |
| `hash`   | Replace with SHA-256 hash of the **original** token (the `value` field is ignored) |
| `uuid`   | Replace with a random UUID v4 (the `value` field is ignored) |

### How It Works

The `request(flow)` hook (and its mirror, `response(flow)`) executes in three phases to prevent one rule's replacement value from being re-matched by another rule:

1. **Hostname selection** — The addon extracts the request's hostname (`flow.request.pretty_host` — the Host header / SNI, correct under transparent proxying; `flow.request.host` would be the destination IP) and collects all rules whose `hostnames` patterns match (supporting exact, glob `*`, and regex).
2. **Phase 1 — Detection** — Every applicable rule's matchers scan the **original** request data (parsed JSON, form, raw body, and headers). Findings (original token values) are collected but nothing is modified.
3. **Phase 2 — Application** — Matchers are grouped by content type. Each body type is parsed **once** from the original request, all matchers of that type are applied to the shared parsed data, and the result is written back **once**. This ensures that one rule's replacement value can never be re-matched by another rule's regex.
4. **Phase 3 — Logging** — If `log_replacements` is enabled, a console message is emitted for every match found in Phase 1.

Content types:
| Field | Target | Parsing |
|-------|--------|---------|
| `body.json` | JSON body | `json.loads()` → JSONPath walk → regex filter |
| `body.form` | URL-encoded form | `parse_qsl()` → field lookup → regex filter |
| `body.query` | URL query string | `parse_qsl()` → field lookup → regex filter |
| `body.raw` | Arbitrary text | Direct regex application |
| `headers` | HTTP header value | Header lookup (case-insensitive) → regex application |

> **Note:** JSONPath array indices (e.g., `$.items[0].key`) are not supported.
> Use `body.raw` with a regex to match tokens inside arrays, or restructure
> the JSON to use named keys instead of array indices.

> **Note:** `body.query` is only processed for requests, not responses. Query
> strings are a request-only concept and cannot appear in HTTP responses.

> **Important:** `regex` is required for `body.json`, `body.form`, `body.query`,
> and `body.raw` content patterns. Without an explicit regex, a wildcard `.*` would
> match and replace **all** values for the targeted field, which is a footgun.

### Design choices and limitations

**Hostname patterns: `*` and `?` are glob wildcards.**
Patterns containing only plain characters, `*` (match any number of characters), and `?` (match exactly one character) are treated as globs. Patterns containing any other regex metacharacter (e.g. `^`, `$`, `.`, `+`, `{`, `}`, `[`, `]`, `|`, `(`, `)`) are treated as full regular expressions. There is no way to match a literal `*` or `?` in a glob pattern — use a regex pattern if needed.

**`regex` is required for `body.json`, `body.form`, `body.query`, and `body.raw`.**
A `regex` is mandatory for all content pattern fields. Without one, a wildcard `.*` would match and replace **all** values for the targeted field, which is a footgun. Use `regex: ".*"` to match all values explicitly.

**`body.json` paths: no `..` (descendant) operator, no array indices.**
The JSONPath implementation is "JSONPath-like" — it uses dot notation for path traversal but does not support the full JSONPath spec:
- `..` (descendant operator) is not supported. A path like `$..api_key` will be rejected at config load time.
- Array indices like `$.items[0].key` are not supported. A warning is emitted and the matcher returns no matches. Use `body.raw` with a regex to match tokens inside arrays.

**`content_patterns` must be a YAML list.**
Passing a string or other non-list type for `content_patterns` produces a clear validation error at load time rather than a confusing `AttributeError` at request time.

**Three-phase architecture prevents cross-rule interference.**
Each request is processed in three phases: (1) detection on original data, (2) single-pass application grouped by content type, (3) logging. This ensures one rule's replacement value can never be re-matched by another rule's regex, even when multiple rules target the same content type.

**Sequential matcher application for structured content types.**
For `body.json`, `body.form`, and `body.query`, all matchers of the same type operate on the same parsed data object in sequence. If two matchers target the **same key/field**, the second matcher will see the first matcher's replacement value and may re-match it. For `body.raw` and `headers`, replacements are collected from the **original** value first and applied in a single pass, so this cross-rule re-matching is fully prevented. In practice each field is targeted by at most one rule, so sequential application rarely causes issues. Avoid configuring multiple matchers for the same key/field.

**Response processing mirrors request processing.**
The addon applies the same hostname + content-pattern matching to HTTP responses as to requests, masking sensitive tokens that may have been returned by upstream servers (e.g., API keys in error responses, tokens in 401 bodies).


### Integration with the Proxy Container

> **In this project the token_replacer is already wired in and active.** The
> [Containerfile](../../pi-coding-agent-proxy/Containerfile) bakes the script + a default config, the
> [entrypoint](../../pi-coding-agent-proxy/entrypoint.sh) loads it with `-s` and points
> `TOKEN_REPLACER_CONFIG_PATH` at `/home/mitmproxy/config/token_replacer.yaml`,
> and `run.py` mounts the host's [`.pi-container/token_replacer.yaml`](../../.pi-container/token_replacer.yaml)
> over it (also injecting any `${ENV:VAR}` secrets it references). The steps below
> describe that wiring for reference / other proxies.

The token_replacer addon is loaded as a mitmproxy script via the `scripts` option. The script exposes a module-level `addons = [addon]` list, which is how mitmproxy discovers and registers it (a bare `addon = ...` variable would load but never register its hooks).

#### Step 1: Copy files into the mitmproxy container

```dockerfile
COPY pi-coding-agent-proxy/addons/token_replacer/token_replacer.py \
     /home/mitmproxy/scripts/token_replacer.py
```

The image must also have `pyyaml` installed (`pip install pyyaml`).

#### Step 2: Load the script via `-s` / `--set scripts`

Add the following to your mitmproxy command line (e.g., in the container entrypoint). `-s` is the short form and can be repeated to load multiple addons:

```bash
mitmweb --mode transparent@8080 \
        -s /home/mitmproxy/scripts/token_replacer.py \
        ...
```

#### Step 3: Point the addon at its config

The addon reads `TOKEN_REPLACER_CONFIG_PATH` at import time (falling back to `token_replacer.yaml` alongside the script). In this project it is baked as image `ENV` and the config is mounted there from the host:

```dockerfile
ENV TOKEN_REPLACER_CONFIG_PATH=/home/mitmproxy/config/token_replacer.yaml
```

#### Troubleshooting

- If the addon does not load or has no effect, check the mitmproxy logs. Common issues include:
  - **No `addons = [addon]` list** — the script loads (config parses) but hooks never fire.
  - **Wrong hook name** — hooks must be `request` / `response`, not `on_request` / `on_response`.
  - **Matching on `flow.request.host`** — use `pretty_host`; in transparent mode `host` is the destination IP, so hostname rules never match.
  - Incorrect script path (verify the file exists in the container).
  - YAML syntax errors in the config file.
  - Python import errors (ensure `pyyaml` is installed in the mitmproxy environment).
  - Script file not readable by the `mitmproxy` user (COPY preserves host file mode; `chmod a+r` in the image if needed).

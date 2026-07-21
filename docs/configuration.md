# Configuration


## Environment Configuration

The application uses a `.env` file for managing environment-specific settings. See `.env.example` for all available options.

### Security

- **`ADMIN_PASSWORD`** MUST be changed from the default `CHANGEME` before running.
  The proxy's mitmweb UI will refuse to start with a default or empty password.
- **Model integrity**: Set `sha256` in `models.json` to verify downloaded model files.
  Without a checksum, downloads proceed without integrity verification.

### Run Configuration

The following environment variables are used by `build.sh` and `run.sh` to configure the container runtime, proxy, and `llama-server`:

| Variable | Description | Default |
|----------|-------------|---------|
| `PI_IMAGE_TAG` | The tag of the pi container image to run | `pi-coding-agent:local` |
| `PROXY_IMAGE_TAG` | The tag of the proxy container image to run | `pi-coding-agent-proxy:local` |
| `LLAMA_BIN` | Path to the `llama-server` executable | `llama-server` or `/opt/homebrew/bin/llama-server` |
| `BRIDGE_INTERFACE` | Host bridge interface for container networking | Per-runtime: `podman0` / `docker0` |
| `PROXY_UPSTREAM_NETWORK` | The upstream network the proxy connects to for internet access | Per-runtime: `default` / `podman` / `bridge` |
| `LOG_LEVEL` | Log level | `INFO` |
| `ADMIN_PASSWORD` | Password for mitmproxy Web UI | `CHANGEME` |
| `CONTAINER_RUNTIME` | Container CLI to use (`docker` or `podman`) | Auto-detected (prefers `docker` > `podman`) |

`BRIDGE_INTERFACE` and `PROXY_UPSTREAM_NETWORK` are derived from `CONTAINER_RUNTIME` and rarely need setting; provide them only to override the per-runtime default for your host.

> Per-project settings (IPv6, proxy DNS, mitmweb UI exposure, llama-server startup tuning, resource limits, tmpfs, flow export, egress, extra agent env/mounts) are **not** environment variables — they live in `.pi-container/config.yaml`, documented below.

## Per-workspace Configuration

### Introduction

When launched, pi-container looks for workspace-specific overrides in `./.pi-container` and package dependencies in the directory it's launched in. Each workspace gets its own agent config, proxy, isolated network, and chat templates — all under that workspace's `.pi-container/` (seeded from the `pi-coding-agent/default/` template on first run).

Orchestration settings live in a single **`config.yaml`**; the proxy addon configs (`allowlist.yaml`, `token_replacer.yaml`) stay in their own files because they're mounted into and parsed by the proxy container.

### The `models.json` file and `serverCustomParameters`

The LLM models that pi-container serves are configured in `.pi-container/agent/models.json`, which is pi-coding-agent's own `models.json` format with an extended `serverCustomParameters` block per provider. This block is the bridge between the pi-container orchestration layer and llama-server — it tells pi-container which model files to download, where they live on disk, and which `llama-server` command-line flags to pass when launching the model.

The file structure looks like this:

```json
// .pi-container/agent/models.json
{
  "providers": {
    "local-ornith": {
      "baseUrl": "http://llama:9999/v1",
      "api": "openai-completions",
      "apiKey": "not-required",
      "compat": { ... },
      "models": [ ... ],
      "serverCustomParameters": {
        "hfModels": { ... },
        "flags": [ ... ]
      }
    }
  }
}
```

Not all of pi-coding-agent's `models.json` fields are consumed by pi-container — only a subset is read, since most model metadata (IDs, context windows, tool-calling flags, etc.) is pi-coding-agent's concern, not llama-server's. At startup, `run.py` iterates `providers` and for each entry that has `serverCustomParameters` it extracts:

| Field | Used by pi-container? | Notes |
|-------|----------------------|-------|
| `providers.<name>` (key) | **Yes** — as `server_id` | Becomes the llama-server `--alias` and the sharing key. Must be unique per provider. |
| `baseUrl` | **Yes** — port only | `run.py` parses `http://llama:9999/v1` and extracts the port (`9999`) so the agent container knows which llama-server port to target. The scheme/host/path are not validated. |
| `serverCustomParameters.hfModels` | **Yes** | Model file download config + per-model additional flags. |
| `serverCustomParameters.flags` | **Yes** | llama-server CLI flags, passed verbatim. |
| `api`, `apiKey` | No | pi-coding-agent uses these for API negotiation; pi-container ignores them. |
| `compat` | No | pi-coding-agent compatibility flags; pi-container ignores them. |
| `models[].id`, `models[].name`, `models[].contextWindow`, `models[].toolCalling`, `models[].vision`, `models[].reasoning`, `models[].options`, etc. | No | pi-coding-agent model metadata. pi-container does not read these. |

In short, pi-container only needs `baseUrl` (for the port), the provider name, and `serverCustomParameters` — the rest of the `models.json` structure is for pi-coding-agent's model registry and is passed through untouched.

The `serverCustomParameters` object has two fields:

#### `hfModels` — model files

`hfModels` is a dictionary mapping **labels** (arbitrary short names) to per-model download and flag configuration. Each entry tells pi-container how to fetch a model file from Hugging Face and how to pass it to llama-server:

```json
"hfModels": {
  "main": {
    "fileFlag": "--model",
    "repo": "deepreinforce-ai/Ornith-1.0-35B-GGUF",
    "file": "ornith-1.0-35b-Q6_K.gguf",
    "dir": "Ornith-1.0-35B-GGUF",
    "additionalServerFlags": [],
    "sha256": "<optional hex digest>"
  },
  "draft": {
    "fileFlag": "--model-draft",
    "repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF",
    "file": "mtp-gemma-4-26B-A4B-it.gguf",
    "dir": "gemma-4-26B-A4B-it-qat-GGUF",
    "additionalServerFlags": [
      "--spec-type", "draft-mtp",
      "--spec-draft-n-min", 1,
      "--spec-draft-n-max", 4
    ]
  },
  "mmproj": {
    "fileFlag": "--mmproj",
    "repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF",
    "file": "mmproj-F16.gguf",
    "dir": "gemma-4-26B-A4B-it-qat-GGUF",
    "additionalServerFlags": []
  }
}
```

Each `hfModels` entry requires:

| Field | Description |
|-------|-------------|
| `fileFlag` | The llama-server flag name (e.g. `--model`, `--model-draft`, `--mmproj`). This flag is emitted with the model's resolved path when launching llama-server. |
| `repo` | Hugging Face repository slug (e.g. `unsloth/gemma-4-26B-A4B-it-qat-GGUF`). |
| `file` | Filename within the repository (e.g. `ornith-1.0-35b-Q6_K.gguf`). |
| `dir` | Subdirectory under `llama-server/models/` where the file is cached. Files from different repos use different dirs to avoid collisions. |
| `additionalServerFlags` | Extra flags appended after this model's `fileFlag` + path on the llama-server command line. Useful for per-model options like speculative decoding settings (`--spec-type`, `--spec-draft-n-max`). |
| `sha256` | *(optional)* SHA-256 hex digest of the model file. If set, pi-container verifies the downloaded file before starting llama-server; a mismatch aborts startup. Without a checksum, downloads proceed without integrity verification. |

The `hfModels` entries are processed in sorted label order, but each entry's `additionalServerFlags` preserve their specified order — and the overall `flags` list is passed to llama-server verbatim, in the order defined.

Multiple labels are common: `main` for the base model, `draft` for a speculative decoding draft model, `mmproj` for a multi-modal projection head, or additional labels for LoRA adapters and other llama-server features. A provider **must** have at least one entry (the "main" model), and at minimum the `main` label should point to the primary model file.

#### `flags` — llama-server command-line flags

`flags` is an array of strings and numbers passed directly to llama-server. This is where you tune inference behavior:

```json
"flags": [
  "--no-mmap",
  "--mlock",
  "--kv-offload",
  "--threads", 10,
  "--threads-batch", 8,
  "--parallel", 1,
  "--batch-size", 4096,
  "--ubatch-size", 512,
  "--flash-attn", "on",
  "--ctx-size", 131072,
  "--ctx-checkpoints", 32,
  "--checkpoint-min-step", 256,
  "--repeat-penalty", 1.0,
  "--top_p", 0.95,
  "--top_k", 64,
  "--prio", 2,
  "--cache-ram", 4096,
  "--jinja",
  "--chat-template-file", ".pi-container/chat-templates/Ornith-1.0-35B-FP8/chat_template.jinja",
  "--chat-template-kwargs", "{\"enable_thinking\":true}",
  "--n-gpu-layers", 999
]
```

Each item is a single CLI token — strings are flag names or values, numbers are emitted as their numeric string. The list is passed to llama-server in order; flag ordering matters for some llama-server options.

Common categories:

- **Memory**: `--no-mmap`, `--mlock`, `--cache-ram`, `--kv-offload`
- **Performance**: `--threads`, `--threads-batch`, `--parallel`, `--batch-size`, `--ubatch-size`, `--flash-attn`
- **Context**: `--ctx-size`, `--ctx-checkpoints`, `--checkpoint-min-step`
- **Sampling**: `--top_p`, `--top_k`, `--repeat-penalty`
- **GPU**: `--n-gpu-layers` (999 = offload all layers to GPU)
- **Chat template**: `--jinja`, `--chat-template-file`, `--chat-template-kwargs`

The `--chat-template-file` path is resolved relative to the workspace directory (since llama-server runs on the host from the workspace), so `.pi-container/chat-templates/<model>/chat_template.jinja` is the typical pattern.

#### Server sharing and fingerprints

A llama-server process is a **host-wide shared resource**, keyed by provider name plus a stable fingerprint of its `serverCustomParameters`. Two projects with the same provider name and identical `serverCustomParameters` (model files, flags) share one llama-server process — saving RAM by avoiding double-loading a model. A same-named provider with different parameters gets its own server, ensuring a project never silently attaches to a llama-server running the wrong model.

This means:
- Identical `serverCustomParameters` across projects → one process (efficient).
- Divergent `serverCustomParameters` (even different flag values) → separate processes.
- Changing `serverCustomParameters` mid-session restarts the server.

#### Validation

`run.py` validates `models.json` at startup. Missing `hfModels` entries, empty dicts, or non-string required fields produce clear errors:

```
Models configuration invalid:
  providers.local-ornith.serverCustomParameters.hfModels: must not be null
  providers.local-ornith.serverCustomParameters.hfModels.main.repo: must not be null

Fix: update .pi-container/agent/models.json to match the expected schema.
```

It also checks that any `--chat-template-file` paths referenced in `flags` exist on disk, resolving `.pi-container/...` paths relative to the workspace.

#### Ready-made setups

Example configurations for popular models are shipped under `docs/setups/`:

| Setup | Model | Repo |
|-------|-------|------|
| [Qwen3.6-35B-A3B-UD-Q6_K_XL](setups/Qwen3.6-35B-A3B-UD-Q6_K_XL/) | Qwen3.6 35B-A3B (MTP + vision) | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` |
| [gemma-4-26b-a4b-it-qat-GGUF](setups/gemma-4-26b-a4b-it-qat-GGUF/) | Gemma 4 26B-A4B (MTP + vision) | `unsloth/gemma-4-26B-A4B-it-qat-GGUF` |
| [ornith-1.0-35b-Q6_K](setups/ornith-1.0-35b-Q6_K/) | Ornith 1.0 35B (vision) | `deepreinforce-ai/Ornith-1.0-35B-GGUF` |

Each setup directory contains a `models.json` you can drop into `.pi-container/agent/`, plus any required chat templates under `chat-templates/`. See each setup's `README.md` for notes and caveats.

### The `config.yaml` file

`.pi-container/config.yaml` is the single source of truth for this workspace's orchestration knobs:

```yaml
# .pi-container/config.yaml
resources:
  agent: { memory: 16g, cpus: 8 }
  proxy: { memory: 4g, cpus: 4 }
llama:
  startup_timeout: 180        # seconds to wait for /health per attempt
  startup_attempts: 2         # relaunches before giving up
network:
  ipv6: false                 # plumb IPv6 through the isolated net + proxy
  dns: "1.1.1.1"              # upstream resolver the proxy uses
proxy:
  expose_ui: localhost        # mitmweb UI bind: localhost | lan
agent:
  env: {}                     # extra --env vars for the agent container
  mounts: []                  # extra bind mounts (absolute host paths)
tmpfs:
  paths: []
flow_export:
  enabled: false
egress:
  allow: { ssh: false, smtp: false, git: false, ntp: false, tcp_ports: [], udp_ports: [] }
```

Any missing section falls back to a safe default (values above; egress → deny-all; flow_export → off). Each subsection is documented below.

### Resource limits

`resources.agent` and `resources.proxy` set CPU/memory caps on the two containers this workspace launches (`--memory` / `--cpus`). A `null` (or omitted) value drops the corresponding flag → **no limit** for that dimension. Defaults are `agent: 16g/8`, `proxy: 4g/4`.

### llama-server startup tuning

`llama.startup_timeout` (seconds) is how long to wait for each model's `/health` before treating the launch as failed; `llama.startup_attempts` is how many times to relaunch before giving up. Raise both for large models that are slow to load. Defaults: `180` / `2`.

### Network

`network.ipv6` toggles IPv6 for this project's isolated network + proxy (only works if the runtime **and** host route IPv6 — leave `false` on macOS; see [Network topology](architecture.md#network-topology)). `network.dns` is the upstream resolver the proxy uses for the agent's DNS lookups (default `1.1.1.1`) — set it to a corporate/internal resolver when needed.

### Proxy UI exposure

`proxy.expose_ui` controls where the proxy's mitmweb UI (on its auto-assigned port) is published:

- `localhost` (default) — bound to `127.0.0.1` only; not reachable from other machines.
- `lan` — bound to `0.0.0.0`; reachable across the network (still password-gated by `ADMIN_PASSWORD`).

### Extra agent env / mounts

`agent.env` (a map) adds environment variables to the agent container, and `agent.mounts` (a list of `host:container[:ro]` specs, absolute host paths) adds bind mounts — for one-off tools, caches, or credentials a project needs:

```yaml
agent:
  env:
    MY_API_BASE: https://internal.example.com
  mounts:
    - /Users/me/.cache/pip:/home/pi/.cache/pip:ro
```

### Dependency definition files

Project-specific setup is defined in two files under `.pi-container/dependencies/`. These are **baked into the project-specific image at build time**, not installed at runtime. This eliminates redundant `apt-get update` and `apt-get install` calls at container startup.

| File | Privilege | Runs | Purpose |
|------|-----------|------|---------|
| `.pi-container/dependencies/root/commands.sh` | root | **Build time** | Install system packages (`apt-get`), npm globals, system config — baked into image |
| `.pi-container/dependencies/pi/commands.sh` | pi | **Runtime** (via entrypoint) | Init venvs, clone repos, workspace setup — runs against bind-mounted workspace |

**Example `root/commands.sh`:**
```bash
#!/bin/bash
set -e
# Install system packages
apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libavcodec-extra

# Install npm globals
npm install -g typescript
```

**Example `pi/commands.sh`:**
```bash
#!/bin/bash
set -e
# Initialize Python venv
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Clone a repo
git clone https://github.com/example/repo.git
```

**How it works:**

1. On first run, pi-container seeds both files from templates in `pi-coding-agent/default/dependencies/`.
2. When definition files exist and are non-empty, pi-container computes a content hash and builds a project-specific image with the scripts baked in. The hash is stored as a label (`pi-container.hash`) on the image for cache invalidation.
3. At **build time**, `root/commands.sh` executes (system-wide setup: apt, npm globals). At **runtime**, `pi/commands.sh` executes via the entrypoint (workspace-local setup: venvs, cloned repos).
4. If definition files are empty or absent, the workspace uses the shared base image (no project-specific build).

**Why two execution times?**

- `root/commands.sh` runs at **build time** because it installs system-wide packages (apt, npm globals) that should be baked into the image. These persist across container runs.
- `pi/commands.sh` runs at **runtime** because it creates workspace-local artifacts (venvs, cloned repos) in the bind-mounted workspace. If these were created at build time, they would be hidden by the bind mount at runtime.

**Image caching:**

Project-specific images are cached and reused across runs. pi-container computes a content hash of:
- `.pi-container/dependencies/root/commands.sh` (if it exists and is non-empty)
- `.pi-container/dependencies/pi/commands.sh` (if it exists and is non-empty)
- `pi-coding-agent/Containerfile` (always)
- `pi-coding-agent/entrypoint.sh` (always)

The hash is stored as a label (`pi-container.hash`) on the image. On each run, pi-container reads this label and compares it to the current hash. If they match, the cached image is used (no rebuild). If they differ (or the label is missing), a new image is built.

This enables:
- **Cross-workspace sharing**: Two workspaces with identical definition files compute the same hash and share one image.
- **Automatic invalidation**: Editing a definition file, the Containerfile, or the entrypoint triggers a rebuild on the next run.
- **Migration**: Images built before this feature (no label) are treated as stale and rebuilt with the label.

**Note on workspace-local artifacts:** Venvs, cloned repos, and other workspace-local artifacts created by `pi/commands.sh` are NOT cached across container runs. The image cache only applies to system-wide setup from `root/commands.sh` and the image definition itself.

**Key principles:**

- The shared base image contains only packages essential to pi itself (see [Shared Base apt Packages](project-specific-containers.md#shared-base-apt-packages)).
- Any additional packages must be installed via `root/commands.sh`.
- Both files are optional — if absent, the workspace uses the shared image.
- Changes to definition files trigger a rebuild on the next `run.sh` invocation (detected via image label comparison).

### Allowlist

The `allowlist.yaml` config in the project's `.pi-container/` defines hostname rules for the [allowlist addon](proxy/allowlist.md) running on that project's mitmproxy transparent proxy. It is **per-project** — each workspace's proxy mounts its own allowlist (seeded from a generic pypi/npm/github/apt template on first run; edit it per project). Traffic from the agent container to non-allowlisted hosts is **blocked with HTTP 403**. If the file is missing entirely, the image's fail-closed default blocks all hosts.

Each rule has a `name`, `mode` (`allow`), a list of `hostnames` (supporting `*` wildcards), and optional `ip_ranges`. Traffic matching any rule is permitted; all other traffic is denied. The default mode is `allow` with a `block` default action.

Current default rules allow:
- **PyPI**: `pypi.org`, `files.pythonhosted.org`
- **npm**: `registry.npmjs.org`, `*.npmjs.org`
- **GitHub**: `github.com`, `api.github.com`, `codeload.github.com`, `objects.githubusercontent.com`, and related subdomains
- **Yarn**: `registry.yarnpkg.com`
- **Debian apt**: `deb.debian.org`, `security.debian.org`, `packages.debian.org`

Add new rules for any additional hostnames the agent needs to reach (e.g. internal APIs, private package registries).

### Token Replacer Secrets

The `token_replacer.yaml` config in `.pi-container/` may reference `${ENV:VAR}` values that must be set in the host environment before running. `run.py` scans this config and injects the values as environment variables into the proxy container. Override `ContainerNetworkManager._pull_secrets_from_config()` (in [`src/network.py`](https://github.com/mikkovihonen/pi-container/blob/main/src/network.py)) to integrate with a secret store (Vault, AWS Secrets Manager, etc.).

### Transient tmpfs Mounts

`config.yaml`'s `tmpfs.paths` defines paths mounted as **tmpfs** (volatile RAM disks) inside the pi container. Data written to these paths is **lost when the container stops** — useful for build artifacts, caches, and temp files that should not persist across runs.

```yaml
# .pi-container/config.yaml
tmpfs:
  paths:
    - /workspace/.venv
    - /workspace/.pytest_cache
    - /workspace/node_modules/.cache
```

Each path is mounted at the same absolute location inside the container. On podman/docker, mounts use the `notmpcopyup` flag so they start empty rather than copying the host's bind-mounted content into the tmpfs. Paths are deduplicated and sorted for deterministic output.

### Flow export

`config.yaml`'s `flow_export.enabled` toggles whether the proxy's captured HTTP/HTTPS flow history for this workspace is exported after the agent shuts down (defaults to disabled):

```yaml
# .pi-container/config.yaml
flow_export:
  enabled: true
```

When enabled, `run.py` reads the flows the proxy staged for this session and writes a merged snapshot bucketed by UTC date under `.pi-container/exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.jsonl`. When the section is absent or malformed, export is **off** (fail-safe). The export contains full request/response bodies and headers — see [Version control](#version-control-gitignore) for why `.pi-container/exports/` must never be committed.

### Egress policy

`config.yaml`'s `egress.allow` is the **per-project** proxy egress policy. Only HTTP/HTTPS/DNS are intercepted by mitmproxy; every other protocol is denied by default. Opt a protocol in here to let the agent use it — but note these are forwarded **uninspected** (plain NAT); mitmproxy and the allowlist do not see them.

```yaml
# .pi-container/config.yaml
egress:
  allow:
    ssh: false            # TCP 22 (e.g. git over SSH)
    smtp: false           # TCP 25, 465, 587
    git: false            # TCP 9418 (git://)
    ntp: false            # UDP 123
    tcp_ports: []         # arbitrary extra TCP ports, e.g. [2222, 8443]
    udp_ports: []         # arbitrary extra UDP ports, e.g. [51820]
```

`run.py` translates truthy flags and non-empty port lists into the proxy container's `PROXY_ALLOW_*` env vars, which its entrypoint uses to open the matching `iptables` FORWARD rules. An absent or malformed section means **deny-all** (fail-safe). See [Proxy egress policy](architecture.md#proxy-egress-policy) for the full protocol/port reference.

### Chat templates

Some models need an explicit Jinja chat template. Place them under `.pi-container/chat-templates/<model>/` and reference them from a model's `serverCustomParameters.flags` with a path **relative to the workspace**:

```json
"--chat-template-file", ".pi-container/chat-templates/Ornith-1.0-35B-FP8/chat_template.jinja"
```

`llama-server` runs on the host from the workspace directory, so the relative path resolves against `.pi-container/chat-templates/` in whichever project you launched `pi` from — the templates are seeded there on first run alongside the rest of the config. (Model *weights* are shared across projects under `llama-server/models/`; only the small chat-template files are per-project.)

### Version control (.gitignore)

A ready-to-copy [`.gitignore.example`](https://github.com/mikkovihonen/pi-container/blob/main/docs/assets/.gitignore.example) lists every entry a workspace needs. Copy the relevant lines into your project's `.gitignore`.

Most of `.pi-container/` is project configuration you **should commit** so the environment is reproducible: `config.yaml`, `allowlist.yaml`, `token_replacer.yaml`, `chat-templates/`, and `dependencies/` (root/commands.sh and pi/commands.sh — see [Dependency definition files](#dependency-definition-files)). (`token_replacer.yaml` holds only `${ENV:VAR}` references, never resolved secrets — see [Token Replacer Secrets](#token-replacer-secrets).)

The one directory you **must ignore** is the flow-export output:

```gitignore
# pi-container: proxy flow capture — sensitive and ephemeral, never commit
.pi-container/exports/
```

`.pi-container/exports/` holds the proxy's captured HTTP/HTTPS traffic — full request/response bodies and headers, including any `Authorization`/cookie values the [token_replacer](proxy/token-replacer.md) did not redact — as raw `flows-<ip>.jsonl` files and date-bucketed snapshots under `exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.jsonl`. Treat it as sensitive. It is also where run-time shadows an empty tmpfs (so the agent can't read prior captures), which can leave an empty `exports/` dir in a workspace even when no traffic was captured. This repo already ignores it; add the entry above to **your** project's `.gitignore` when you run pi-container inside it.

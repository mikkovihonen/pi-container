# Configuration


## Environment Configuration

The application uses a `.env` file to store environment-specific settings. See `.env.example` for all available options.

### Security

- **`ADMIN_PASSWORD`** MUST be changed from the default `CHANGEME` before running.
  The proxy's mitmweb UI will refuse to start with a default or empty password.
- **Model integrity**: Set `sha256` in `models.json` to verify downloaded model files.
  The download skips integrity checks without a checksum.

### Run Configuration

The following environment variables are used by `build.sh` and `run.sh` to configure the container runtime, proxy, and `llama-server`:

| Variable | Description | Default |
|----------|-------------|---------|
| `PI_IMAGE_TAG` | The tag of the pi container image to run | `pi-coding-agent:local` |
| `PROXY_IMAGE_TAG` | The tag of the proxy container image to run | `pi-coding-agent-proxy:local` |
| `LLAMA_BIN` | Path to the `llama-server` executable | `llama-server` or `/opt/homebrew/bin/llama-server` |
| `BRIDGE_INTERFACE` | Host bridge interface for container networking | `podman0` |
| `PROXY_UPSTREAM_NETWORK` | The upstream network the proxy connects to for internet access | `podman` |
| `LOG_LEVEL` | Log level | `INFO` |
| `ADMIN_PASSWORD` | Password for mitmproxy Web UI | `CHANGEME` |
| `BUILDER_IMAGE_TAG` | The tag of the toolchain image that the agent image copies node/python/podman from | `pi-coding-agent-builder:local` |
| `NODE_SOURCE` | `build` compiles Node from source in the toolchain image (~1 hour) instead of staging the official tarball | `prebuilt` |
| `PYTHON_OPTIMIZE` | `0` skips the PGO CPython build in the toolchain image: a fraction of the memory and time, ~10-20% slower Python | `1` (PGO on) |
| `PI_MEMORY_PREFLIGHT` | `0` skips the pre-build memory check (see below) | `1` (check on) |

`BRIDGE_INTERFACE` and `PROXY_UPSTREAM_NETWORK` have per-runtime defaults. They rarely need setting. Provide them only to override the default for your host.

`NODE_SOURCE` and `PYTHON_OPTIMIZE` are the two toolchain knobs that `build.sh` forwards as build args. The **component versions themselves** are not environment variables. Every version, git commit, and SHA-256 the toolchain image builds from is an `ARG` in [`pi-coding-agent-builder/Containerfile`](https://github.com/mikkovihonen/pi-container/blob/main/pi-coding-agent-builder/Containerfile). The declaration lives on the stage that uses it. Bumping podman, Node, CPython, Go, Rust, netavark, or any pip pin is an edit in that one file. Overriding one for a one-off build means calling podman directly. `build.sh` forwards only the two knobs above:

```bash
podman build --build-arg PODMAN_VERSION=v6.1.0 --build-arg PODMAN_COMMIT=<peeled-sha> \
    --tag pi-coding-agent-builder:local -f pi-coding-agent-builder/Containerfile .
```

A version and its hashes must move together. The build refuses to start if any pin is unset or empty (naming the `ARG`). A mismatched hash fails the download instead of building something unverified.

> **Docker is not supported.** pi-container's isolation model depends on the agent container running inside a user namespace where container uid 0 maps to an unprivileged host user. Stock Docker provides no such remap. The same configuration would give two materially different security guarantees. See [Nested containers](#nested-containers).

> Per-project settings (IPv6, proxy DNS, mitmweb UI exposure, llama-server startup tuning, resource limits, tmpfs, flow export, egress, nested containers, extra agent env or mounts) are **not** environment variables. They live in `.pi-container/config.yaml`, documented below.

## Per-workspace Configuration

### Introduction

When you launch pi-container, it looks for workspace-specific overrides in `./.pi-container` and package dependencies in the directory you launched it from. Each workspace gets its own agent config, proxy, isolated network, and chat templates. They all live under that workspace's `.pi-container/` (seeded from the `pi-coding-agent/default/` template on first run).

Orchestration settings live in a single **`config.yaml`**. The proxy addon configs (`allowlist.yaml`, `token_replacer.yaml`) stay in their own files. The proxy container mounts and parses them.

### The `models.json` file and `serverCustomParameters`

You configure the LLM models that pi-container serves in `.pi-container/agent/models.json`. This file uses pi-coding-agent's own `models.json` format with an extended `serverCustomParameters` block per provider. This block bridges the pi-container orchestration layer and llama-server. It tells pi-container which model files to download, where they live on disk, and which `llama-server` command-line flags to pass when launching the model.

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

pi-container reads only a subset of pi-coding-agent's `models.json` fields. pi-coding-agent handles most model metadata (IDs, context windows, tool-calling flags). llama-server does not need them. At startup, `run.py` iterates `providers`. For each entry that has `serverCustomParameters` it extracts:

| Field | Used by pi-container? | Notes |
|-------|----------------------|-------|
| `providers.<name>` (key) | **Yes** — as `server_id` | Becomes the llama-server `--alias` and the sharing key. Must be unique per provider. |
| `baseUrl` | **Yes** | Required for local providers (`serverCustomParameters`). Must start with `http://` or `https://` and include an explicit container port. Hostnames (`llama`, the provider name, or custom hostnames) are automatically resolved to the proxy. Ports must be unique across local providers (e.g. `9999`, `9998`). Note: `localhost`/`127.0.0.1` cannot be used because it resolves to the container's own loopback. |
| `serverCustomParameters.hfModels` | **Yes** | Model file download config + per-model additional flags. |
| `serverCustomParameters.flags` | **Yes** | llama-server CLI flags, passed verbatim. |
| `api`, `apiKey` | No | pi-coding-agent uses these for API negotiation. pi-container ignores them. |
| `compat` | No | pi-coding-agent compatibility flags. pi-container ignores them. |
| `models[].id`, `models[].name`, `models[].contextWindow`, `models[].toolCalling`, `models[].vision`, `models[].reasoning`, `models[].options`, etc. | No | pi-coding-agent model metadata. pi-container does not read these. |

In short, pi-container only needs `baseUrl` (for the port and hostname routing), the provider name, and `serverCustomParameters`. The rest of the `models.json` structure is for pi-coding-agent's model registry. pi-container passes it through untouched.

#### Multiple providers

You can configure multiple local providers (or a mix of local and cloud providers) in `models.json`. When multiple local providers are configured:
- Each provider with `serverCustomParameters` launches its own `llama-server` process on the host.
- Each local provider's `baseUrl` must use a **unique container port** (for example, `http://llama:9999/v1` for the first provider, `http://llama:9998/v1` or `http://local-gemma:9998/v1` for the second) so the transparent proxy can forward traffic to the appropriate server.
- The proxy automatically resolves `llama`, each provider name, and custom hostnames in `baseUrl` to the proxy's internal address.

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

You must set each `hfModels` entry:

| Field | Description |
|-------|-------------|
| `fileFlag` | The llama-server flag name (e.g. `--model`, `--model-draft`, `--mmproj`). The system emits this flag with the model's resolved path when launching llama-server. |
| `repo` | Hugging Face repository slug (e.g. `unsloth/gemma-4-26B-A4B-it-qat-GGUF`). |
| `file` | Filename within the repository (e.g. `ornith-1.0-35b-Q6_K.gguf`). |
| `dir` | Subdirectory under `llama-server/models/` where the system caches the file. Files from different repos use different dirs to avoid collisions. |
| `additionalServerFlags` | Extra flags appended after this model's `fileFlag` and path on the llama-server command line. Use this for per-model options like speculative decoding settings (`--spec-type`, `--spec-draft-n-max`). |
| `sha256` | *(optional)* SHA-256 hex digest of the model file. If you set it, pi-container verifies the downloaded file before starting llama-server. A mismatch aborts startup. Without a checksum, downloads proceed without integrity verification. |

The system processes `hfModels` entries in sorted label order. Each entry's `additionalServerFlags` keep their specified order. The system passes the overall `flags` list to llama-server verbatim and in the order defined.

Multiple labels are common. Use `main` for the base model, `draft` for a speculative decoding draft model, `mmproj` for a multi-modal projection head, or additional labels for LoRA adapters and other llama-server features. A provider **must** have at least one entry (the "main" model). At minimum the `main` label must point to the primary model file.

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

Each item is a single CLI token. Strings are flag names or values. The system emits numbers as their numeric string. The system passes the list to llama-server in order. Flag ordering matters for some llama-server options.

Common categories:

- **Memory**: `--no-mmap`, `--mlock`, `--cache-ram`, `--kv-offload`
- **Performance**: `--threads`, `--threads-batch`, `--parallel`, `--batch-size`, `--ubatch-size`, `--flash-attn`
- **Context**: `--ctx-size`, `--ctx-checkpoints`, `--checkpoint-min-step`
- **Sampling**: `--top_p`, `--top_k`, `--repeat-penalty`
- **GPU**: `--n-gpu-layers` (999 = offload all layers to GPU)
- **Chat template**: `--jinja`, `--chat-template-file`, `--chat-template-kwargs`

The `--chat-template-file` path resolves relative to the workspace directory. llama-server runs on the host from the workspace. So `.pi-container/chat-templates/<model>/chat_template.jinja` is the typical pattern.

#### Server sharing and fingerprints

A llama-server process is a **host-wide shared resource**. It uses the provider name plus a stable fingerprint of its `serverCustomParameters` as its key. Two projects with the same provider name and identical `serverCustomParameters` (model files, flags) share one llama-server process. This saves RAM by avoiding double-loading a model. A same-named provider with different parameters gets its own server. This ensures a project never silently attaches to a llama-server running the wrong model.

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

> **A repeated key is rejected, not merged.** YAML keeps only the **last** occurrence of a duplicated mapping key and discards the earlier value. The easy way to hit this is adding an entry above a seeded empty default instead of replacing it:
>
> ```yaml
> ports:
>   publish:
>     - "18080:8080"
>   publish: []          # ← wins; the entry above is silently thrown away
> ```
>
> `run.sh` parses `config.yaml`, `allowlist.yaml` and `token_replacer.yaml` strictly and aborts with the file and line number if any of them repeats a key. Without that check the file parses clean, passes schema validation, and the setting simply never takes effect.

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
  max_view_flows: 2000        # max allowed flows in mitmweb RAM (denied flows always kept)
  stream_large_bodies: 10m    # stream bodies > threshold directly (e.g. 10m, off)
agent:
  env: {}                     # extra --env vars for the agent container
  mounts: []                  # extra bind mounts (absolute host paths)
  capabilities: []            # Linux capabilities to add (e.g., SYS_PTRACE)
  devices: []                 # device passthroughs (e.g., /dev/video0:/dev/video0)
  read_only_pi_container: true # lock .pi-container inside /workspace as read-only
nested_containers:
  enabled: false              # let the agent run its own containers (off by default)
  storage: volume             # nested image store: volume | tmpfs
  security: disable           # agent SELinux label: disable | engine_t
  ports:
    expose: localhost         # host bind for published ports: localhost | lan
    publish: []               # host ports for nested-container UIs, e.g. [3000]
tmpfs:
  paths: []                   # volatile RAM mounts under /workspace
volumes:
  paths: []                   # persistent named volume mounts (shadowing host paths)
flow_export:
  enabled: false
egress:
  allow: { ssh: false, smtp: false, git: false, ntp: false, tcp_ports: [], udp_ports: [] }
```

Any missing section falls back to a safe default (values above). The system treats egress as deny-all. It treats flow_export as off. Each subsection is documented below.

### Resource limits

`resources.agent` and `resources.proxy` set CPU and memory caps on the two containers this workspace launches (`--memory` / `--cpus`). A `null` (or omitted) value drops the corresponding flag. The dimension has **no limit**. Defaults are `agent: 16g/8`, `proxy: 4g/4`.

### llama-server startup tuning

`llama.startup_timeout` (seconds) sets how long to wait for each model's `/health`. The launch fails after this wait. `llama.startup_attempts` is how many times to relaunch before giving up. Raise both for large models that load slowly. Defaults: `180` / `2`.

### Network

`network.ipv6` toggles IPv6 for this project's isolated network and proxy (only works if the runtime **and** host route IPv6). Leave `false` on macOS (see [Network topology](architecture.md#network-topology)). `network.dns` is the upstream resolver the proxy uses for the agent's DNS lookups (default `1.1.1.1`). Set it to a corporate or internal resolver when needed.

### Proxy configuration

The `proxy` section tunes the `pi-coding-agent-proxy` container:

- `proxy.expose_ui` controls where the proxy's mitmweb UI (on its auto-assigned port) is published:
    - `localhost` (default) — bound to `127.0.0.1` only. Not reachable from other machines.
    - `lan` — bound to `0.0.0.0`. Reachable across the network (still password-gated by `ADMIN_PASSWORD`).
- `proxy.max_view_flows` (integer, default `2000`) sets the maximum number of allowed flows retained in `mitmweb`'s in-memory view to prevent container memory exhaustion and OOM crashes during long sessions. **Blocked/denied flows are always retained** so rejections remain inspectable. Set to `null` or `0` for unlimited in-memory retention.
- `proxy.stream_large_bodies` (string, default `"10m"`) enables direct body streaming for transfers larger than this threshold, bypassing full RAM buffering in mitmproxy. Set to `null`, `"none"`, or `"off"` to disable.

### Extra agent env / mounts

`agent.env` (a map) adds environment variables to the agent container. `agent.mounts` (a list of `host:container[:ro]` specs, absolute host paths) adds bind mounts for one-off tools, caches, or credentials a project needs:

```yaml
agent:
  env:
    MY_API_BASE: https://internal.example.com
  mounts:
    - /Users/me/.cache/pip:/home/pi/.cache/pip:ro
```

### Capabilities

`agent.capabilities` (a list of capability names) adds Linux capabilities to the agent container via `--cap-add`. Useful when the agent needs elevated privileges for specific operations:

```yaml
agent:
  capabilities:
    - SYS_PTRACE      # debug processes
    - SYS_ADMIN       # mount filesystems, some kernel operations
    - DAC_OVERRIDE    # bypass file permission checks
    - NET_RAW         # use raw and packet sockets
```

Common capabilities:

| Capability | Description |
|------------|-------------|
| `SYS_PTRACE` | Trace processes (debuggers, profilers) |
| `SYS_ADMIN` | Mount filesystems, some kernel operations |
| `DAC_OVERRIDE` | Bypass file read/write/execute permission checks |
| `NET_RAW` | Use raw and packet sockets |
| `MKNOD` | Create special files |
| `SYS_RESOURCE` | Override resource limits |
| `SYS_NICE` | Set process nice value, set CPU affinity |

See `man 7 capabilities` for the full list.

### Device passthroughs

`agent.devices` (a list of `host:container[:mode]` specs) passes host devices into the agent container via `--device`. Useful for GPU access, USB devices, serial ports, etc:

```yaml
agent:
  devices:
    - /dev/video0:/dev/video0              # Camera
    - /dev/bus/usb:/dev/bus/usb            # USB devices
    - /dev/nvidia0:/dev/nvidia0            # NVIDIA GPU
    - /dev/ttyUSB0:/dev/ttyUSB0            # Serial port
```

Device entries support the optional `mode` suffix (`r` for read-only, `w` for write-only, `rw` for read-write). The default is `rw` if omitted.

### Read-only `.pi-container` workspace lock

`agent.read_only_pi_container` (boolean, default `true`) mounts `/workspace/.pi-container` as a read-only (`:ro`) bind mount inside the agent container.

This provides protection against prompt injection and untrusted scripts by preventing the agent from modifying:
- `config.yaml` (resource limits, network settings, security policies)
- `allowlist.yaml` (punching unauthorized egress holes)
- `token_replacer.yaml` (disabling secret redaction rules)
- `dependencies/root/commands.sh` and `dependencies/pi/commands.sh` (poisoning future container builds)
- `chat-templates/` and `agent/models.json`

```yaml
agent:
  read_only_pi_container: true   # default: true (secure)
```

Subdirectories mounted under separate container paths (such as `/home/pi/.pi/agent` for storing session logs and transcripts) remain fully functional and writable. Set to `false` only if you explicitly want the agent to edit project configuration files directly.

### Transient `tmpfs` mounts

`tmpfs.paths` mounts volatile RAM disks over specific paths under `/workspace`. Data written to these paths lives purely in RAM and is completely wiped when the container stops. Useful for ephemeral directories, temporary scratch spaces, or build locks:

```yaml
tmpfs:
  paths:
    - /workspace/.pytest_cache
    - /workspace/__pycache__
```

### Named shadow volumes

`volumes.paths` mounts project-scoped named persistent volumes over subdirectories of `/workspace` (e.g. package stores or build caches).

Unlike `tmpfs`, named shadow volumes persist across container runs in the container runtime's volume store, keeping build caches warm while isolating them from the host filesystem:

```yaml
volumes:
  paths:
    - /workspace/node_modules
    - /workspace/.venv
    - /workspace/target
```

### Security hardening

The `security` section in `config.yaml` provides defense-in-depth safeguards against malicious code execution, credential exfiltration, and host infiltration:

```yaml
security:
  # Automatically lock .git/hooks inside /workspace as read-only.
  # Prevents untrusted agent code from planting malicious git hooks that execute
  # on the host machine when the developer runs git commands outside the container.
  read_only_git_hooks: true

  # Dangerous host directories that should NEVER be mounted into the agent container.
  # If any of these paths (or their subdirectories) appear in agent.mounts,
  # pi-container will reject launching the container to prevent host credential theft.
  blocked_mount_paths:
    - "~/.ssh"
    - "~/.gnupg"
    - "~/.aws"
    - "~/.azure"
    - "~/.config/gcloud"
    - "~/.kube"
    - "~/.docker"
    - "/var/run/docker.sock"
    - "/var/run/podman/podman.sock"
    - "/etc/shadow"
    - "/etc/passwd"
    - "/etc/sudoers"
    - "/root"

  # Private network CIDRs blocked from egress to protect internal infrastructure,
  # local router web UIs, and cloud metadata services (e.g. AWS 169.254.169.254)
  # against SSRF and DNS rebinding attacks.
  blocked_ip_ranges:
    - "127.0.0.0/8"          # IPv4 loopback
    - "10.0.0.0/8"           # Private RFC 1918 Class A
    - "172.16.0.0/12"        # Private RFC 1918 Class B
    - "192.168.0.0/16"       # Private RFC 1918 Class C
    - "169.254.0.0/16"       # IPv4 link-local / Cloud metadata (IMDS)
    - "::1/128"              # IPv6 loopback
    - "fc00::/7"             # IPv6 unique local address (ULA)
    - "fe80::/10"            # IPv6 link-local

  # Allowlist of safe Git configuration keys forwarded from the host into the container.
  # All other host git configuration keys (including execution hooks, filters, and http.extraHeader)
  # are stripped to prevent host environment leaks or arbitrary code execution.
  git_config_allowlist:
    - "user.name"
    - "user.email"
    - "init.defaultBranch"
    - "pull.rebase"
    - "core.autocrlf"
    - "core.filemode"
    - "core.eol"
    - "commit.gpgSign"
    - "user.signingKey"
    - "gpg.format"
    - "gpg.ssh.program"
    - "tag.gpgSign"
```

Key security protections:
- **Read-Only Git Hooks**: When `read_only_git_hooks: true` (default), `/workspace/.git/hooks` is mounted read-only, preventing the agent from modifying or writing malicious Git hooks that would execute on your host machine.
- **Dangerous Mount Guardrails**: Any attempt to mount sensitive host directories (SSH keys, AWS/cloud credentials, container sockets) in `agent.mounts` is blocked at startup.
- **SSRF & Metadata Protection**: Blocked IP ranges prevent the container from connecting to private subnets, localhost ports on other interfaces, and cloud instance metadata services (`169.254.169.254`) unless explicitly allowlisted. Local model endpoints (`llama`) and container network interfaces remain securely reachable.
- **Git Config Sanitization**: Only keys explicitly listed in `git_config_allowlist` are passed from the host Git configuration into the container. Dangerous keys like `core.sshCommand`, `core.fsmonitor`, `core.editor`, `filter.*`, and `http.extraHeader` are unconditionally stripped.
- **Privilege Escalation Prevention**: Containers run with `--security-opt no-new-privileges` (when nested containers are disabled), and shadow volumes and `tmpfs` mounts are mounted with `nodev,nosuid`.

<a name="upgrading-a-workspace"></a>
### Upgrading an existing workspace

`.pi-container/` is seeded on first run and then **never edited again**. The seeder only writes files that are absent, so your changes are never overwritten. The cost is that a workspace does not pick up new config fields on its own. When a release adds one, the next launch stops with:

```
Configuration incompatible with this version of pi-container:
  nested_containers.ports: required field missing
```

Two failures produce that banner, and they have different fixes:

| What the error says | Fix |
|---|---|
| **Only** `schema_version mismatch` | Edit `schema_version` in `.pi-container/config.yaml` to the version named. The shape already validates, so nothing else changes and your settings are kept. |
| Anything naming a **field** | Re-seed that one file: `rm .pi-container/config.yaml` and re-run. |

Bumping the version string is not a general remedy. When a field is missing, no edit to `schema_version` conjures it — you clear the version check and fail the field check one line further down. The launch error names whichever applies rather than offering both.

Re-seeding is **one file, not the directory**. Because the seeder only fills in what is missing, deleting `config.yaml` alone regenerates it from the current template and leaves `allowlist.yaml`, `token_replacer.yaml`, `agent/models.json`, `chat-templates/` and `dependencies/` untouched. `rm -rf .pi-container` also works but takes every hand-edited file in the workspace with it — reach for it only when several are out of date at once.

Your own edits to `config.yaml` are not merged into the fresh copy, so note your settings before deleting it. A `diff` against `pi-coding-agent/default/config.yaml` beforehand shows exactly what you changed.

<a name="pi-container-environment"></a>
### The `PI_CONTAINER_*` environment contract

A project's own scripts often need to behave differently inside the agent. They may pick a different compose overlay, skip a test that wants host networking, or point a tool at an address that only exists here. The agent injects a small, stable set of variables for exactly that. Nothing has to probe for it. Every variable pi-container sets shares the `PI_CONTAINER` prefix.

| Variable | Value | Set by |
|----------|-------|--------|
| `PI_CONTAINER` | `1`, always | The image. Present for every process (login shells, `podman exec`, anything the entrypoint never touches). |
| `PI_CONTAINER_VERSION` | The running pi-container version, e.g. `0.4.2` | `run.py`, from the validated `schema_version`. Absent if the workspace has none. |
| `PI_CONTAINER_HOST_IP` | `169.254.1.2` | The image. Where the **agent** is reachable from inside a nested container (see [Nested containers](#nested-containers)). |
| `PI_CONTAINER_NESTED` | `true` | `run.py`, only when `nested_containers.enabled` is true. |

Test the marker, not a side effect:

```make
# A project Makefile choosing a compose overlay
ifdef PI_CONTAINER
  COMPOSE_FILES := -f docker-compose.yml -f docker-compose.pi-container.yml
endif
```

```bash
# A shell script
if [ -n "$PI_CONTAINER" ]; then …; fi
```

The system deliberately makes `PI_CONTAINER` **not** a general "am I in a container" test. `/run/.containerenv` and `/.dockerenv` answer that. They are true inside nested containers too. `PI_CONTAINER` means *this* container: the agent, with its isolated network, its proxy-only egress, and the workspace bind-mounted at `/workspace`.

Two properties worth relying on. The variables are set at the image and `run` layers. They survive `podman exec` and any shell the agent spawns. They do **not** propagate into nested containers. A container the agent starts gets a clean environment. This makes `PI_CONTAINER` mean "I am the agent" rather than "I am somewhere under it".

<a name="nested-containers"></a>
### Nested containers

`nested_containers` lets the agent run its own containers **inside** the agent container as rootless podman. It supports `podman build`, `podman run`, `docker compose up`, and testcontainers-style integration tests. It is **off by default**. Enabling it is a genuine (small, bounded) reduction in the agent container's confinement.

```yaml
# .pi-container/config.yaml
nested_containers:
  enabled: false
  storage: volume     # volume | tmpfs
  security: disable   # disable | engine_t
  ports:
    expose: localhost # localhost | lan
    publish: []       # e.g. [3000, 5173, "18080:8080"]
```

| Key | Values | Meaning |
|-----|--------|---------|
| `enabled` | `false` (default) / `true` | When false, no extra run flags, no volume, and no entrypoint work. The only cost is the toolchain's ~150 MB in the image. |
| `storage` | `volume` (default) / `tmpfs` | Where nested image layers live. `volume` is a persistent per-project named volume (`pi-nested-<project-hash>`) that survives runs. This avoids re-pulling base images every session. `tmpfs` is a volatile RAM disk. It needs a large `podman machine` and re-pulls each run. |
| `security` | `disable` (default) / `engine_t` | The agent container's SELinux label (see below). |
| `ports.publish` | `[]` (default) | Host ports for UIs served by nested containers (see below). |
| `ports.expose` | `localhost` (default) / `lan` | Whether those ports bind `127.0.0.1` only, or every interface. |

**Egress is still intercepted.** Containers the agent starts are *children* in its mount and network namespaces. Rootless podman NATs their traffic into the agent's own network stack, subject to the agent's routes. The agent's only route out is the proxy. The proxy's `FORWARD` policy is `DROP`. Verified: with the agent on the `--internal` isolated network, a nested container reaching for a raw IP gets `Network unreachable` from the kernel. Nesting is **not** a bypass. The proxy needs no changes.

Two honest consequences:

- **Flow attribution coarsens.** `flow_export` partitions captured flows by client IP. Nested-container traffic carries the agent's IP. Nested flows are indistinguishable from the agent's own.
- **Per-inner-container resource limits are best-effort.** Rootless podman in a container has no delegated cgroup subtree. `podman run --memory=…` *inside* the agent does not enforce. `resources.agent` still bounds the whole tree in aggregate. A nested workload cannot exceed the agent's budget.

**What `enabled: true` actually changes.** The agent container gains, *only* while nesting is enabled:

| Flag | Why it is required |
|------|--------------------|
| `--device /dev/fuse` | `fuse-overlayfs` fallback when native rootless overlay is unavailable. |
| `--device /dev/net/tun` | `pasta` creates the inner tap device. Grants no egress — the only route off the isolated network is still the proxy. |
| `--security-opt label=disable` | `/dev/net/tun` is inaccessible under the default SELinux label (see below). |
| `--security-opt unmask=ALL` | Podman bind-mounts parts of `/proc` read-only and masks others in the agent container. Those mounts are **locked** in the nested user namespace. Without this the inner runtime fails with `crun: open /proc/sys/net/ipv4/ping_group_range: Read-only file system`. |
| `--cap-add SYS_ADMIN` | Podman's default seccomp profile permits `mount`/`sethostname`/`umount2`/`pivot_root` **only** when `CAP_SYS_ADMIN` is in the container's capability set, and a seccomp filter cannot be relaxed from inside a nested user namespace. Without it the inner runtime fails with `crun: sethostname: Operation not permitted`. |

`CAP_SYS_ADMIN` sounds alarming, so it deserves the plain reading. Inside a user namespace it is **namespaced**. The agent container's userns maps container uid 0 to an unprivileged host user. The capability confers power over what that namespace owns, not over the host, the host kernel's global state, or other containers. It is nonetheless the broadest capability there is. It is the main reason nesting is opt-in.

Still **absent** with nesting on: `--privileged`, `seccomp=unconfined` (the filter stays active for everything the profile does not gate on `CAP_SYS_ADMIN`), `--userns` overrides, and any container-runtime socket. Mounting the host runtime socket (`/var/run/docker.sock`) is **never** offered. That socket is a full-privilege API to the host runtime. It would break the isolation model, not weaken it.

Egress interception is unaffected by any of this. Verified with the final flag set: with the agent attached only to the `--internal` isolated network, an inner container gets `Network unreachable` from the kernel for a raw IP. No HTTPS connection succeeds.

**SELinux posture.** On an SELinux-enforcing host, `/dev/net/tun` is inaccessible under the default `container_t` label, so one of two labels is required:

| `security` | Agent flag | Trade-off |
|-----------|-----------|-----------|
| `disable` (default) | `--security-opt label=disable` | Nested containers need no special flags — `docker compose`, testcontainers and anything driving the API socket work unmodified. The agent loses SELinux *type* confinement, but keeps the user namespace (container uid 0 is still an unprivileged host user), rootlessness, seccomp, the capability bounding set, the routing dead end, and the absence of any host socket. |
| `engine_t` | `--security-opt label=type:container_engine_t` | The agent stays SELinux-confined, but **every** nested container must then be run with `--security-opt unmask=all` or it fails in `crun` (`mount tmpfs to proc/acpi: Permission denied`). `unmask` is not a `containers.conf` key, so this cannot be made transparent. |

**Reaching a nested container's UI from the host.** A nested container's own `-p 3000:3000` publishes into the **agent's** network namespace, not the host's — a browser on the host has nothing to connect to, because the agent container is the outermost thing the host can see and it publishes no ports of its own. `ports.publish` closes that last hop by having the agent container republish the port:

```yaml
nested_containers:
  enabled: true
  ports:
    expose: localhost
    publish:
      - 3000            # host 3000 → agent 3000
      - 5173
      - "18080:8080"    # host 18080 → agent 8080
```

Two things then have to line up. pi-container only builds the outer hop:

1. **The nested container must publish the agent-side port itself** — `podman run -p 3000:3000 …`, or a compose `ports:` entry. Listing `3000` here does not reach into a nested container that only listens on its own internal port.
2. **The service must listen on all interfaces inside the nested container**, not `127.0.0.1`. A dev server bound to loopback is unreachable from outside its own container, whatever is published in front of it. (Vite: `--host`. `next dev`: `-H 0.0.0.0`.)

Ports must be **declared in config** rather than discovered. You fix a container's published ports at start. pi-container cannot add one to a running agent. Changing this list takes effect on the next `pi` launch. If a host port is already taken the launch **fails immediately**. It names the port instead of letting podman reject it after the images are built and the proxy is up. Use the `"HOSTPORT:AGENTPORT"` form to move a collision out of the way without changing what the nested container publishes.

This is **inbound only and creates no egress.** The agent's only route out is still the proxy. A published port does not change that (verified: a nested container on the isolated network still cannot reach HTTPS or a raw IP). What it does change is that something outside can now reach a service the agent controls. `expose: localhost` (the default) keeps that to processes on your own machine. `expose: lan` binds `0.0.0.0`. That makes an unauthenticated dev server reachable by anything that can route to your machine. The mitmweb UI is at least password-gated. This is not. Prefer an SSH tunnel to opening it to the network.

Under the hood, this is also why the image sets `netns = "bridge"` in its `containers.conf` drop-in (a documented `containers.conf` key that takes the same values as `--network`). This overrides podman 6's rootless default of `pasta`. Measured with the agent on the isolated network and the same outer `-p` in both cases: under `pasta` the forward reaches the agent's netns and the TCP handshake even completes, then stalls. The connection arrives carrying the agent's own address as its source. This is also the address pasta hands the nested guest, so the flow never resolves. Giving the guest the host's address is pasta's deliberate way of avoiding NAT. This is a consequence of its design rather than a defect. Under `bridge`, `rootlessport` binds a real socket in the agent's netns. The agent's `-p` publishes it. The host reaches the UI. Bridge is also what `docker compose` already got, since it creates a user-defined network per project. A plain `podman run -p` now behaves like the compose path instead of differently from it.

**`host.docker.internal` does not reach the agent from inside a nested container.** This is the mirror image of the outbound hop above. It bites any project that runs a sidecar on the agent (an exporter, a mock API, a language server) and has a nested container talk to it. Inside a nested container, both `host.containers.internal` and `host.docker.internal` resolve to the podman machine's gvproxy address (`192.168.127.254` on macOS). That is **your Mac, not the agent**. The service isn't there. The agent's isolated network has no route to it either. The connection times out instead of being refused. This reads like a slow service instead of a wrong address.

The address that does work is **`169.254.1.2`**. That is not a value pi-container picks. podman assigns it when it builds the rootless network namespace. You can see it in the `pasta` command line inside the agent:

```
/usr/bin/pasta --config-net … --no-map-gw --map-guest-addr 169.254.1.2
```

Measured from inside a nested container, against a listener on `0.0.0.0:9100` in the agent:

| target | result |
|--------|--------|
| `192.168.127.254` (`host.docker.internal`) | times out (gvproxy, i.e. the host, unreachable from the isolated network) |
| `10.89.1.1` (nested bridge gateway) | connection refused (`--no-map-gw`) |
| `10.89.0.2` (agent's own bridge address) | connection refused (wrong netns) |
| **`169.254.1.2`** | **serves** |

**Configure the address, not the name.** The obvious fix (`--add-host host.docker.internal:169.254.1.2`, or a compose `extra_hosts:` entry) **does not work**. It fails in a way that looks like it worked. Podman keeps its own entry for that name. `/etc/hosts` ends up listing it twice:

```
169.254.1.2      host.docker.internal          ← from --add-host
192.168.127.254  host.containers.internal host.docker.internal
```

busybox `wget` takes the first match and succeeds. A quick check from inside the container passes. Go's resolver sorts multi-address results by RFC 6724 destination-address selection. It ranks a global-scope address above a link-local one. A Go client dials `192.168.127.254` first. A short timeout expires before any fallback. Prometheus stayed `down` with `context deadline exceeded` until the podman line was deleted from `/etc/hosts` by hand. It went `up` on the next scrape. Anything else with RFC 6724-aware resolution (Go, glibc `getaddrinfo`) behaves the same way.

Podman will not omit its own entry. `--no-hosts` suppresses it but is mutually exclusive with `--add-host`. Put the literal address wherever the target is configured. For a compose stack, that fits in a **named overlay**. It is a tracked file that only applies when you ask for it. An explicit `-f` list replaces compose's auto-discovery:

```yaml
# docker-compose.pi-container.yml — tracked, and inert unless named
services:
  prometheus:
    volumes:
      # Replaces rather than adds: compose merges volumes keyed on the
      # container-side target path, so the committed mount is superseded
      # while named volumes survive.
      - ./config/prometheus.pi-container.yml:/etc/prometheus/prometheus.yml:ro

  caddy:
    # Ports do NOT merge that way — a second entry is appended, giving two
    # publishes of the same port. `!override` replaces the list instead.
    ports: !override
      - "8080:8080"
```

That asymmetry is worth knowing before writing an overlay. **volumes merge by target and supersede**. **ports append.** Measured with podman-compose 1.6.0: a base `127.0.0.1:8080:8080` plus an overlay `8080:8080` yields both. podman then rejects the duplicate publish. `!override` is the Compose-spec tag for replacing a list outright.

The same overlay is where a loopback bind gets relaxed. A service bound to `127.0.0.1` inside a nested container is unreachable from the host whatever the agent publishes (see the two conditions above). It has to bind `0.0.0.0`, but only inside the agent. Doing that in the committed compose file would widen every other operator's exposure to fix an environment they are not in. Doing it in the overlay confines it to the agent. Inside the agent, `0.0.0.0` is bounded by the isolated network and the real control is `nested_containers.ports.expose`.

```
compose -f docker-compose.yml -f docker-compose.pi-container.yml up -d
```

The name matters. `docker-compose.override.yml` is loaded automatically. An agent-only address placed there reaches every operator. You must gitignore it to keep it out of their way. Any other filename is inert until you pass it with `-f`. This is what makes the fix safe to commit and review rather than something each operator rediscovers.

Selecting it is what [`PI_CONTAINER`](#pi-container-environment) is for — no probing, and no way to forget:

```make
ifdef PI_CONTAINER
  COMPOSE_FILES := -f docker-compose.yml -f docker-compose.pi-container.yml
endif
```

The address itself is available as `PI_CONTAINER_HOST_IP`. A generated config or a tool invocation can substitute it instead of hardcoding. Prometheus is the awkward case. Its config file has no environment substitution. The literal has to be written down somewhere. That makes the caveat worth repeating: `169.254.1.2` is **coupled to podman's rootless-netns layout**, not to a documented interface. Treat it as a version-pinned constant that a podman upgrade in the agent image can invalidate. Its failure mode is a silently empty dashboard.

This is **not** fixable in the image's `containers.conf` drop-in, which is where the `netns` default above lives. `host_containers_internal_ip` is ignored there, under `[containers]` and under `[network]` alike (verified with an override file proven to be read: a marker `env` entry in the same file reached the container while the `/etc/hosts` line stayed unchanged). The agent has `/run/.containerenv`. The nested podman detects that it is itself inside a container. It propagates the agent's own `host.containers.internal` entry. That entry outranks the config key. Hence a per-project fix rather than a transparent default.

**Registries must be allowlisted.** The proxy's allowlist is `default_action: block`. Image pulls return 403 until you permit a registry. `allowlist.yaml` ships a commented-out `container-registries-allow` rule (Docker Hub, GHCR, Quay, GCR). Uncomment it, or add the registries you use. `run.py` logs a warning at startup when nesting is enabled and no registry hostname is allowed.

**TLS inside nested containers.** The image ships `/etc/containers/containers.conf.d/50-pi-container.conf`. It mounts the agent's full CA bundle (system CAs *plus* the mitmproxy CA) into every nested container. It points `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `GIT_SSL_CAINFO` at it. That covers tools honouring those variables (OpenSSL, Go, Node, Python-requests, curl, git). A tool that only reads a hardcoded path inside its own image still fails. The fix is per-image (`COPY` the CA in a build stage).

**Machine sizing.** A podman machine of **at least 4 GB is required** to build the agent image at all (see [Getting Started](getting-started.md#prerequisites)). Nesting is the workload most likely to expose anything smaller than that. The default `resources.agent.memory` is `16g`, which a 4 GB VM cannot honour. Raise the machine (`podman machine set --memory 8192` or more) before enabling nesting. Do this especially before choosing `storage: tmpfs`, where the first sizeable image pull lands in RAM.

The same limit bites at **image build** time. The [toolchain image](architecture.md#toolchain-builder-image) compiles CPython, podman, and netavark (and Node too, with `NODE_SOURCE=build`). One PGO compile job peaks near 900 MiB. Anything already running in the VM (other containers, a live `pi` session) eats into the total. This is why 2 GiB is not enough and 4 GB is the documented minimum. `build.sh` checks available memory in the builder **before starting the build**. It refuses with the fix options instead of dying minutes later at `gcc: fatal error: Killed signal terminated program cc1`:

```
ERROR: Only 276 MiB of memory is available where images are built, but compiling
       Python needs ~900 MiB for a single compile job. Not starting the build …
Fix one of:
  * give the VM more memory:  podman machine stop && podman machine set --memory 4096 && podman machine start
  * free memory in the VM:    stop containers you are not using (podman ps)
  * skip the PGO build:       PYTHON_OPTIMIZE=0 ./build.sh  (~10-20% slower Python)
  * already-cached Python:    PI_MEMORY_PREFLIGHT=0 ./build.sh  (skips this check)
```

The check reads `MemAvailable` (reclaimable page cache counts). On a warm builder it is an order of magnitude above `MemFree`. The check runs inside the podman VM. It applies to `build.sh` only. `run.py`'s project-image build compiles nothing. The toolchain arrives as a `COPY` from a prebuilt image.

The compilers are also capped by *memory* rather than core count. `make -j`, `go build -p`, and `cargo --jobs` each get `MemAvailable / peak-RSS-per-job` jobs. They never get more than `nproc`. Giving the VM more memory automatically raises the cap. `MAKE_JOBS` overrides it for CPython.

**Storage lifecycle.** The `pi-nested-<project-hash>` volume carries the same labels as project-specific images (`pi-container.type=nested-storage`, `pi-container.project.hash`, `pi-container.project.path`). The same orphan-cleanup pass reclaims it when the project directory is deleted or moved (see [Orphan detection](#orphan-detection)). As with images, a volume any container still holds open is skipped rather than removed. A later run reclaims it once that container is gone. Concurrent runs in one workspace share the volume (that is what makes the layer cache shared). They interlock through containers/storage's own lockfiles (which live inside the shared graph root). Each run keeps its own podman run root under its private `XDG_RUNTIME_DIR`.

**`docker` and `compose` both work out of the box.** A `docker` to `podman` shim is installed at `/usr/local/bin/docker`. Tools that shell out to `docker` by name work (`docker build`, `docker run`, `docker ps`, …). **`podman-compose`** ships as the compose provider. `docker compose up` and `podman compose up` work with no per-project setup. The provider is pinned in `containers.conf` (`compose_providers = ["podman-compose"]`). Without that pin podman would try `docker-compose` first and report it missing instead of using the provider that is installed.

For the full design (including the rejected alternatives: host socket, privileged DinD, sibling sidecar) and the verification log, see `docs/design/nested-containers.md`.

### Dependency definition files

Project-specific setup is defined in two files under `.pi-container/dependencies/`. These are **baked into the project-specific image at build time**, not installed at runtime. This eliminates redundant `apt-get update` and `apt-get install` calls at container startup.

| File | Privilege | Runs | Purpose |
|------|-----------|------|---------|
| `.pi-container/dependencies/root/commands.sh` | root | **Build time** | Install system packages (`apt-get`), npm globals, and system config (baked into image) |
| `.pi-container/dependencies/pi/commands.sh` | pi | **Runtime** (via entrypoint) | Init venvs, clone repos, and workspace setup (runs against bind-mounted workspace) |

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
2. When definition files exist and are non-empty, pi-container computes a content hash and builds a **project-specific image** with the scripts baked in. The image is tagged `pi-container-project-<project-hash>-<image-hash>.local` and carries labels (`pi-container.hash`, `pi-container.project.hash`, `pi-container.build.time`, `pi-container.type`) for cache invalidation and discovery.
3. At **build time**, `root/commands.sh` executes (system-wide setup: apt, npm globals). At **runtime**, `pi/commands.sh` executes via the entrypoint (workspace-local setup: venvs, cloned repos).
4. If definition files are empty or absent, the workspace uses the shared base image (no project-specific build).

**Why two execution times?**

- `root/commands.sh` runs at **build time** because it installs system-wide packages (apt, npm globals) that belong in the image. These persist across container runs.
- `pi/commands.sh` runs at **runtime** because it creates workspace-local artifacts (venvs, cloned repos) in the bind-mounted workspace. If these were created at build time, they would be hidden by the bind mount at runtime.

**Image caching and cleanup:**

Project-specific images are cached and reused across runs. pi-container computes a content hash of:
- `.pi-container/dependencies/root/commands.sh` (if it exists and is non-empty)
- `.pi-container/dependencies/pi/commands.sh` (if it exists and is non-empty)
- `pi-coding-agent/Containerfile` (always)
- `pi-coding-agent/entrypoint.sh` (always)

The toolchain (`pi-coding-agent-builder/`) is deliberately **not** hashed. The project image consumes it as a built image, not as source. What invalidates a project image there is the builder image's **build time** (see below).

The hash is stored as the `pi-container.hash` label on the image. The image tag is `pi-container-project-<project-hash>-<image-hash>.local`, where:
- `<project-hash>` is the first 10 characters of `SHA-256(str(project_dir.resolve()))` — the same hash used for proxy and network naming (`pi-proxy-<hash>`, `pi-isolated-net-<hash>`).
- `<image-hash>` is the first 16 characters of the content digest computed from `root/commands.sh`, `pi/commands.sh`, `Containerfile`, and `entrypoint.sh`.

On each run, pi-container:
1. Enumerates all existing project-specific images (those with the `pi-container.type=project` label).
2. Removes any images whose `pi-container.project.hash` matches this project but whose `pi-container.hash` differs from the current content hash.
3. Compares the image's `pi-container.build.time` against the newest build time of the two images it copies content from. These are `pi-coding-agent-proxy:local` (the mitmproxy CA certificate) and `pi-coding-agent-builder:local` (the toolchain). If either is newer, the copy is stale and the image is rebuilt.
4. If no image matches the current hash, builds a new one.
5. If an image already matches and is not stale, uses it (no rebuild).

This enables:
- **Automatic invalidation**: Editing a definition file, the Containerfile, or the entrypoint triggers a rebuild on the next run, and the old image is removed.
- **Per-project isolation**: Images from different workspaces never collide, even with identical definition files.
- **Disk-space management**: Stale project-specific images are cleaned up automatically.
- **Orphan detection**: When a project directory is deleted, its images are automatically removed on the next run of any project (not just the deleted one). See [Orphan Detection](#orphan-detection).

#### Orphan detection

Each project-specific image stores the absolute path of its source project directory in the `pi-container.project.path` label. On every run, pi-container scans all project images and removes any that don't have a verifiable source project. An image is removed if:

- Its `pi-container.project.path` label points to a path that no longer exists (deleted project), OR
- Its `pi-container.project.path` label is missing or blank (older images from before this feature — unverifiable).

The system keeps only images with a path label pointing to an **existing** directory. The shared images (`pi-coding-agent:local`, `pi-coding-agent-proxy:local`, `pi-coding-agent-builder:local`) are never cleanup candidates whatever their labels say. They belong to no project and are rebuilt only by `build.sh`.

The system enumerates and removes images **by image ID**, not by tag. A rebuild that moves a tag leaves the previous image untagged. An untagged image has no usable name. podman renders it as the literal `<none>:<none>`, which is not a valid image reference. Identifying by ID is what lets those images be reclaimed.

**The system never removes images and volumes in use.** Every cleanup pass skips anything a container (running, stopped, or merely created) still holds open. It logs it at INFO rather than attempting the removal. This is the normal case when two sessions share a workspace. A session started before a definition-file change keeps running on its now-stale image. A later run reclaims it once that session exits. The same applies to a session whose project directory was deleted mid-run, and to its nested-storage volume.

This handles:

- **Deleted projects**: Images from removed workspaces are cleaned up automatically.
- **Moved projects**: Images retain the old path label. The system detects them as orphaned and removes them. New builds use the new path.
- **Legacy images**: Images without a path label (from older versions) are removed as unverifiable.

**Build function parameters:**

The `build_project_image()` function in `src/build.py` accepts the following parameters:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `runtime` | `str` | Yes | Container runtime (`podman`) |
| `root_commands_path` | `str` | Yes | Absolute path to `root/commands.sh` on the host |
| `pi_commands_path` | `str` | Yes | Absolute path to `pi/commands.sh` on the host |
| `image_tag` | `str` | Yes | Image tag (format: `pi-container-project-<project-hash>-<image-hash>.local`) |
| `label_hash` | `str` | Yes | Content hash for cache invalidation |
| `project_hash` | `str` | No | Project identity hash → set as `pi-container.project.hash` label |
| `project_path` | `str` | No | Absolute project directory path → set as `pi-container.project.path` label (enables orphan detection) |
| `build_timestamp` | `str` | No | ISO 8601 timestamp → set as `pi-container.build.time` label |

**Image labels:**

| Label | Purpose | How it's calculated |
|-------|---------|---------------------|
| `pi-container.hash` | Content hash for cache invalidation (compares against current definition files). | SHA-256 of concatenated hashes of: `.pi-container/dependencies/root/commands.sh` and `pi/commands.sh` (each if non-empty), `pi-coding-agent/Containerfile`, `pi-coding-agent/entrypoint.sh` (16-char hex digest) |
| `pi-container.project.hash` | Project identity hash (links the image to its source workspace). | First 10 chars of SHA-256 of the absolute project directory path (10-char hex digest) |
| `pi-container.project.path` | **Absolute path of the project directory at build time** (used for orphan detection). If the path no longer exists, the image is automatically removed on the next run. | Stored as-is from `PROJECT_DIR.resolve()` at build time |
| `pi-container.build.time` | ISO 8601 timestamp of the build (used for age-based discovery). | UTC timestamp at build time in ISO 8601 format (e.g., `2024-01-15T12:30:45Z`) |
| `pi-container.type` | Set to `"project"` for project-specific images (enables system-wide discovery via `podman image ls --filter label=pi-container.type=project`). | Always set to `"project"` by `build_project_image()` |

**Key principles:**

- The shared base image carries the language runtimes and tooling every workspace needs: **Node 26**, **CPython 3.14 with `uv`**, the nested-container toolchain (**podman**, `netavark`, `aardvark-dns`), and `podman-compose`. All of those are compiled in the [toolchain builder image](architecture.md#toolchain-builder-image) and copied in. Anything beyond that is project-specific and belongs in `root/commands.sh`.
- None of it is per-project. Building Python in `root/commands.sh` meant a workspace that left the file at its no-op default had no interpreter at all. Every project-image rebuild recompiled CPython (the slowest and most OOM-prone step in the build). A project-image rebuild now compiles nothing. It copies a prebuilt tree.
- Both files are optional. If absent, the workspace uses the shared image.
- Changes to definition files trigger a rebuild on the next `run.sh` invocation (detected via image label comparison). Old images are removed automatically.

### Allowlist

The `allowlist.yaml` config in the project's `.pi-container/` defines hostname rules for the [allowlist addon](proxy/allowlist.md) running on that project's mitmproxy transparent proxy. It is **per-project**. Each workspace's proxy mounts its own allowlist (seeded from a generic pypi/npm/github/apt template on first run). Edit it per project. Traffic from the agent container to non-allowlisted hosts is **blocked with HTTP 403**. If the file is missing entirely, the image's fail-closed default blocks all hosts.

Each rule has a `name`, `mode` (`allow`), a list of `hostnames` (supporting `*` wildcards), and optional `ip_ranges`. Traffic matching any rule is permitted. All other traffic is denied. The default mode is `allow` with a `block` default action.

Current default rules allow:
- **PyPI**: `pypi.org`, `files.pythonhosted.org`
- **npm**: `registry.npmjs.org`, `*.npmjs.org`
- **GitHub**: `github.com`, `api.github.com`, `codeload.github.com`, `objects.githubusercontent.com`, and related subdomains
- **Yarn**: `registry.yarnpkg.com`
- **Debian apt**: `deb.debian.org`, `security.debian.org`, `packages.debian.org`

Add new rules for any additional hostnames the agent needs to reach (e.g. internal APIs, private package registries).

### Token Replacer Secrets

The `token_replacer.yaml` config in `.pi-container/` may reference `${ENV:VAR}` values. You must set those values in the host environment before running. `run.py` scans this config and injects the values as environment variables into the proxy container. Override `ContainerNetworkManager._pull_secrets_from_config()` (in [`src/network.py`](https://github.com/mikkovihonen/pi-container/blob/main/src/network.py)) to integrate with a secret store (Vault, AWS Secrets Manager, etc.).

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

The system mounts each path at the same absolute location inside the container. Mounts use podman's `notmpcopyup` flag. This starts them empty instead of copying the host's bind-mounted content into the tmpfs. The system deduplicates and sorts paths for deterministic output.

### Flow export

`config.yaml`'s `flow_export.enabled` toggles whether the proxy's captured HTTP/HTTPS flow history for this workspace is exported after the agent shuts down (defaults to disabled):

```yaml
# .pi-container/config.yaml
flow_export:
  enabled: true
```

When enabled, `run.py` reads the flows the proxy staged for this session. It writes a merged snapshot bucketed by UTC date under `.pi-container/exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.jsonl`. When the section is absent or malformed, export is **off** (fail-safe). The export contains full request and response bodies and headers — see [Version control](#version-control-gitignore) for why `.pi-container/exports/` must never be committed.

### Egress policy

`config.yaml`'s `egress.allow` is the **per-project** proxy egress policy. Only HTTP/HTTPS/DNS are intercepted by mitmproxy. Every other protocol is denied by default. Opt a protocol in here to let the agent use it. Note that these are forwarded **uninspected** (plain NAT). mitmproxy and the allowlist do not see them.

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

`run.py` translates truthy flags and non-empty port lists into the proxy container's `PROXY_ALLOW_*` env vars. The entrypoint uses them to open the matching `iptables` FORWARD rules. An absent or malformed section means **deny-all** (fail-safe). See [Proxy egress policy](architecture.md#proxy-egress-policy) for the full protocol/port reference.

### Chat templates

Some models need an explicit Jinja chat template. Place them under `.pi-container/chat-templates/<model>/`. Reference them from a model's `serverCustomParameters.flags` with a path **relative to the workspace**:

```json
"--chat-template-file", ".pi-container/chat-templates/Ornith-1.0-35B-FP8/chat_template.jinja"
```

`llama-server` runs on the host from the workspace directory, so the relative path resolves against `.pi-container/chat-templates/` in whichever project you launched `pi` from. The templates are seeded there on first run alongside the rest of the config. (Model *weights* are shared across projects under `llama-server/models/`. Only the small chat-template files are per-project.)

### Version control (.gitignore)

A ready-to-copy [`.gitignore.example`](https://github.com/mikkovihonen/pi-container/blob/main/docs/assets/.gitignore.example) lists every entry a workspace needs. Copy the relevant lines into your project's `.gitignore`.

Most of `.pi-container/` is project configuration you **should commit** so the environment is reproducible: `config.yaml`, `allowlist.yaml`, `token_replacer.yaml`, `chat-templates/`, and `dependencies/` (root/commands.sh and pi/commands.sh — see [Dependency definition files](#dependency-definition-files)). (`token_replacer.yaml` holds only `${ENV:VAR}` references, never resolved secrets — see [Token Replacer Secrets](#token-replacer-secrets).)

The one directory you **must ignore** is the flow-export output:

```gitignore
# pi-container: proxy flow capture — sensitive and ephemeral, never commit
.pi-container/exports/
```

`.pi-container/exports/` holds the proxy's captured HTTP/HTTPS traffic. It stores full request and response bodies and headers, including any `Authorization` and cookie values the [token_replacer](proxy/token-replacer.md) did not redact. It stores them as raw `flows-<ip>.jsonl` files and date-bucketed snapshots under `exports/flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.jsonl`. Treat it as sensitive. It is also where run-time shadows an empty tmpfs (so the agent can't read prior captures). This can leave an empty `exports/` dir in a workspace even when no traffic was captured. This repo already ignores it. Add the entry above to **your** project's `.gitignore` when you run pi-container inside it.

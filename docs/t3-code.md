# T3 Code Integration

**T3 Code** ([t3.codes](https://t3.codes)) is an open-source, local-first control panel and graphical interface (desktop, web, and mobile) for AI coding agents. It provides a visual workspace with multi-threading, interactive diff reviews, branch management, and model reasoning visualizations.

T3 Code features first-class native support for the **Pi coding agent** (`@earendil-works/pi-coding-agent`), driving Pi via its structured JSONL RPC interface.

`pi-container` supports running T3 Code directly inside the isolated agent container, allowing you to use the **https://t3.codes** web interface (or the desktop/mobile apps) while retaining complete network egress inspection, token substitution, and sandbox isolation.

---

## Architecture Overview

```mermaid
flowchart LR
    subgraph Host["Host Machine"]
        Browser["Browser or Desktop App<br/><b>https://t3.codes</b>"]
        Llama["llama-server<br/>(local models)"]
    end

    subgraph ContainerStack["pi-container Sandbox"]
        subgraph Proxy["mitmproxy Container"]
            ProxyFilter["Proxy Filtering<br/>- allowlist.yaml<br/>- token_replacer.yaml"]
        end

        subgraph Agent["Agent Container"]
            T3Server["<b>t3 serve</b><br/>(port 3773)"]
            Pi["<b>pi</b><br/>(RPC Mode)"]
            Tools["Toolchains<br/>mold, git, compilers"]
            Workspace["/workspace"]
        end
    end

    Browser -- "WebSocket (127.0.0.1:3773)" --> T3Server
    T3Server -- "JSONL RPC" --> Pi
    Pi --> Workspace
    Pi --> Tools
    Pi -- "LLM Requests" --> ProxyFilter
    ProxyFilter --> Llama
    ProxyFilter -- "Allowed APIs" --> RemoteLLM["External LLMs"]
```

### Key Principles

- **In-Container Execution**: The T3 Code server process (`t3 serve`) runs inside the agent container alongside Pi. It directly spawns `/usr/local/bin/pi` inside `/workspace`.
- **Inbound Host Forwarding**: Host port `3773` is published to the agent container (`127.0.0.1:3773`), allowing your host browser at `https://t3.codes` to establish a local WebSocket connection (`ws://127.0.0.1:3773`).
- **Proxy Egress & Token Replacement**: All outbound API calls from Pi or T3 Code traverse the `pi-coding-agent-proxy`. Unapproved network traffic is blocked, and dummy provider tokens are swapped with real credentials on the wire.
- **Dynamic Model Discovery**: T3 Code queries Pi's RPC endpoint, which dynamically loads model definitions from `/home/pi/.pi/agent/models.json` (including local `llama-server` endpoints).

---

## Quickstart

### 1. Enable the T3 Toolchain

Add `"t3"` to your `tools.include` list in `.pi-container/config.yaml`:

```yaml
tools:
  include:
    - "t3"
```

*(Alternatively, you can install the CLI dynamically in `.pi-container/dependencies/root/commands.sh` via `npm install -g t3` or `curl -fsSL https://t3.codes/install.sh | sh`).*

### 2. Launch in T3 Mode

Run the container launcher with the `--t3` flag:

```bash
./run.sh --t3
```

The launcher will:
1. Verify host port `3773` is free and forward `127.0.0.1:3773` to the agent container.
2. Launch `t3 serve --host 0.0.0.0 --port 3773`.
3. Output the T3 pairing URL:
    ```text
    ==================================================================
      Starting T3 Code server for Pi inside pi-container...
      Web UI: https://t3.codes
    ==================================================================
    Listening on http://0.0.0.0:3773
    Pairing URL: http://localhost:3773/pair#token=abcdef123456...
    ```

### 3. Connect from Your Browser

1. Click the pairing link from your terminal or open [https://t3.codes](https://t3.codes).
2. T3 Code connects to your local container instance via WebSocket.
3. Select any configured model from the model picker and start prompt sessions.

---

## Configuration Reference

### Agent Configuration (`config.yaml`)

You can set T3 mode as the default or configure custom port mappings in `.pi-container/config.yaml`:

```yaml
agent:
    # Execution mode: "cli" (default interactive terminal) or "t3" (headless server)
    mode: t3

    # Advertised host for pairing links and QR codes (defaults to "localhost")
    advertise_host: localhost

    # Inbound host ports published directly from the agent container
    ports:
        # expose: localhost (127.0.0.1 only) or lan (0.0.0.0)
        expose: localhost
        publish:
            - 3773              # host 3773 -> container 3773
            - "13773:3773"      # custom host port 13773 -> container 3773
```

### Host Advertising & Pairing URLs

When T3 Code runs inside a container, `t3 serve` must bind to `--host 0.0.0.0` so forwarded traffic from the host (`-p 127.0.0.1:3773:3773`) is accepted. By default, T3 Code inspects container network interfaces and would advertise the internal bridge IP (e.g. `10.89.0.2`), which is unreachable from host browsers.

`pi-container` includes an automated network shim (`t3-advertise-host.cjs`) that ensures pairing URLs, connection links, and terminal QR codes always display `localhost` (or your chosen host IP):

- **Default (`localhost`)**: The pairing link displays `http://localhost:3773/pair#token=...` and works directly in host browsers.
- **Custom Host / LAN IP**: If you expose the port on your local network (`agent.ports.expose: lan`) and want to scan the terminal QR code or pair from a phone or second device, configure `agent.advertise_host` in `config.yaml` or pass `--t3-advertise-host`:
    ```bash
    ./run.sh --t3 --t3-advertise-host 192.168.1.100
    ```
    The terminal pairing URL and rendered QR code will then be generated with `http://192.168.1.100:3773/pair#token=...`.


### Agent Specialization (Pi Exclusivity)

By default, upstream T3 Code ships with generic drivers enabled (such as `codex` and `claudeAgent`), while disabling Pi. Inside `pi-container`, T3 Code is strictly specialized for the Pi agent:

1. **Enterprise Policy Enforcement (`/etc/t3code/policy.json`)**:
    - The agent container bakes an administrative policy with `limits.providers.allowedDrivers: ["pi"]`.
    - Other drivers (`codex`, `claudeAgent`, `cursor`, `grok`) are permanently disallowed by policy and cannot be scheduled or started.
2. **Pre-Seeded User Settings (`userdata/settings.json`)**:
    - On server startup, `entrypoint.sh` seeds `/home/pi/.pi/agent/t3/userdata/settings.json`.
    - Pi is marked as enabled, alternative drivers are disabled, and the default model instance is set to `pi`.
3. **Persistent Base Directory (`--base-dir /home/pi/.pi/agent/t3`)**:
    - Because `/home/pi/.pi/agent` is mounted from the host project's `.pi-container/` directory, all projects, threads, and diff reviews persist across container restarts.


### Source Control Provider Toolchains

T3 Code includes integrated PR review, issue tracking, and repository operations for 6 Git hosting providers. You can enable their matching CLI tools in `tools.include`:

| Provider | Mechanism | Optional Tool | Description |
| :--- | :--- | :--- | :--- |
| **GitHub** | CLI (`gh`) | `gh` | Official GitHub CLI for pull requests and repo discovery. |
| **GitLab** | CLI (`glab`) | `glab` | Official GitLab CLI for merge requests and issue tracking. |
| **Forgejo / Gitea** | CLI (`tea`, `fj`) | `tea` | Official Gitea/Forgejo CLI. |
| **Azure DevOps** | CLI (`az`) | `az` | Azure CLI with pre-installed `azure-devops` extension. |
| **GitCafe** | CLI (`cafe`) | `cafe` | Official GitCafe CLI bundled with standalone Bun runtime. |
| **Bitbucket** | Direct API | *(none needed)* | T3 Code calls `api.bitbucket.org/2.0` natively via API token. |

To enable specific source control CLIs in your workspace, declare them in `.pi-container/config.yaml`:

```yaml
tools:
  include:
    - t3
    - gh
    - glab
    - tea
    - az
    - cafe
```


### Network Allowlist (`allowlist.yaml`)

Ensure upstream rules for T3 Code and your desired source control hosts are present in `.pi-container/allowlist.yaml`:

```yaml
    # T3 Code (t3.codes) web app and release endpoints
    - name: "t3-codes-allow"
      mode: "allow"
      hostnames:
        - "t3.codes"
        - "*.t3.codes"
        - "ping.gg"
        - "*.ping.gg"
      ip_ranges: []

    # Source control providers supported by T3 Code
    - name: "gitlab-source-control-allow"
      mode: "allow"
      hostnames:
        - "gitlab.com"
        - "*.gitlab.com"
      ip_ranges: []

    - name: "forgejo-gitea-source-control-allow"
      mode: "allow"
      hostnames:
        - "codeberg.org"
        - "*.codeberg.org"
        - "gitea.com"
        - "*.gitea.com"
        - "forgejo.org"
        - "*.forgejo.org"
      ip_ranges: []

    - name: "bitbucket-source-control-allow"
      mode: "allow"
      hostnames:
        - "bitbucket.org"
        - "*.bitbucket.org"
        - "api.bitbucket.org"
        - "auth.atlassian.com"
        - "id.atlassian.com"
        - "*.atlassian.com"
      ip_ranges: []

    - name: "azure-devops-source-control-allow"
      mode: "allow"
      hostnames:
        - "dev.azure.com"
        - "*.dev.azure.com"
        - "*.visualstudio.com"
        - "login.microsoftonline.com"
        - "management.azure.com"
      ip_ranges: []

    - name: "gitcafe-source-control-allow"
      mode: "allow"
      hostnames:
        - "git.cafe"
        - "*.git.cafe"
        - "api.git.cafe"
      ip_ranges: []
```

---

## Security Considerations

1. **Loopback Binding by Default**: Host port `3773` binds strictly to `127.0.0.1`. Remote machines on your local network cannot connect unless you explicitly set `agent.ports.expose: lan`.
2. **Unchanged Sandbox Isolation**: Running T3 Code does not require relaxing SELinux confinement or enabling `nested_containers`. The server runs as the unprivileged `pi` user.
3. **Session Persistence**: Pi session trees are saved in `/home/pi/.pi/agent/sessions/`, which is mounted from your host project's `.pi-container/` directory. All thread history persists across container restarts.


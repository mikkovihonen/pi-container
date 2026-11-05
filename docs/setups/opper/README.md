# Opper AI Gateway Setup

This setup connects `pi-container` to [Opper AI](https://platform.opper.ai/) via its European-hosted OpenAI-compatible Chat Completions endpoint (`https://api.opper.ai/v3/compat`).

---

## Prerequisites

1. An Opper account and API key from [platform.opper.ai](https://platform.opper.ai/) under **Settings > API keys**.
2. Save your API key either in the host environment or in your OS keyring:
```bash
# Option A: Save to your OS user keyring (macOS Keychain / Secret Service)
uv run keyring set pi-container OPPER_API_KEY

# Option B: Set in your host shell environment
export OPPER_API_KEY="opp_..."
```

---

## Configuration Steps

### 1. Configure the Gateway Definition
Copy `opper.yaml` from this directory into `~/.config/pi-container/gateways/opper.yaml`:
```bash
mkdir -p ~/.config/pi-container/gateways
cp docs/setups/opper/opper.yaml ~/.config/pi-container/gateways/
```

### 2. Configure the Agent Models
Copy `models.json` from this directory into your workspace's `.pi-container/agent/models.json` (or merge the `opper` provider into your existing providers list).

### 3. Allow Proxy Egress
Add `api.opper.ai` to your workspace `.pi-container/allowlist.yaml`:
```yaml
    - name: "opper-ai-allow"
      mode: "allow"
      hostnames:
        - "api.opper.ai"
      ip_ranges: []
```

### 4. Configure Credential Injection
Add the following rule to `.pi-container/token_replacer.yaml`:
```yaml
rules:
  - name: "Inject Opper API Key"
    hostnames:
      - "api.opper.ai"
    content_patterns:
      - field: "headers"
        header_name: "Authorization"
        regex: "(?<=Bearer\\s)OPPER_API_KEY_PLACEHOLDER"
    replace_with:
      strategy: "static"
      value: "${ENV:OPPER_API_KEY}"
```

---

## Notes & Features

- **Zero Secret Exposure:** The agent container only sees the dummy token `OPPER_API_KEY_PLACEHOLDER`. Real credentials never enter the agent container.
- **Failover Pools & Bare Model Names:**
    - Using provider prefixes like `anthropic/claude-sonnet-4-6` pins the route to Anthropic.
    - Using bare model names like `claude-sonnet-4-6` activates Opper's dynamic multi-provider routing and automatic failover.
- **EU Data Sovereignty & Zero Data Retention:**
    - Opper supports EU-only inference hosting and Zero Data Retention (ZDR) configurable in the Opper platform dashboard.


import sys

sys.dont_write_bytecode = True

"""Shared configuration for the run orchestration.

Holds the paths, environment-derived constants and logging setup used across
``run.py`` and its collaborator modules (``models``, ``server``, ``network``).

Importing this module has no gating side effects — it does NOT validate the
environment or call ``sys.exit``. Those app-startup checks live in ``run.py`` so
the collaborator modules (and their tests) can import configuration cheaply.
"""

import logging
import os
import shutil
from pathlib import Path

from util import load_dotenv

# ─── Paths ──────────────────────────────────────────────────────────────────

SCRIPT_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = SCRIPT_DIR.parent
PROJECT_DIR: Path = Path(os.environ.get("PROJECT_DIR", Path.cwd()))
DOTENV_PATH: Path = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

# ─── Logging ──────────────────────────────────────────────────────────────
# Configure the root logger once here; every module uses
# ``logging.getLogger(__name__)`` and inherits this configuration.

log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

# ─── Constants ──────────────────────────────────────────────────────────────

IMAGE_TAG: str = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
LLAMA_BIN: str | None = os.environ.get("LLAMA_BIN") or shutil.which("llama-server")
MAX_STARTUP_ATTEMPTS: int = int(os.environ.get("MAX_STARTUP_ATTEMPTS", 2))
MODELS_DIR: Path = REPO_ROOT / "llama-server" / "models"
LLAMA_SERVER_LOCK_DIR: Path = REPO_ROOT / "llama-server" / ".locks"
ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "")

# Directory on the host where the proxy container's config files live.
# The token_replacer config is mounted into the container at runtime so that
# run.py can scan it for ${ENV:...} references and pull required secrets
# from the host secret store.
CONFIG_DIR: Path = REPO_ROOT / ".pi-container"

# Optional explicit network overrides. ``None`` means "use the runtime's
# default"; a non-empty value forces it for the selected runtime.
BRIDGE_INTERFACE_ENV: str | None = os.environ.get("BRIDGE_INTERFACE") or None
PROXY_UPSTREAM_NETWORK_ENV: str | None = os.environ.get("PROXY_UPSTREAM_NETWORK") or None


def env_truthy(name: str, default: bool = False) -> bool:
    """Interpret an env var as a boolean (true/1/yes/on, case-insensitive)."""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Whether the proxy/agent network stack supports IPv6. Default: disabled, in
# which case IPv6 is explicitly turned off across both containers so the agent
# cannot egress uninspected over v6 (bypassing mitmproxy + the allowlist). When
# enabled, the isolated network gets an IPv6 subnet and the proxy mirrors its
# v4 REDIRECT/NAT rules in ip6tables. See .env.example for the caveats.
IPV6_ENABLED: bool = env_truthy("IPV6_ENABLED", default=False)

# Per-protocol forwarding opt-ins for the proxy. Only HTTP/HTTPS/DNS are
# intercepted by mitmproxy; every other protocol the agent emits is forwarded
# UNINSPECTED, so the proxy defaults to denying it. Setting one of these (in
# .env) opens the corresponding protocol in the proxy's FORWARD chain. Any that
# are set are passed through to the proxy container verbatim.
PROXY_FORWARD_ENV_VARS = (
    "PROXY_ALLOW_SSH",
    "PROXY_ALLOW_SMTP",
    "PROXY_ALLOW_GIT",
    "PROXY_ALLOW_NTP",
    "PROXY_ALLOW_TCP_PORTS",
    "PROXY_ALLOW_UDP_PORTS",
)
PROXY_FORWARD_ENV: dict[str, str] = {k: os.environ[k] for k in PROXY_FORWARD_ENV_VARS if os.environ.get(k)}

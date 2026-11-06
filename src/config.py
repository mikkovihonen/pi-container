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

from huggingface_hub.constants import HF_HUB_CACHE

from secret_store import get_keyring_secret
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
logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────────

GLOBAL_CONFIG_DIR: Path = Path.home() / ".config" / "pi-container"
GLOBAL_MODELS_REGISTRY_DIR: Path = GLOBAL_CONFIG_DIR / "models"
GLOBAL_GATEWAYS_REGISTRY_DIR: Path = GLOBAL_CONFIG_DIR / "gateways"
GLOBAL_CACHE_DIR: Path = Path.home() / ".cache" / "pi-container"

IMAGE_TAG: str = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
PROXY_IMAGE_TAG: str = os.environ.get("PROXY_IMAGE_TAG", "pi-coding-agent-proxy:local")
LLAMA_BIN: str | None = os.environ.get("LLAMA_BIN") or shutil.which("llama-server")
MODELS_DIR: Path = Path(HF_HUB_CACHE)
LLAMA_SERVER_LOCK_DIR: Path = GLOBAL_CACHE_DIR / "locks" / "servers"
PROXY_LOCK_DIR: Path = GLOBAL_CACHE_DIR / "locks" / "proxies"
LOGS_DIR: Path = GLOBAL_CACHE_DIR / "logs"
TOKENS_DIR: Path = GLOBAL_CACHE_DIR / "tokens"
if "ADMIN_PASSWORD" in os.environ:
    logger.warning("ADMIN_PASSWORD in host environment is ignored; credentials must be stored in the OS keyring.")
    del os.environ["ADMIN_PASSWORD"]
ADMIN_PASSWORD: str = get_keyring_secret("ADMIN_PASSWORD") or ""

# Directory on the host where the proxy container's config files live.
# The token_replacer config is mounted into the container at runtime so that
# run.py can scan it for ${ENV:...} references and pull required secrets
# from the host secret store.
CONFIG_DIR: Path = REPO_ROOT / ".pi-container"

# Optional explicit network overrides. ``None`` means "use the runtime's
# default"; a non-empty value forces it for the selected runtime.
BRIDGE_INTERFACE_ENV: str | None = os.environ.get("BRIDGE_INTERFACE") or None
PROXY_UPSTREAM_NETWORK_ENV: str | None = os.environ.get("PROXY_UPSTREAM_NETWORK") or None

# NOTE: per-project settings — IPv6, proxy DNS, mitmweb UI exposure, mitmweb flow
# export, proxy egress policy, tmpfs paths, llama-server startup tuning, container
# resource limits, and extra agent env/mounts — are read from
# ``{PROJECT_DIR}/.pi-container/config.yaml`` (see ``network.load_project_config``
# and its accessors), not environment variables.

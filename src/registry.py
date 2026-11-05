from __future__ import annotations

import sys

sys.dont_write_bytecode = True

"""Global model and gateway registry loader.

- Gateways: ~/.config/pi-container/gateways/<gateway_id>.yaml (provider-level external gateways)
- Models:   ~/.config/pi-container/models/<model_id>.yaml     (model-level local llama-server definitions)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import GLOBAL_GATEWAYS_REGISTRY_DIR, GLOBAL_MODELS_REGISTRY_DIR
from models import ServerConfig

logger = logging.getLogger(__name__)


# ─── Data Structures ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GatewayAuth:
    """Authentication configuration for a model gateway."""

    type: str = "openid"
    discovery_url: str | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    scopes: list[str] = field(default_factory=list)
    token_cache: Path | None = None
    env_var: str | None = None
    keyring_service: str | None = None
    keyring_account: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayAuth:
        token_cache_val = data.get("tokenCache")
        token_cache_path = Path(token_cache_val).expanduser() if token_cache_val else None
        return cls(
            type=data.get("type", "openid"),
            discovery_url=data.get("discoveryUrl"),
            authorization_url=data.get("authorizationUrl"),
            token_url=data.get("tokenUrl"),
            client_id=data.get("clientId"),
            scopes=data.get("scopes", []),
            token_cache=token_cache_path,
            env_var=data.get("envVar"),
            keyring_service=data.get("keyringService"),
            keyring_account=data.get("keyringAccount"),
        )


@dataclass(frozen=True)
class GatewayDef:
    """Definition for an external model gateway (e.g. Opper, Nitor Gateway)."""

    id: str
    base_url: str
    gateway_type: str = "openai-compat"
    description: str = ""
    auth: GatewayAuth | None = None

    @classmethod
    def from_dict(cls, gateway_id: str, data: dict[str, Any]) -> GatewayDef:
        auth_data = data.get("auth")
        auth = GatewayAuth.from_dict(auth_data) if auth_data else None
        return cls(
            id=data.get("id", gateway_id),
            base_url=data.get("baseUrl", ""),
            gateway_type=data.get("type", "openai-compat"),
            description=data.get("description", ""),
            auth=auth,
        )


@dataclass(frozen=True)
class LlamaServerModelDef:
    """Model definition managed locally via a host llama-server process."""

    id: str
    server_config: ServerConfig
    description: str = ""

    @classmethod
    def from_dict(cls, model_id: str, data: dict[str, Any]) -> LlamaServerModelDef:
        server_config = ServerConfig.from_dict(
            {
                "hfModels": data.get("hfModels", {}),
                "flags": data.get("flags", []),
            }
        )
        return cls(
            id=data.get("id", model_id),
            server_config=server_config,
            description=data.get("description", ""),
        )


# ─── Default Seed Definitions ────────────────────────────────────────────────

DEFAULT_QWEN_MODEL_DEF = """id: Qwen3.6-35B-A3B-UD-Q6_K_XL
kind: llama-server
description: "Qwen 3.6 35B with speculative decoding"

hfModels:
  main:
    fileFlag: "--model"
    repo: "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"
    file: "Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf"
    additionalServerFlags:
      - "--spec-type"
      - "draft-mtp"
      - "--spec-draft-n-max"
      - 2
    sha256: ""

flags:
  - "--load-mode"
  - "mlock"
  - "--kv-offload"
  - "--threads"
  - 8
  - "--threads-batch"
  - 8
  - "--parallel"
  - 1
  - "--batch-size"
  - 4096
  - "--ubatch-size"
  - 512
  - "--flash-attn"
  - "on"
  - "--cache-type-k"
  - "q8_0"
  - "--cache-type-v"
  - "q8_0"
  - "--ctx-size"
  - 131072
  - "--ctx-checkpoints"
  - 32
  - "--checkpoint-min-step"
  - 256
  - "--repeat-penalty"
  - 1.0
  - "--top-p"
  - 0.95
  - "--top-k"
  - 64
  - "--prio"
  - 2
  - "--cache-ram"
  - 8192
  - "--jinja"
  - "--chat-template-file"
  - ".pi-container/chat-templates/Qwen3.6-35B-A3B-UD-Q6_K-XL/chat_template.jinja"
  - "--reasoning"
  - "on"
  - "--n-gpu-layers"
  - 999
"""


# ─── Registry Loader Functions ───────────────────────────────────────────────


def get_models_dir(override_dir: Path | None = None) -> Path:
    """Return the active global models registry directory."""
    return override_dir if override_dir is not None else GLOBAL_MODELS_REGISTRY_DIR


def get_gateways_dir(override_dir: Path | None = None) -> Path:
    """Return the active global gateways registry directory."""
    return override_dir if override_dir is not None else GLOBAL_GATEWAYS_REGISTRY_DIR


def seed_default_models(registry_dir: Path | None = None) -> list[str]:
    """Seed default model definitions (Qwen3.6) if the models registry directory is empty or missing."""
    target_dir = get_models_dir(registry_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    seeded: list[str] = []
    existing = list(target_dir.glob("*.yaml"))
    if not existing:
        qwen_file = target_dir / "Qwen3.6-35B-A3B-UD-Q6_K_XL.yaml"
        qwen_file.write_text(DEFAULT_QWEN_MODEL_DEF, encoding="utf-8")
        seeded.append(qwen_file.name)
        logger.info(f"Seeded default model definition: {qwen_file}")

    return seeded


def _find_definition_file(name: str, target_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    if not target_dir.exists():
        return None

    candidate = target_dir / f"{name}.yaml"
    if candidate.is_file():
        try:
            from yaml_strict import load_yaml_file

            data = load_yaml_file(candidate)
        except Exception as e:
            logger.error(f"Failed to parse registry definition {candidate}: {e}")
            return None

        if not isinstance(data, dict):
            logger.error(f"Registry definition {candidate} must be a dictionary.")
            return None

        return candidate, data

    return None


def load_gateway_def(gateway_id: str, registry_dir: Path | None = None) -> GatewayDef | None:
    """Load an external gateway definition by ID from ~/.config/pi-container/gateways/."""
    target_dir = get_gateways_dir(registry_dir)
    found = _find_definition_file(gateway_id, target_dir)
    if not found:
        return None
    _, data = found
    return GatewayDef.from_dict(gateway_id, data)


def load_model_def(model_id: str, registry_dir: Path | None = None) -> LlamaServerModelDef | None:
    """Load a local llama-server model definition by ID from ~/.config/pi-container/models/."""
    target_dir = get_models_dir(registry_dir)
    found = _find_definition_file(model_id, target_dir)
    if not found:
        return None
    _, data = found

    from schema_common import validate_model_def_data

    def_errors = validate_model_def_data(data, model_id=model_id)
    if def_errors:
        for err in def_errors:
            logger.error(f"Model definition error in {model_id}.yaml: {err}")
        return None

    return LlamaServerModelDef.from_dict(model_id, data)


def list_gateway_defs(registry_dir: Path | None = None) -> list[GatewayDef]:
    """List all valid gateway definitions found in the gateways registry directory."""
    target_dir = get_gateways_dir(registry_dir)
    if not target_dir.exists():
        return []

    defs: list[GatewayDef] = []
    for path in sorted(target_dir.iterdir()):
        if path.suffix == ".yaml" and path.is_file():
            gw_def = load_gateway_def(path.stem, target_dir)
            if gw_def:
                defs.append(gw_def)
    return defs


def list_model_defs(registry_dir: Path | None = None) -> list[LlamaServerModelDef]:
    """List all valid model definitions found in the models registry directory."""
    target_dir = get_models_dir(registry_dir)
    if not target_dir.exists():
        return []

    defs: list[LlamaServerModelDef] = []
    for path in sorted(target_dir.iterdir()):
        if path.suffix == ".yaml" and path.is_file():
            m_def = load_model_def(path.stem, target_dir)
            if m_def:
                defs.append(m_def)
    return defs

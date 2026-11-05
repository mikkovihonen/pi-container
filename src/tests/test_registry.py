import sys

sys.dont_write_bytecode = True

"""Tests for src/registry.py (global model & gateway registry loader)."""

from pathlib import Path

from registry import (
    GatewayDef,
    LlamaServerModelDef,
    list_gateway_defs,
    list_model_defs,
    load_gateway_def,
    load_model_def,
    seed_default_models,
)


def test_seed_default_models(tmp_path: Path):
    reg_dir = tmp_path / "models"
    assert not reg_dir.exists()

    seeded = seed_default_models(reg_dir)
    assert len(seeded) == 1
    assert seeded[0] == "Qwen3.6-35B-A3B-UD-Q6_K_XL.yaml"
    assert (reg_dir / "Qwen3.6-35B-A3B-UD-Q6_K_XL.yaml").exists()

    # Second call does not re-seed or overwrite
    seeded_again = seed_default_models(reg_dir)
    assert seeded_again == []


def test_load_llama_server_model_def(tmp_path: Path):
    reg_dir = tmp_path / "models"
    seed_default_models(reg_dir)

    m_def = load_model_def("Qwen3.6-35B-A3B-UD-Q6_K_XL", reg_dir)
    assert m_def is not None
    assert isinstance(m_def, LlamaServerModelDef)
    assert m_def.id == "Qwen3.6-35B-A3B-UD-Q6_K_XL"
    assert "main" in m_def.server_config.hf_models
    assert m_def.server_config.hf_models["main"].repo == "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"
    assert "--load-mode" in m_def.server_config.flags


def test_load_gateway_def_oidc_discovery(tmp_path: Path):
    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)

    gw_yaml = """
id: nitor
description: "Nitor Corporate Gateway"
type: nitor
baseUrl: "https://gw.nitor.app/v1"
auth:
  type: openid
  discoveryUrl: "https://auth.nitor.app/.well-known/openid-configuration"
  clientId: "pi-container"
  scopes:
    - "openid"
    - "profile"
"""
    (gw_dir / "nitor.yaml").write_text(gw_yaml)

    gw_def = load_gateway_def("nitor", gw_dir)
    assert gw_def is not None
    assert isinstance(gw_def, GatewayDef)
    assert gw_def.id == "nitor"
    assert gw_def.base_url == "https://gw.nitor.app/v1"
    assert gw_def.auth is not None
    assert gw_def.auth.type == "openid"
    assert gw_def.auth.discovery_url == "https://auth.nitor.app/.well-known/openid-configuration"
    assert gw_def.auth.client_id == "pi-container"
    assert "openid" in gw_def.auth.scopes


def test_load_gateway_def_explicit_endpoints(tmp_path: Path):
    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)

    gw_yaml = """
id: custom-gw
type: generic-openai
baseUrl: "https://api.gateway.example.com/v1"
auth:
  type: openid
  authorizationUrl: "https://auth.example.com/oauth/authorize"
  tokenUrl: "https://auth.example.com/oauth/token"
  clientId: "test-client"
  tokenCache: "~/.cache/pi-container/tokens/custom.json"
"""
    (gw_dir / "custom-gw.yaml").write_text(gw_yaml)

    gw_def = load_gateway_def("custom-gw", gw_dir)
    assert gw_def is not None
    assert isinstance(gw_def, GatewayDef)
    assert gw_def.auth is not None
    assert gw_def.auth.authorization_url == "https://auth.example.com/oauth/authorize"
    assert gw_def.auth.token_url == "https://auth.example.com/oauth/token"
    assert gw_def.auth.token_cache == Path.home() / ".cache" / "pi-container" / "tokens" / "custom.json"


def test_load_gateway_def_api_key(tmp_path: Path):
    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)

    gw_yaml = """
id: opper
description: "Opper AI Gateway"
type: openai-compat
baseUrl: "https://api.opper.ai/v3/compat"
auth:
  type: api-key
  envVar: "OPPER_API_KEY"
  keyringService: "pi-container"
  keyringAccount: "OPPER_API_KEY"
"""
    (gw_dir / "opper.yaml").write_text(gw_yaml)

    gw_def = load_gateway_def("opper", gw_dir)
    assert gw_def is not None
    assert isinstance(gw_def, GatewayDef)
    assert gw_def.id == "opper"
    assert gw_def.gateway_type == "openai-compat"
    assert gw_def.base_url == "https://api.opper.ai/v3/compat"
    assert gw_def.auth is not None
    assert gw_def.auth.type == "api-key"
    assert gw_def.auth.env_var == "OPPER_API_KEY"
    assert gw_def.auth.keyring_service == "pi-container"
    assert gw_def.auth.keyring_account == "OPPER_API_KEY"


def test_load_gateway_def_defaults_to_openai_compat(tmp_path: Path):
    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)

    gw_yaml = """
id: generic-gw
baseUrl: "https://api.example.com/v1"
"""
    (gw_dir / "generic-gw.yaml").write_text(gw_yaml)

    gw_def = load_gateway_def("generic-gw", gw_dir)
    assert gw_def is not None
    assert isinstance(gw_def, GatewayDef)
    assert gw_def.gateway_type == "openai-compat"


def test_load_nonexistent_model_returns_none(tmp_path: Path):
    reg_dir = tmp_path / "models"
    reg_dir.mkdir(parents=True)
    assert load_model_def("does-not-exist", reg_dir) is None


def test_load_nonexistent_gateway_returns_none(tmp_path: Path):
    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)
    assert load_gateway_def("does-not-exist", gw_dir) is None


def test_load_corrupt_file_returns_none(tmp_path: Path):
    reg_dir = tmp_path / "models"
    reg_dir.mkdir(parents=True)
    (reg_dir / "broken.yaml").write_text("invalid: yaml: [unclosed")
    assert load_model_def("broken", reg_dir) is None

    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)
    (gw_dir / "broken.yaml").write_text("invalid: yaml: [unclosed")
    assert load_gateway_def("broken", gw_dir) is None


def test_list_model_defs(tmp_path: Path):
    reg_dir = tmp_path / "models"
    seed_default_models(reg_dir)

    defs = list_model_defs(reg_dir)
    assert len(defs) == 1
    assert defs[0].id == "Qwen3.6-35B-A3B-UD-Q6_K_XL"


def test_list_gateway_defs(tmp_path: Path):
    gw_dir = tmp_path / "gateways"
    gw_dir.mkdir(parents=True)

    gw_yaml = """
id: opper
baseUrl: "https://api.opper.ai/v3/compat"
"""
    (gw_dir / "opper.yaml").write_text(gw_yaml)

    defs = list_gateway_defs(gw_dir)
    assert len(defs) == 1
    assert defs[0].id == "opper"

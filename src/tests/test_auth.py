from __future__ import annotations

import sys

sys.dont_write_bytecode = True

"""Tests for src/auth.py (OpenID Connect browser authentication & token management)."""

import json
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import auth
from registry import GatewayAuth

if TYPE_CHECKING:
    from pathlib import Path


def test_pkce_generation():
    verifier = auth.generate_code_verifier()
    assert len(verifier) >= 43

    challenge = auth.generate_code_challenge(verifier)
    assert len(challenge) > 0
    assert "=" not in challenge


def test_resolve_oidc_endpoints_explicit():
    gw_auth = GatewayAuth(
        type="openid",
        authorization_url="https://auth.example.com/oauth/authorize",
        token_url="https://auth.example.com/oauth/token",
    )
    auth_ep, token_ep = auth.resolve_oidc_endpoints(gw_auth)
    assert auth_ep == "https://auth.example.com/oauth/authorize"
    assert token_ep == "https://auth.example.com/oauth/token"


def test_resolve_oidc_endpoints_discovery():
    gw_auth = GatewayAuth(
        type="openid",
        discovery_url="https://auth.example.com/.well-known/openid-configuration",
    )

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
        }
    ).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        auth_ep, token_ep = auth.resolve_oidc_endpoints(gw_auth)
        assert auth_ep == "https://auth.example.com/authorize"
        assert token_ep == "https://auth.example.com/token"


def test_resolve_oidc_endpoints_missing_raises():
    gw_auth = GatewayAuth(type="openid")
    with pytest.raises(ValueError, match="Gateway auth requires"):
        auth.resolve_oidc_endpoints(gw_auth)


def test_token_cache_rw(tmp_path: Path):
    cache_file = tmp_path / "tokens" / "test.json"
    data = {"access_token": "abc123xyz", "expires_at": time.time() + 3600}

    auth.write_cached_token(cache_file, data)
    assert cache_file.exists()
    assert (cache_file.stat().st_mode & 0o777) == 0o600

    loaded = auth.read_cached_token(cache_file)
    assert loaded == data


def test_ensure_gateway_auth_returns_valid_cached(tmp_path: Path):
    cache_file = tmp_path / "tokens" / "nitor.json"
    auth.write_cached_token(
        cache_file,
        {"access_token": "valid_token_123", "expires_at": time.time() + 1000},
    )

    gw_auth = GatewayAuth(
        type="openid",
        token_cache=cache_file,
        authorization_url="https://auth.example.com/auth",
        token_url="https://auth.example.com/token",
    )

    token = auth.ensure_gateway_auth("nitor", gw_auth)
    assert token == "valid_token_123"


def test_ensure_gateway_auth_refreshes_expired_token(tmp_path: Path):
    cache_file = tmp_path / "tokens" / "nitor.json"
    auth.write_cached_token(
        cache_file,
        {
            "access_token": "expired_token",
            "refresh_token": "valid_refresh",
            "expires_at": time.time() - 100,  # Expired
        },
    )

    gw_auth = GatewayAuth(
        type="openid",
        token_cache=cache_file,
        authorization_url="https://auth.example.com/auth",
        token_url="https://auth.example.com/token",
        client_id="test-client",
    )

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {
            "access_token": "new_fresh_token",
            "expires_in": 3600,
        }
    ).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        token = auth.ensure_gateway_auth("nitor", gw_auth)
        assert token == "new_fresh_token"

        # Verify cache was updated
        updated = auth.read_cached_token(cache_file)
        assert updated["access_token"] == "new_fresh_token"
        assert updated["refresh_token"] == "valid_refresh"

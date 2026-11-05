from __future__ import annotations

import sys

sys.dont_write_bytecode = True

"""OpenID Connect browser-interactive authentication with PKCE for model gateways."""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from config import TOKENS_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from registry import GatewayAuth

logger = logging.getLogger(__name__)


# ─── PKCE Helpers ────────────────────────────────────────────────────────────


def generate_code_verifier() -> str:
    """Generate a high-entropy cryptographic random string for PKCE (RFC 7636)."""
    return secrets.token_urlsafe(64)


def generate_code_challenge(verifier: str) -> str:
    """Generate the SHA-256 base64url-encoded code challenge from the verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# ─── OIDC Discovery Helper ───────────────────────────────────────────────────


def resolve_oidc_endpoints(auth: GatewayAuth) -> tuple[str, str]:
    """Resolve (authorization_endpoint, token_endpoint) from discoveryUrl or explicit URLs."""
    if auth.discovery_url:
        logger.info(f"Fetching OIDC configuration from {auth.discovery_url}")
        req = urllib.request.Request(auth.discovery_url, headers={"User-Agent": "pi-container"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            auth_ep = data.get("authorization_endpoint")
            token_ep = data.get("token_endpoint")
            if not auth_ep or not token_ep:
                raise ValueError(f"OIDC discovery document missing required endpoints: {auth.discovery_url}")
            return auth_ep, token_ep

    if auth.authorization_url and auth.token_url:
        return auth.authorization_url, auth.token_url

    raise ValueError("Gateway auth requires either 'discoveryUrl' or both 'authorizationUrl' and 'tokenUrl'.")


# ─── Token Cache Helpers ─────────────────────────────────────────────────────


def get_token_cache_path(gateway_id: str, auth: GatewayAuth) -> Path:
    """Return the path to the cached token file, ensuring parent directory exists."""
    path = auth.token_cache or TOKENS_DIR / f"{gateway_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_cached_token(cache_path: Path) -> dict[str, Any] | None:
    """Read cached token data if the file exists and is valid JSON."""
    if not cache_path.is_file():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to read token cache {cache_path}: {e}")
        return None


def write_cached_token(cache_path: Path, data: dict[str, Any]) -> None:
    """Write token data securely with 0600 permissions."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(cache_path, 0o600)
    except OSError as e:
        logger.warning(f"Could not set 0600 permissions on {cache_path}: {e}")


# ─── Refresh Token Grant ─────────────────────────────────────────────────────


def refresh_access_token(token_endpoint: str, client_id: str, refresh_token: str) -> dict[str, Any] | None:
    """Attempt to exchange a refresh token for a new access token."""
    payload = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        token_endpoint,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "pi-container",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "expires_in" in data:
                data["expires_at"] = time.time() + float(data["expires_in"])
            return data
    except Exception as e:
        logger.warning(f"Failed to refresh access token: {e}")
        return None


# ─── Interactive Browser Login Flow ──────────────────────────────────────────


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Temporary local HTTP server handler to receive the OAuth redirect code."""

    auth_code: str | None = None
    auth_state: str | None = None
    auth_error: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard HTTP server logging."""
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        if "error" in params:
            _OAuthCallbackHandler.auth_error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication Failed</h1><p>Error received from identity provider.</p>")
            return

        if "code" in params and "state" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            _OAuthCallbackHandler.auth_state = params["state"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                b"<h2>Authentication Successful</h2>"
                b"<p>You may close this tab and return to your terminal.</p>"
                b"</body></html>"
            )


def interactive_browser_login(
    auth_endpoint: str,
    token_endpoint: str,
    client_id: str,
    scopes: list[str],
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Execute an OAuth 2.0 PKCE authorization code grant via local browser."""
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)

    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.auth_state = None
    _OAuthCallbackHandler.auth_error = None

    server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    server_port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{server_port}/callback"

    scope_str = " ".join(scopes) if scopes else "openid profile offline_access"
    query_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope_str,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{auth_endpoint}?{urllib.parse.urlencode(query_params)}"

    print(f"\n[pi-container] Opening browser for OpenID authentication:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Poll server for callback with timeout
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        server.handle_request()
        if _OAuthCallbackHandler.auth_code or _OAuthCallbackHandler.auth_error:
            break

    server.server_close()

    if _OAuthCallbackHandler.auth_error:
        raise RuntimeError(f"Authentication failed: {_OAuthCallbackHandler.auth_error}")

    if not _OAuthCallbackHandler.auth_code:
        raise TimeoutError("Interactive browser authentication timed out waiting for redirect.")

    if _OAuthCallbackHandler.auth_state != state:
        raise ValueError("Invalid OAuth state returned; CSRF protection check failed.")

    # Exchange authorization code for tokens
    payload = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": _OAuthCallbackHandler.auth_code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        token_endpoint,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "pi-container",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if "expires_in" in data:
            data["expires_at"] = time.time() + float(data["expires_in"])
        return data


# ─── Public Main Entry Point ─────────────────────────────────────────────────


def ensure_gateway_auth(gateway_id: str, auth: GatewayAuth) -> str:
    """Ensure a valid access token is available for the given gateway.

    Checks cached tokens, refreshes if expired, or launches interactive browser login.
    Returns the valid Bearer access token string.
    """
    cache_path = get_token_cache_path(gateway_id, auth)
    cached = read_cached_token(cache_path)

    # 1. Use cached token if valid (with 60s safety buffer)
    if cached and "access_token" in cached:
        expires_at = cached.get("expires_at", 0)
        if expires_at > time.time() + 60:
            return cached["access_token"]

        # 2. Try refresh token if available
        if "refresh_token" in cached:
            try:
                _, token_ep = resolve_oidc_endpoints(auth)
                client_id = auth.client_id or "pi-container"
                refreshed = refresh_access_token(token_ep, client_id, cached["refresh_token"])
                if refreshed and "access_token" in refreshed:
                    if "refresh_token" not in refreshed:
                        refreshed["refresh_token"] = cached["refresh_token"]
                    write_cached_token(cache_path, refreshed)
                    logger.info(f"Refreshed access token for gateway '{gateway_id}'")
                    return refreshed["access_token"]
            except Exception as e:
                logger.warning(f"Could not refresh token for gateway '{gateway_id}': {e}")

    # 3. Interactive browser login
    auth_ep, token_ep = resolve_oidc_endpoints(auth)
    client_id = auth.client_id or "pi-container"
    new_tokens = interactive_browser_login(
        auth_endpoint=auth_ep,
        token_endpoint=token_ep,
        client_id=client_id,
        scopes=auth.scopes,
    )
    write_cached_token(cache_path, new_tokens)
    logger.info(f"Successfully authenticated gateway '{gateway_id}' via browser")
    return new_tokens["access_token"]

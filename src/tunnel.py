from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from contextlib import ExitStack

sys.dont_write_bytecode = True

from secret_store import DEFAULT_KEYRING_SERVICE, get_keyring_secret

logger = logging.getLogger(__name__)

FQDN_REGEX = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,}$")


class TunnelValidationError(Exception):
    """Raised when tunnel preflight checks or prerequisite validations fail."""


def format_error_box(title: str, lines: list[str]) -> str:
    """Format an eye-catching error box for terminal startup failures."""
    sep = "=" * 80
    body = "\n".join(f"  {line}" for line in lines)
    return f"\n{sep}\n  STARTUP ERROR: {title}\n{sep}\n{body}\n{sep}\n"


def validate_domain_fqdn(domain: str) -> bool:
    """Validate that a domain name is a syntactically valid FQDN."""
    if not domain or not isinstance(domain, str):
        return False
    clean = domain.strip().lower()
    if clean.startswith("http://") or clean.startswith("https://"):
        return False
    return bool(FQDN_REGEX.match(clean))


def resolve_tunnel_token(
    account: str = "CLOUDFLARE_TUNNEL_TOKEN",
    service: str = DEFAULT_KEYRING_SERVICE,
) -> str | None:
    """Resolve tunnel credentials exclusively from the OS keyring."""
    return get_keyring_secret(account=account, service=service)


def normalize_tunnel_url(raw_url: str) -> str:
    """Normalize any tunnel or public URL to strict HTTPS with no port.

    Examples:
      - 'http://t3.example.com:3773' -> 'https://t3.example.com'
      - 'https://t3.example.com:443/' -> 'https://t3.example.com'
      - 't3.example.com'             -> 'https://t3.example.com'
      - 'https://node.tailnet.ts.net' -> 'https://node.tailnet.ts.net'
    """
    if not raw_url or not isinstance(raw_url, str):
        raise TunnelValidationError("Tunnel URL must be a non-empty string.")

    clean = raw_url.strip()
    if not clean:
        raise TunnelValidationError("Tunnel URL must be a non-empty string.")

    if not clean.startswith(("http://", "https://")):
        if clean.count(":") > 1 and not clean.startswith("["):
            clean = f"[{clean}]"
        clean = f"https://{clean}"
    elif clean.startswith("http://"):
        rest = clean[7:]
        if rest.count(":") > 1 and not rest.startswith("["):
            rest = f"[{rest}]"
        clean = f"https://{rest}"

    parsed = urlparse(clean)
    hostname = parsed.hostname
    if not hostname:
        raise TunnelValidationError(f"Invalid tunnel URL: '{raw_url}'")

    hostname = hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        return f"https://[{hostname}]"
    return f"https://{hostname}"


def validate_tunnel_prerequisites(
    tunnel_type: str | None,
    domain: str | None = None,
    quick: bool = False,
    runner: str = "auto",
    keyring_account: str = "CLOUDFLARE_TUNNEL_TOKEN",
    tailscale_mode: str = "serve",
    runtime: str = "podman",
) -> None:
    """Validate tunnel prerequisites fail-fast on startup before initializing any resources."""
    if not tunnel_type:
        return

    ttype = tunnel_type.strip().lower()
    if ttype not in ("tailscale", "cloudflare"):
        raise TunnelValidationError(
            format_error_box(
                f"Unsupported Tunnel Type '{tunnel_type}'",
                [
                    f"The specified tunnel type '{tunnel_type}' is not supported.",
                    "Supported tunnel options:",
                    "  --t3-tunnel tailscale   (Private WireGuard mesh with automated HTTPS)",
                    "  --t3-tunnel cloudflare  (Cloudflare Zero Trust tunnel)",
                ],
            )
        )

    if ttype == "tailscale":
        _validate_tailscale_prerequisites(tailscale_mode=tailscale_mode)
    elif ttype == "cloudflare":
        _validate_cloudflare_prerequisites(
            domain=domain,
            quick=quick,
            runner=runner,
            keyring_account=keyring_account,
            runtime=runtime,
        )


def _validate_tailscale_prerequisites(tailscale_mode: str = "serve") -> None:
    """Validate host Tailscale installation and daemon state."""
    tailscale_bin = shutil.which("tailscale")
    if not tailscale_bin:
        raise TunnelValidationError(
            format_error_box(
                "Tailscale is not installed on the host",
                [
                    "You passed '--t3-tunnel tailscale', but the 'tailscale' binary",
                    "was not found on your host PATH.",
                    "",
                    "Required Actions:",
                    "  1. Install Tailscale on your host machine:",
                    "     - macOS:   brew install tailscale (or install the Tailscale App)",
                    "     - Linux:   curl -fsSL https://tailscale.com/install.sh | sh",
                    "  2. Authenticate and connect:",
                    "     - Run: tailscale up",
                    "  3. Re-run pi-container:",
                    "     - ./run.sh --t3 --t3-tunnel tailscale",
                ],
            )
        )

    try:
        res = subprocess.run(
            [tailscale_bin, "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr or "daemon unreachable")
        status_data = json.loads(res.stdout) if res.stdout else {}
        backend_state = status_data.get("BackendState", "")
        if backend_state not in ("Running", "Starting"):
            raise TunnelValidationError(
                format_error_box(
                    "Tailscale daemon is not authenticated or running",
                    [
                        f"Tailscale is installed, but reports state '{backend_state}'.",
                        "",
                        "Required Actions:",
                        "  1. Connect this device to your Tailnet:",
                        "     - Run: tailscale up",
                        "  2. Verify connection:",
                        "     - Run: tailscale status",
                        "  3. Re-run pi-container.",
                    ],
                )
            )
    except TunnelValidationError:
        raise
    except Exception as e:
        raise TunnelValidationError(
            format_error_box(
                "Unable to communicate with Tailscale daemon",
                [
                    f"Tailscale daemon status check failed: {e}",
                    "",
                    "Ensure the Tailscale service is running on the host:",
                    "  - Run: tailscale up",
                ],
            )
        ) from e


def _validate_cloudflare_prerequisites(
    domain: str | None,
    quick: bool,
    runner: str,
    keyring_account: str,
    runtime: str,
) -> None:
    """Validate Cloudflare tunnel arguments, domain, keyring credentials, and runners."""
    if not quick:
        if not domain:
            raise TunnelValidationError(
                format_error_box(
                    "Cloudflare Tunnel Domain Missing",
                    [
                        "You passed '--t3-tunnel cloudflare', but did not specify your user-owned",
                        "domain on Cloudflare.",
                        "",
                        "Required Actions:",
                        "  1. Specify your user-owned domain via --t3-tunnel-domain:",
                        "     - ./run.sh --t3 --t3-tunnel cloudflare --t3-tunnel-domain t3.yourdomain.com",
                        "",
                        "  2. (Alternatively) Run a zero-setup ephemeral tunnel without a custom domain:",
                        "     - ./run.sh --t3 --t3-tunnel cloudflare --t3-tunnel-quick",
                    ],
                )
            )
        if not validate_domain_fqdn(domain):
            raise TunnelValidationError(
                format_error_box(
                    "Invalid Domain Name",
                    [
                        f"The value '{domain}' supplied to --t3-tunnel-domain is not a valid",
                        "fully-qualified domain name (FQDN).",
                        "",
                        "Expected format:",
                        "  subdomain.yourdomain.com (e.g. t3.myworkspace.dev)",
                    ],
                )
            )

        token = resolve_tunnel_token(account=keyring_account)
        if not token:
            raise TunnelValidationError(
                format_error_box(
                    "Cloudflare Tunnel Token not found in OS Keyring",
                    [
                        f"You requested a named Cloudflare tunnel for '{domain}', but no",
                        f"tunnel token was found in the OS keyring under service '{DEFAULT_KEYRING_SERVICE}'",
                        f"and account '{keyring_account}'.",
                        "",
                        "Required Actions:",
                        "  1. Retrieve your tunnel token from the Cloudflare Zero Trust dashboard:",
                        "     (Networks -> Tunnels -> Select Tunnel -> Copy token)",
                        "  2. Save it securely in your OS keyring:",
                        f"     - Run: uv run keyring set {DEFAULT_KEYRING_SERVICE} {keyring_account}",
                        "  3. Re-run pi-container.",
                    ],
                )
            )

    runner_mode = runner.strip().lower()
    if runner_mode not in ("auto", "host", "container"):
        raise TunnelValidationError(
            format_error_box(
                f"Invalid Tunnel Runner '{runner}'",
                [
                    f"Runner '{runner}' is invalid. Expected: 'auto', 'host', or 'container'.",
                ],
            )
        )

    has_host_bin = bool(shutil.which("cloudflared"))
    has_runtime = bool(shutil.which(runtime))

    if runner_mode == "host" and not has_host_bin:
        raise TunnelValidationError(
            format_error_box(
                "Host binary 'cloudflared' not found",
                [
                    "You configured '--t3-tunnel-runner host', but 'cloudflared' was not found on your host PATH.",
                    "",
                    "Required Actions:",
                    "  - Install cloudflared: brew install cloudflared (macOS) or apt/dnf install cloudflared",
                    "  - Or use containerized mode: --t3-tunnel-runner container (or --t3-tunnel-runner auto)",
                ],
            )
        )

    if runner_mode == "container" and not has_runtime:
        raise TunnelValidationError(
            format_error_box(
                f"Container runtime '{runtime}' not found",
                [
                    f"You configured '--t3-tunnel-runner container', but '{runtime}' was not found on your host PATH.",
                ],
            )
        )

    if runner_mode == "auto" and not has_host_bin and not has_runtime:
        raise TunnelValidationError(
            format_error_box(
                "Unable to run cloudflared (binary and container runtime unavailable)",
                [
                    "'cloudflared' was not found on your host PATH, and running containerized",
                    f"via '{runtime}' is not available.",
                    "",
                    "Required Actions:",
                    "  - Install cloudflared: brew install cloudflared (macOS)",
                    f"  - OR ensure container runtime '{runtime}' is installed and in PATH.",
                ],
            )
        )


class TailscaleTunnelManager:
    """Manages Tailscale tunnel integration and MagicDNS HTTPS exposure."""

    def __init__(self, mode: str = "serve", port: int = 3773) -> None:
        self.mode = mode.strip().lower()
        self.port = port
        self.tailscale_bin = shutil.which("tailscale") or "tailscale"
        self._fqdn: str | None = None
        self._ipv4: str | None = None

    def start(self, stack: ExitStack) -> tuple[str, str]:
        """Start or configure Tailscale tunnel and register teardown with ExitStack.

        Returns (public_url, advertised_host).
        """
        self._query_status()
        advertised_host = self._fqdn or self._ipv4 or "localhost"

        if self.mode == "serve":
            logger.info(f"Enabling Tailscale Serve on host port {self.port} with MagicDNS HTTPS...")
            try:
                subprocess.run(
                    [self.tailscale_bin, "serve", "--bg", str(self.port)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"Tailscale Serve execution warning: {e}")

            def _cleanup() -> None:
                logger.info("Cleaning up Tailscale Serve...")
                with contextlib.suppress(Exception):
                    subprocess.run(
                        [self.tailscale_bin, "serve", "reset"],
                        check=False,
                        capture_output=True,
                        timeout=5,
                    )

            stack.callback(_cleanup)
            raw_url = f"https://{advertised_host}"
        else:
            raw_url = f"https://{self._ipv4 or advertised_host}"

        public_url = normalize_tunnel_url(raw_url)
        return public_url, advertised_host

    def _query_status(self) -> None:
        try:
            res = subprocess.run(
                [self.tailscale_bin, "status", "--json"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            data = json.loads(res.stdout)
            self_node = data.get("Self", {})
            dns_name = self_node.get("DNSName", "").rstrip(".")
            ips = self_node.get("TailscaleIPs", [])
            ipv4 = next((ip for ip in ips if ":" not in ip), None)
            self._fqdn = dns_name or None
            self._ipv4 = ipv4
        except Exception as e:
            logger.debug(f"Tailscale status query: {e}")


class CloudflareTunnelManager:
    """Manages Cloudflare Tunnel execution (host binary or containerized sidecar)."""

    def __init__(
        self,
        domain: str | None = None,
        quick: bool = False,
        runner: str = "auto",
        keyring_account: str = "CLOUDFLARE_TUNNEL_TOKEN",
        port: int = 3773,
        runtime: str = "podman",
        run_id: str | None = None,
    ) -> None:
        self.domain = domain.strip().lower() if domain else None
        self.quick = quick
        self.runner = runner.strip().lower()
        self.keyring_account = keyring_account
        self.port = port
        self.runtime = runtime
        self.run_id = run_id or "default"
        self._proc: subprocess.Popen | None = None
        self._container_name = f"pi-t3-tunnel-cf-{self.run_id}"

    def start(self, stack: ExitStack) -> tuple[str, str]:
        """Start Cloudflare tunnel process and register teardown with ExitStack.

        Returns (public_url, advertised_host).
        """
        use_host = self.runner == "host" or (self.runner == "auto" and bool(shutil.which("cloudflared")))

        if self.quick:
            return self._start_quick_tunnel(use_host=use_host, stack=stack)
        return self._start_named_tunnel(use_host=use_host, stack=stack)

    def _start_named_tunnel(self, use_host: bool, stack: ExitStack) -> tuple[str, str]:
        token = resolve_tunnel_token(account=self.keyring_account)
        if not token:
            raise TunnelValidationError(f"Tunnel token '{self.keyring_account}' not found in keyring.")

        if use_host:
            cmd = ["cloudflared", "tunnel", "run", "--token", token]
            logger.info(f"Starting host cloudflared tunnel for user-owned domain: {self.domain}...")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stack.callback(self._terminate_proc)
        else:
            cmd = [
                self.runtime,
                "run",
                "--rm",
                "--name",
                self._container_name,
                "--network",
                "host",
                "docker.io/cloudflare/cloudflared:latest",
                "tunnel",
                "run",
                "--token",
                token,
            ]
            logger.info(
                f"Starting containerized cloudflared tunnel ({self.runtime}) for user-owned domain: {self.domain}..."
            )
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stack.callback(self._cleanup_container)

        public_url = normalize_tunnel_url(self.domain or "localhost")
        advertised_host = self.domain or "localhost"
        return public_url, advertised_host

    def _start_quick_tunnel(self, use_host: bool, stack: ExitStack) -> tuple[str, str]:
        import time

        local_target = f"http://127.0.0.1:{self.port}"
        if use_host:
            cmd = ["cloudflared", "tunnel", "--url", local_target]
            logger.info("Starting host cloudflared quick tunnel (trycloudflare.com)...")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stack.callback(self._terminate_proc)
        else:
            cmd = [
                self.runtime,
                "run",
                "--rm",
                "--name",
                self._container_name,
                "--network",
                "host",
                "docker.io/cloudflare/cloudflared:latest",
                "tunnel",
                "--url",
                local_target,
            ]
            logger.info(f"Starting containerized cloudflared quick tunnel ({self.runtime})...")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stack.callback(self._cleanup_container)

        # Parse assigned trycloudflare.com URL from stderr output
        quick_url: str | None = None
        start_time = time.time()
        timeout = 15.0

        if self._proc and self._proc.stderr:
            while time.time() - start_time < timeout:
                line = self._proc.stderr.readline()
                if not line and self._proc.poll() is not None:
                    break
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if match:
                    quick_url = match.group(0)
                    break

        if not quick_url:
            logger.warning("Could not automatically parse trycloudflare.com URL from cloudflared logs.")
            quick_url = "https://your-quick-tunnel.trycloudflare.com"

        public_url = normalize_tunnel_url(quick_url)
        advertised_host = urlparse(public_url).hostname or "localhost"
        return public_url, advertised_host

    def _terminate_proc(self) -> None:
        if self._proc and self._proc.poll() is None:
            logger.info("Stopping cloudflared process...")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()

    def _cleanup_container(self) -> None:
        self._terminate_proc()
        with contextlib.suppress(Exception):
            subprocess.run(
                [self.runtime, "rm", "-f", self._container_name],
                capture_output=True,
                check=False,
                timeout=5,
            )

import json
import subprocess
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from tunnel import (
    CloudflareTunnelManager,
    TailscaleTunnelManager,
    TunnelValidationError,
    format_error_box,
    normalize_tunnel_url,
    resolve_tunnel_token,
    validate_domain_fqdn,
    validate_tunnel_prerequisites,
)


class TestDomainValidation:
    def test_valid_fqdn(self):
        assert validate_domain_fqdn("t3.example.com") is True
        assert validate_domain_fqdn("sub.domain.co.uk") is True
        assert validate_domain_fqdn("agent-dev.my-site.org") is True
        assert validate_domain_fqdn("localhost.localdomain") is True

    def test_invalid_fqdn(self):
        assert validate_domain_fqdn("") is False
        assert validate_domain_fqdn(None) is False
        assert validate_domain_fqdn("localhost") is False
        assert validate_domain_fqdn("https://t3.example.com") is False
        assert validate_domain_fqdn("http://t3.example.com") is False
        assert validate_domain_fqdn("-startdash.example.com") is False
        assert validate_domain_fqdn("enddash-.example.com") is False
        assert validate_domain_fqdn("example..com") is False
        assert validate_domain_fqdn("a" * 255 + ".com") is False


class TestNormalizeTunnelUrl:
    def test_strips_ports_and_enforces_https(self):
        assert normalize_tunnel_url("http://t3.example.com:3773") == "https://t3.example.com"
        assert normalize_tunnel_url("https://t3.example.com:443/") == "https://t3.example.com"
        assert normalize_tunnel_url("http://t3.example.com:8443/pair#token=123") == "https://t3.example.com"
        assert normalize_tunnel_url("t3.example.com") == "https://t3.example.com"
        assert normalize_tunnel_url("https://node.tailnet.ts.net:3773") == "https://node.tailnet.ts.net"
        assert normalize_tunnel_url("http://100.64.0.1:3773") == "https://100.64.0.1"
        assert normalize_tunnel_url("https://100.64.0.1:443") == "https://100.64.0.1"

    def test_ipv6_normalization(self):
        assert normalize_tunnel_url("https://[2001:db8::1]:3773") == "https://[2001:db8::1]"
        assert normalize_tunnel_url("http://[2001:db8::1]:3773") == "https://[2001:db8::1]"
        assert normalize_tunnel_url("http://2001:db8::1") == "https://[2001:db8::1]"
        assert normalize_tunnel_url("2001:db8::1") == "https://[2001:db8::1]"

    def test_case_insensitivity(self):
        assert normalize_tunnel_url("https://T3.MyDomain.COM:3773") == "https://t3.mydomain.com"

    def test_invalid_urls_raise(self):
        with pytest.raises(TunnelValidationError, match="Tunnel URL must be a non-empty string"):
            normalize_tunnel_url("")
        with pytest.raises(TunnelValidationError, match="Tunnel URL must be a non-empty string"):
            normalize_tunnel_url("   ")
        with pytest.raises(TunnelValidationError, match="Tunnel URL must be a non-empty string"):
            normalize_tunnel_url(None)  # type: ignore[arg-type]
        with pytest.raises(TunnelValidationError, match="Invalid tunnel URL"):
            normalize_tunnel_url("https://")
        with pytest.raises(TunnelValidationError, match="Invalid tunnel URL"):
            normalize_tunnel_url("://invalid")


class TestErrorBoxFormatting:
    def test_error_box_structure(self):
        box = format_error_box("Test Error", ["Line 1", "Line 2"])
        assert "STARTUP ERROR: Test Error" in box
        assert "Line 1" in box
        assert "Line 2" in box
        assert "=" * 80 in box


class TestKeyringTokenResolution:
    def test_resolves_from_keyring(self):
        with patch("tunnel.get_keyring_secret", return_value="test_cf_token_123"):
            token = resolve_tunnel_token(account="MY_TUNNEL_TOKEN", service="pi-container")
            assert token == "test_cf_token_123"

    def test_returns_none_if_missing(self):
        with patch("tunnel.get_keyring_secret", return_value=None):
            token = resolve_tunnel_token(account="MISSING_TOKEN")
            assert token is None


class TestTunnelPrerequisitesValidation:
    def test_none_tunnel_passes(self):
        validate_tunnel_prerequisites(None)
        validate_tunnel_prerequisites("")

    def test_unsupported_tunnel_type(self):
        with pytest.raises(TunnelValidationError, match="Unsupported Tunnel Type 'wireguard'"):
            validate_tunnel_prerequisites("wireguard")

    def test_tailscale_missing_binary(self):
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(TunnelValidationError, match="Tailscale is not installed on the host"),
        ):
            validate_tunnel_prerequisites("tailscale")

    def test_tailscale_daemon_down(self):
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stderr = "daemon stopped"
        with (
            patch("shutil.which", return_value="/usr/local/bin/tailscale"),
            patch("subprocess.run", return_value=mock_res),
            pytest.raises(TunnelValidationError, match="Unable to communicate with Tailscale daemon"),
        ):
            validate_tunnel_prerequisites("tailscale")

    def test_tailscale_daemon_logged_out(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = json.dumps({"BackendState": "Stopped"})
        with (
            patch("shutil.which", return_value="/usr/local/bin/tailscale"),
            patch("subprocess.run", return_value=mock_res),
            pytest.raises(TunnelValidationError, match="Tailscale daemon is not authenticated or running"),
        ):
            validate_tunnel_prerequisites("tailscale")

    def test_tailscale_success(self):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = json.dumps({"BackendState": "Running"})
        with (
            patch("shutil.which", return_value="/usr/local/bin/tailscale"),
            patch("subprocess.run", return_value=mock_res),
        ):
            validate_tunnel_prerequisites("tailscale")

    def test_cloudflare_missing_domain(self):
        with pytest.raises(TunnelValidationError, match="Cloudflare Tunnel Domain Missing"):
            validate_tunnel_prerequisites("cloudflare", domain=None, quick=False)

    def test_cloudflare_invalid_domain(self):
        with pytest.raises(TunnelValidationError, match="Invalid Domain Name"):
            validate_tunnel_prerequisites("cloudflare", domain="not-a-domain", quick=False)

    def test_cloudflare_missing_keyring_token(self):
        with (
            patch("tunnel.resolve_tunnel_token", return_value=None),
            pytest.raises(TunnelValidationError, match="Cloudflare Tunnel Token not found in OS Keyring"),
        ):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain="t3.example.com",
                quick=False,
                keyring_account="CLOUDFLARE_TUNNEL_TOKEN",
            )

    def test_cloudflare_invalid_runner(self):
        with (
            patch("tunnel.resolve_tunnel_token", return_value="valid_token"),
            pytest.raises(TunnelValidationError, match="Invalid Tunnel Runner 'invalid'"),
        ):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain="t3.example.com",
                quick=False,
                runner="invalid",
            )

    def test_cloudflare_host_runner_missing_binary(self):
        with (
            patch("tunnel.resolve_tunnel_token", return_value="valid_token"),
            patch("shutil.which", return_value=None),
            pytest.raises(TunnelValidationError, match="Host binary 'cloudflared' not found"),
        ):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain="t3.example.com",
                quick=False,
                runner="host",
            )

    def test_cloudflare_container_runner_missing_runtime(self):
        with (
            patch("tunnel.resolve_tunnel_token", return_value="valid_token"),
            patch("shutil.which", return_value=None),
            pytest.raises(TunnelValidationError, match="Container runtime 'podman' not found"),
        ):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain="t3.example.com",
                quick=False,
                runner="container",
                runtime="podman",
            )

    def test_cloudflare_auto_runner_missing_both(self):
        with (
            patch("tunnel.resolve_tunnel_token", return_value="valid_token"),
            patch("shutil.which", return_value=None),
            pytest.raises(TunnelValidationError, match="Unable to run cloudflared"),
        ):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain="t3.example.com",
                quick=False,
                runner="auto",
                runtime="podman",
            )

    def test_cloudflare_success_named_container_runner(self):
        def mock_which(binary):
            return "/usr/bin/podman" if binary == "podman" else None

        with (
            patch("tunnel.resolve_tunnel_token", return_value="valid_token"),
            patch("shutil.which", side_effect=mock_which),
        ):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain="t3.example.com",
                quick=False,
                runner="auto",
                runtime="podman",
            )

    def test_cloudflare_quick_mode_passes_without_domain_or_token(self):
        def mock_which(binary):
            return "/usr/bin/podman" if binary == "podman" else None

        with patch("shutil.which", side_effect=mock_which):
            validate_tunnel_prerequisites(
                "cloudflare",
                domain=None,
                quick=True,
                runner="auto",
                runtime="podman",
            )


class TestTailscaleTunnelManager:
    def test_tailscale_serve_start_and_cleanup(self):
        mgr = TailscaleTunnelManager(mode="serve", port=3773)
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = json.dumps(
            {
                "Self": {
                    "DNSName": "macbook.tailnet-123.ts.net.",
                    "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
                }
            }
        )

        with patch("subprocess.run", return_value=mock_status) as mock_run:
            with ExitStack() as stack:
                pub_url, adv_host = mgr.start(stack)
                assert pub_url == "https://macbook.tailnet-123.ts.net"
                assert adv_host == "macbook.tailnet-123.ts.net"
                # Check serve --bg call
                calls = [c[0][0] for c in mock_run.call_args_list]
                assert any("serve" in c and "--bg" in c for c in calls)

            # On exit, cleanup should be called (serve reset)
            calls_after = [c[0][0] for c in mock_run.call_args_list]
            assert any("serve" in c and "reset" in c for c in calls_after)

    def test_tailscale_serve_and_cleanup_exceptions_handled(self):
        mgr = TailscaleTunnelManager(mode="serve", port=3773)

        def mock_run_side_effect(cmd, **kwargs):
            if "status" in cmd:
                raise RuntimeError("status error")
            if "serve" in cmd and "--bg" in cmd:
                raise RuntimeError("serve start error")
            if "serve" in cmd and "reset" in cmd:
                raise RuntimeError("serve reset error")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run_side_effect), ExitStack() as stack:
            pub_url, adv_host = mgr.start(stack)
            assert pub_url == "https://localhost"
            assert adv_host == "localhost"

    def test_tailscale_direct_mode(self):
        mgr = TailscaleTunnelManager(mode="direct", port=3773)
        mock_status = MagicMock()
        mock_status.returncode = 0
        mock_status.stdout = json.dumps(
            {
                "Self": {
                    "DNSName": "macbook.tailnet-123.ts.net.",
                    "TailscaleIPs": ["100.64.0.1"],
                }
            }
        )

        with patch("subprocess.run", return_value=mock_status), ExitStack() as stack:
            pub_url, adv_host = mgr.start(stack)
            assert pub_url == "https://100.64.0.1"
            assert adv_host == "macbook.tailnet-123.ts.net"


class TestCloudflareTunnelManager:
    def test_named_tunnel_host_runner(self):
        mgr = CloudflareTunnelManager(
            domain="t3.yourdomain.com",
            quick=False,
            runner="host",
            keyring_account="CLOUDFLARE_TUNNEL_TOKEN",
            port=3773,
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with (
            patch("tunnel.resolve_tunnel_token", return_value="tok_123"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            with ExitStack() as stack:
                pub_url, adv_host = mgr.start(stack)
                assert pub_url == "https://t3.yourdomain.com"
                assert adv_host == "t3.yourdomain.com"
                mock_popen.assert_called_once_with(
                    ["cloudflared", "tunnel", "run", "--token", "tok_123"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            mock_proc.terminate.assert_called_once()

    def test_named_tunnel_container_runner(self):
        mgr = CloudflareTunnelManager(
            domain="t3.yourdomain.com",
            quick=False,
            runner="container",
            keyring_account="CLOUDFLARE_TUNNEL_TOKEN",
            port=3773,
            runtime="podman",
            run_id="abc12345",
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with (
            patch("tunnel.resolve_tunnel_token", return_value="tok_456"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("subprocess.run") as mock_run,
        ):
            with ExitStack() as stack:
                pub_url, adv_host = mgr.start(stack)
                assert pub_url == "https://t3.yourdomain.com"
                assert adv_host == "t3.yourdomain.com"
                mock_popen.assert_called_once_with(
                    [
                        "podman",
                        "run",
                        "--rm",
                        "--name",
                        "pi-t3-tunnel-cf-abc12345",
                        "--network",
                        "host",
                        "docker.io/cloudflare/cloudflared:latest",
                        "tunnel",
                        "run",
                        "--token",
                        "tok_456",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            mock_proc.terminate.assert_called_once()
            mock_run.assert_called_once_with(
                ["podman", "rm", "-f", "pi-t3-tunnel-cf-abc12345"],
                capture_output=True,
                check=False,
                timeout=5,
            )

    def test_quick_tunnel_parses_url(self):
        mgr = CloudflareTunnelManager(
            domain=None,
            quick=True,
            runner="host",
            port=3773,
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.readline.side_effect = [
            "INF Starting tunnel...\n",
            "INF Your quick tunnel is created: https://bravo-charlie.trycloudflare.com\n",
        ]

        with patch("subprocess.Popen", return_value=mock_proc), ExitStack() as stack:
            pub_url, adv_host = mgr.start(stack)
            assert pub_url == "https://bravo-charlie.trycloudflare.com"
            assert adv_host == "bravo-charlie.trycloudflare.com"

    def test_named_tunnel_missing_token_raises(self):
        mgr = CloudflareTunnelManager(domain="t3.example.com", quick=False)
        with (
            patch("tunnel.resolve_tunnel_token", return_value=None),
            ExitStack() as stack,
            pytest.raises(TunnelValidationError, match="Tunnel token 'CLOUDFLARE_TUNNEL_TOKEN' not found"),
        ):
            mgr.start(stack)

    def test_quick_tunnel_container_mode(self):
        mgr = CloudflareTunnelManager(
            domain=None,
            quick=True,
            runner="container",
            port=3773,
            runtime="podman",
            run_id="quick99",
        )
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr.readline.side_effect = [
            "INF Quick tunnel ready: https://charlie-delta.trycloudflare.com\n",
        ]

        with (
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("subprocess.run"),
            ExitStack() as stack,
        ):
            pub_url, adv_host = mgr.start(stack)
            assert pub_url == "https://charlie-delta.trycloudflare.com"
            assert adv_host == "charlie-delta.trycloudflare.com"
            cmd = mock_popen.call_args[0][0]
            assert "podman" in cmd
            assert "pi-t3-tunnel-cf-quick99" in cmd

    def test_quick_tunnel_url_not_found_fallback(self):
        mgr = CloudflareTunnelManager(
            domain=None,
            quick=True,
            runner="host",
            port=3773,
        )
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr.readline.side_effect = ["Starting...\n", ""]

        with patch("subprocess.Popen", return_value=mock_proc), ExitStack() as stack:
            pub_url, adv_host = mgr.start(stack)
            assert pub_url == "https://your-quick-tunnel.trycloudflare.com"

    def test_proc_terminate_timeout_calls_kill(self):
        mgr = CloudflareTunnelManager(domain="t3.example.com", quick=False, runner="host")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 3)

        with (
            patch("tunnel.resolve_tunnel_token", return_value="tok_123"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            with ExitStack() as stack:
                mgr.start(stack)
            mock_proc.kill.assert_called_once()

    def test_cleanup_container_exception_handled(self):
        mgr = CloudflareTunnelManager(domain="t3.example.com", quick=False, runner="container", runtime="podman")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with (
            patch("tunnel.resolve_tunnel_token", return_value="tok_123"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("subprocess.run", side_effect=RuntimeError("podman rm error")),
            ExitStack() as stack,
        ):
            mgr.start(stack)
            # Exception in container cleanup is caught and suppressed

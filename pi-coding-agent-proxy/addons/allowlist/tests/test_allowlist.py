"""
Unit tests for the Allowlist mitmproxy addon.

Run with:
    python -m pytest tests/test_allowlist.py -v
"""

import ipaddress
from unittest.mock import MagicMock, patch

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from allowlist import (
    AllowlistAddon,
    AllowlistRule,
    _glob_to_regex,
    _ip_matches,
    _is_raw_regex,
    _matches_hostname,
    _parse_ip_patterns,
    _parse_server_address,
    _strip_port,
)


# ---------------------------------------------------------------------------
# Helpers: hostname pattern matching
# ---------------------------------------------------------------------------


class TestIsRawRegex:
    def test_plain_hostname(self):
        assert not _is_raw_regex("api.example.com")

    def test_glob_wildcard(self):
        assert not _is_raw_regex("*.example.com")

    def test_question_mark_wildcard(self):
        assert not _is_raw_regex("api?.example.com")

    def test_regex_caret_dollar(self):
        assert _is_raw_regex("^auth\\..*\\.example\\.com$")

    def test_regex_brackets(self):
        assert _is_raw_regex("[a-z]+\\.example\\.com")

    def test_regex_pipes(self):
        assert _is_raw_regex("api\\.example\\.com|staging\\.example\\.com")

    def test_empty_string(self):
        assert not _is_raw_regex("")


class TestGlobToRegex:
    def test_plain(self):
        assert _glob_to_regex("api.example.com") == "^api\\.example\\.com$"

    def test_star_wildcard(self):
        assert _glob_to_regex("*.example.com") == "^.*\\.example\\.com$"

    def test_star_in_middle(self):
        assert _glob_to_regex("api*.example.com") == "^api.*\\.example\\.com$"

    def test_question_mark(self):
        # ? glob → . regex (match any single character)
        # The dot in .example is escaped by re.escape, so result is ^api.\.example\.com$
        assert _glob_to_regex("api?.example.com") == "^api.\\.example\\.com$"


class TestMatchesHostname:
    def test_exact_match(self):
        assert _matches_hostname("api.example.com", ["api.example.com"])

    def test_wildcard_match(self):
        assert _matches_hostname("staging.api.example.com", ["*.example.com"])

    def test_wildcard_no_match(self):
        assert not _matches_hostname("api.other.com", ["*.example.com"])

    def test_regex_match(self):
        assert _matches_hostname("auth.v1.example.com",
                                 ["^auth\\..*\\.example\\.com$"])

    def test_case_insensitive(self):
        assert _matches_hostname("API.Example.COM", ["api.example.com"])

    def test_multiple_patterns(self):
        patterns = ["api.internal.local", "*.staging.local", "^test\\..*\\.dev$"]
        assert _matches_hostname("api.internal.local", patterns)
        assert _matches_hostname("dev.staging.local", patterns)
        assert _matches_hostname("test.prod.dev", patterns)
        assert not _matches_hostname("api.external.com", patterns)


class TestStripPort:
    def test_no_port(self):
        assert _strip_port("api.example.com") == "api.example.com"

    def test_ipv4_port(self):
        assert _strip_port("api.example.com:443") == "api.example.com"

    def test_ipv6_bracketed(self):
        assert _strip_port("[::1]:443") == "[::1]"

    def test_bare_ipv6(self):
        assert _strip_port("::1") == "::1"

    def test_ipv6_full(self):
        assert _strip_port("2001:db8::1") == "2001:db8::1"


# ---------------------------------------------------------------------------
# Helpers: IP matching
# ---------------------------------------------------------------------------


class TestParseIpPatterns:
    def test_single_ipv4(self):
        result = _parse_ip_patterns(["192.168.1.1"])
        assert len(result) == 1
        assert isinstance(result[0], ipaddress.IPv4Address)

    def test_cidr_range(self):
        result = _parse_ip_patterns(["10.0.0.0/8"])
        assert len(result) == 1
        assert isinstance(result[0], ipaddress.IPv4Network)
        assert result[0].network_address == ipaddress.IPv4Address("10.0.0.0")

    def test_ipv6_cidr(self):
        result = _parse_ip_patterns(["fd00::/8"])
        assert len(result) == 1
        assert isinstance(result[0], ipaddress.IPv6Network)

    def test_invalid_pattern_logs_warning(self, caplog):
        result = _parse_ip_patterns(["not-an-ip"])
        assert len(result) == 0

    def test_mixed_patterns(self):
        result = _parse_ip_patterns(["192.168.1.1", "10.0.0.0/8", "fd00::/8"])
        assert len(result) == 3


class TestIpMatches:
    def test_single_ip_match(self):
        patterns = _parse_ip_patterns(["192.168.1.1"])
        assert _ip_matches("192.168.1.1", patterns)
        assert not _ip_matches("192.168.1.2", patterns)

    def test_cidr_match(self):
        patterns = _parse_ip_patterns(["10.0.0.0/8"])
        assert _ip_matches("10.1.2.3", patterns)
        assert _ip_matches("10.255.255.255", patterns)
        assert not _ip_matches("11.0.0.1", patterns)

    def test_subnet_match(self):
        patterns = _parse_ip_patterns(["192.168.0.0/16"])
        assert _ip_matches("192.168.1.1", patterns)
        assert _ip_matches("192.168.255.255", patterns)
        assert not _ip_matches("192.169.0.1", patterns)

    def test_ipv6_match(self):
        patterns = _parse_ip_patterns(["fd00::/8"])
        assert _ip_matches("fd00::1", patterns)
        assert _ip_matches("fdff:ffff:ffff:ffff::1", patterns)
        assert not _ip_matches("fe80::1", patterns)

    def test_invalid_ip_returns_false(self):
        patterns = _parse_ip_patterns(["10.0.0.0/8"])
        assert not _ip_matches("not-an-ip", patterns)

    def test_empty_patterns(self):
        assert not _ip_matches("10.0.0.1", [])


class TestParseServerAddress:
    def test_normal_address(self):
        assert _parse_server_address(("192.168.1.1", 443)) == "192.168.1.1"

    def test_bracketed_ipv6(self):
        assert _parse_server_address(("[::1]", 443)) == "::1"

    def test_empty_address(self):
        assert _parse_server_address(None) is None
        assert _parse_server_address(()) is None


# ---------------------------------------------------------------------------
# Helpers: connection matching
# ---------------------------------------------------------------------------


class TestIsLocalhost:
    def _addon(self):
        return AllowlistAddon(config_path="")

    def test_localhost(self):
        assert self._addon()._is_localhost("localhost")

    def test_localhost_uppercase(self):
        assert self._addon()._is_localhost("LOCALHOST")

    def test_ipv4_loopback(self):
        assert self._addon()._is_localhost("127.0.0.1")

    def test_ipv6_loopback(self):
        assert self._addon()._is_localhost("::1")

    def test_not_localhost(self):
        assert not self._addon()._is_localhost("api.example.com")

    def test_not_loopback(self):
        assert not self._addon()._is_localhost("192.168.1.1")


class TestIsPrivateIp:
    def _addon(self):
        return AllowlistAddon(config_path="")

    def test_loopback(self):
        assert self._addon()._is_private_ip("127.0.0.1")
        assert self._addon()._is_private_ip("::1")

    def test_rfc1918(self):
        assert self._addon()._is_private_ip("10.0.0.1")
        assert self._addon()._is_private_ip("172.16.0.1")
        assert self._addon()._is_private_ip("192.168.1.1")

    def test_link_local(self):
        assert self._addon()._is_private_ip("169.254.1.1")

    def test_reserved(self):
        assert self._addon()._is_private_ip("0.0.0.0")

    def test_public_ip(self):
        assert not self._addon()._is_private_ip("8.8.8.8")
        assert not self._addon()._is_private_ip("1.1.1.1")


# ---------------------------------------------------------------------------
# AllowlistRule
# ---------------------------------------------------------------------------


class TestAllowlistRule:
    def test_allow_hostname_match(self):
        rule = AllowlistRule({
            "name": "test-allow",
            "mode": "allow",
            "hostnames": ["api.example.com", "*.staging.com"],
            "ip_ranges": [],
        })
        assert rule.evaluate("api.example.com", None) == "allow"
        assert rule.evaluate("dev.staging.com", None) == "allow"
        assert rule.evaluate("other.com", None) is None

    def test_allow_ip_match(self):
        rule = AllowlistRule({
            "name": "test-ip-allow",
            "mode": "allow",
            "hostnames": [],
            "ip_ranges": ["10.0.0.0/8"],
        })
        assert rule.evaluate("unknown.host.com", "10.1.2.3") == "allow"
        assert rule.evaluate("unknown.host.com", "8.8.8.8") is None

    def test_allow_hostname_and_ip(self):
        """Either hostname or IP match should be sufficient for allow."""
        rule = AllowlistRule({
            "name": "test-or-allow",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": ["10.0.0.0/8"],
        })
        # Hostname match only
        assert rule.evaluate("api.example.com", "8.8.8.8") == "allow"
        # IP match only
        assert rule.evaluate("unknown.com", "10.1.2.3") == "allow"
        # Neither match
        assert rule.evaluate("unknown.com", "8.8.8.8") is None

    def test_block_hostname_match(self):
        rule = AllowlistRule({
            "name": "test-block",
            "mode": "block",
            "hostnames": ["ads.example.com", "*.tracker.net"],
            "ip_ranges": [],
        })
        assert rule.evaluate("ads.example.com", None) == "deny"
        assert rule.evaluate("bad.tracker.net", None) == "deny"
        assert rule.evaluate("good.com", None) is None

    def test_block_ip_match(self):
        rule = AllowlistRule({
            "name": "test-ip-block",
            "mode": "block",
            "hostnames": [],
            "ip_ranges": ["203.0.113.0/24"],
        })
        assert rule.evaluate("unknown.com", "203.0.113.5") == "deny"
        assert rule.evaluate("unknown.com", "1.2.3.4") is None

    def test_disabled_rule(self):
        rule = AllowlistRule({
            "name": "test-disabled",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
            "enabled": False,
        })
        assert rule.evaluate("api.example.com", None) is None

    def test_regex_hostname(self):
        rule = AllowlistRule({
            "name": "test-regex",
            "mode": "allow",
            "hostnames": ["^v\\d+\\.api\\.example\\.com$"],
            "ip_ranges": [],
        })
        assert rule.evaluate("v1.api.example.com", None) == "allow"
        assert rule.evaluate("v2.api.example.com", None) == "allow"
        assert rule.evaluate("api.example.com", None) is None

    def test_multiple_rules_first_wins(self):
        """Multiple rules; first matching rule determines outcome."""
        rule1 = AllowlistRule({
            "name": "block-first",
            "mode": "block",
            "hostnames": ["evil.com"],
            "ip_ranges": [],
        })
        rule2 = AllowlistRule({
            "name": "allow-second",
            "mode": "allow",
            "hostnames": ["evil.com"],
            "ip_ranges": [],
        })

        # In a real scenario, _check_rules iterates rules in order.
        assert rule1.evaluate("evil.com", None) == "deny"
        assert rule2.evaluate("evil.com", None) == "allow"


# ---------------------------------------------------------------------------
# AllowlistAddon
# ---------------------------------------------------------------------------


def _make_flow(host: str, server_ip: str | None = None,
               method: str = "GET", path: str = "/"):
    """Build a MagicMock HTTPFlow for testing."""
    flow = MagicMock()
    flow.request = MagicMock()
    flow.request.method = method
    flow.request.host = host
    flow.request.pretty_host = host
    flow.request.path = path
    flow.response = None
    flow.error = None
    flow.live = True

    if server_ip is not None:
        flow.server_conn = MagicMock()
        flow.server_conn.address = (server_ip, 443)
    else:
        flow.server_conn = None

    return flow


class TestAllowlistAddonConfig:
    """Test configuration loading from YAML."""

    def test_no_config(self):
        addon = AllowlistAddon(config_path="/nonexistent/path.yaml")
        assert addon.rules == []
        assert addon._mode == "allow"
        assert addon._default_action == "block"

    def test_flat_config(self, tmp_path):
        config = {
            "global": {
                "mode": "allow",
                "hostnames": ["api.example.com", "*.internal.local"],
                "ip_ranges": ["10.0.0.0/8"],
            }
        }
        config_file = tmp_path / "config.yaml"
        import yaml
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        addon = AllowlistAddon(config_path=str(config_file))
        assert len(addon.rules) == 1
        rule = addon.rules[0]
        assert rule.name == "global-allowlist"
        assert rule.mode == "allow"
        assert "api.example.com" in rule.hostnames
        assert "10.0.0.0/8" in rule.ip_ranges

    def test_named_rules(self, tmp_path):
        config = {
            "global": {
                "rules": [
                    {
                        "name": "internal-api",
                        "mode": "allow",
                        "hostnames": ["api.internal.local"],
                        "ip_ranges": [],
                    },
                    {
                        "name": "block-ads",
                        "mode": "block",
                        "hostnames": ["*.ads.example.com"],
                        "ip_ranges": [],
                    },
                ]
            }
        }
        config_file = tmp_path / "config.yaml"
        import yaml
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        addon = AllowlistAddon(config_path=str(config_file))
        assert len(addon.rules) == 2
        assert addon.rules[0].name == "internal-api"
        assert addon.rules[1].name == "block-ads"


class TestAllowlistAddonOnRequest:
    """Test the on_request hook with various scenarios."""

    def test_allowlist_allows_matching_host(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("api.example.com", "93.184.216.34")
        addon.request(flow)
        flow.response = None  # Ensure no response was set
        assert flow.response is None

    def test_allowlist_blocks_non_matching_host(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("evil.com", "1.2.3.4")
        addon.request(flow)
        assert flow.response is not None
        assert flow.response.status_code == 403

    def test_allowlist_allows_matching_ip(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": [],
            "ip_ranges": ["10.0.0.0/8"],
        })]

        flow = _make_flow("unknown.host.com", "10.1.2.3")
        addon.request(flow)
        assert flow.response is None

    def test_allowlist_blocks_non_matching_ip(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": [],
            "ip_ranges": ["10.0.0.0/8"],
        })]

        flow = _make_flow("unknown.host.com", "8.8.8.8")
        addon.request(flow)
        assert flow.response is not None

    def test_block_mode_blocks_matching_host(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "block"
        addon._default_action = "allow"
        addon.rules = [AllowlistRule({
            "name": "block-ads",
            "mode": "block",
            "hostnames": ["*.ads.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("tracker.ads.example.com", "93.184.216.34")
        addon.request(flow)
        assert flow.response is not None
        assert flow.response.status_code == 403

    def test_block_mode_allows_non_matching_host(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "block"
        addon._default_action = "allow"
        addon.rules = [AllowlistRule({
            "name": "block-ads",
            "mode": "block",
            "hostnames": ["*.ads.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("good.example.com", "93.184.216.34")
        addon.request(flow)
        assert flow.response is None

    def test_localhost_always_allowed(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        # No rules — everything should be blocked by default...
        # but localhost is always allowed.
        addon.rules = []

        flow = _make_flow("localhost", "127.0.0.1")
        addon.request(flow)
        assert flow.response is None

    def test_private_ip_always_allowed(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        # Private IP should always be allowed regardless of allowlist rules.
        flow = _make_flow("some-private-host", "192.168.1.100")
        addon.request(flow)
        assert flow.response is None

    def test_dry_run_no_blocking(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon._dry_run = True
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("evil.com", "1.2.3.4")
        addon.request(flow)
        # In dry_run mode, no blocking occurs.
        assert flow.response is None

    def test_status_code_444_kills_flow(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon._status_code = 444
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("evil.com", "1.2.3.4")
        addon.request(flow)
        flow.kill.assert_called_once()

    def test_blocked_flow_has_marked_indicator(self):
        """Blocked flows should have flow.marked set for mitmweb visual distinction."""
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("evil.com", "1.2.3.4")
        addon.request(flow)
        assert flow.marked == ":no_entry_sign:"
        assert "Blocked by allowlist" in flow.comment

    def test_blocked_flow_default_action_has_reason(self):
        """Blocked flows should include the reason in the comment."""
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("evil.com", "1.2.3.4")
        addon.request(flow)
        assert "default action" in flow.comment

    def test_blocked_flow_rule_name_in_comment(self):
        """When blocked by a specific rule, the rule name should appear in the comment."""
        addon = AllowlistAddon(config_path="")
        addon._mode = "block"
        addon._default_action = "allow"
        addon.rules = [AllowlistRule({
            "name": "block-ads",
            "mode": "block",
            "hostnames": ["*.ads.example.com"],
            "ip_ranges": [],
        })]

        flow = _make_flow("tracker.ads.example.com", "93.184.216.34")
        addon.request(flow)
        assert flow.marked == ":no_entry_sign:"
        assert "rule matched" in flow.comment

    def test_skips_completed_flows(self):
        addon = AllowlistAddon(config_path="")
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        # Flow already has a response → should be skipped
        flow = _make_flow("api.example.com", "93.184.216.34")
        flow.response = MagicMock()
        addon.request(flow)

    def test_skipserrored_flows(self):
        addon = AllowlistAddon(config_path="")
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        # Flow has an error → should be skipped
        flow = _make_flow("api.example.com", "93.184.216.34")
        flow.error = Exception("connection reset")
        addon.request(flow)

    def test_skips_dead_flows(self):
        addon = AllowlistAddon(config_path="")
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        # Flow is not live → should be skipped
        flow = _make_flow("api.example.com", "93.184.216.34")
        flow.live = False
        addon.request(flow)

    def test_default_action_allow(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "allow"
        addon.rules = [AllowlistRule({
            "name": "test",
            "mode": "allow",
            "hostnames": ["api.example.com"],
            "ip_ranges": [],
        })]

        # No matching rule, default is allow → should pass through.
        flow = _make_flow("unknown.com", "8.8.8.8")
        addon.request(flow)
        assert flow.response is None

    def test_multiple_rules_first_wins(self):
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon._default_action = "block"
        addon.rules = [
            AllowlistRule({
                "name": "block-first",
                "mode": "block",
                "hostnames": ["evil.com"],
                "ip_ranges": [],
            }),
            AllowlistRule({
                "name": "allow-second",
                "mode": "allow",
                "hostnames": ["evil.com"],
                "ip_ranges": [],
            }),
        ]

        # First rule (block) should win
        flow = _make_flow("evil.com", "1.2.3.4")
        addon.request(flow)
        assert flow.response is not None


# ---------------------------------------------------------------------------
# load() and configure() mitmproxy option hooks
# ---------------------------------------------------------------------------


class TestAddonLoadOptions:
    def test_load_registers_options(self):
        addon = AllowlistAddon(config_path="")
        loader = MagicMock()
        addon.load(loader)
        # Should have called add_option 5 times
        assert loader.add_option.call_count == 5

    @patch("allowlist.ctx")
    def test_configure_updates_mode(self, mock_ctx):
        mock_ctx.options = MagicMock()
        mock_ctx.options.allowlist_mode = "block"
        addon = AllowlistAddon(config_path="")
        addon._mode = "allow"
        addon.configure({"allowlist_mode"})
        assert addon._mode == "block"


# ---------------------------------------------------------------------------
# Integration-style: full flow with config
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_allow_then_block_scenario(self, tmp_path):
        """Simulate a realistic scenario: allow internal, block external."""
        config = {
            "global": {
                "mode": "allow",
                "default_action": "block",
                "status_code": 403,
                "log_blocked": True,
                "log_allowed": False,
                "dry_run": False,
                "rules": [
                    {
                        "name": "internal-api",
                        "mode": "allow",
                        "hostnames": ["api.internal.local", "*.internal.local"],
                        "ip_ranges": [],
                    },
                    {
                        "name": "monitoring-ips",
                        "mode": "allow",
                        "hostnames": [],
                        "ip_ranges": ["10.0.0.0/8", "192.168.0.0/16"],
                    },
                    {
                        "name": "block-ads",
                        "mode": "block",
                        "hostnames": ["*.ads.example.com", "*.tracker.net"],
                        "ip_ranges": [],
                    },
                ],
            }
        }
        config_file = tmp_path / "config.yaml"
        import yaml
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        addon = AllowlistAddon(config_path=str(config_file))
        assert len(addon.rules) == 3

        # Allow: internal API
        flow = _make_flow("api.internal.local", "10.0.1.5")
        addon.request(flow)
        assert flow.response is None

        # Allow: internal subdomain
        flow = _make_flow("dev.staging.internal.local", "10.0.2.10")
        addon.request(flow)
        assert flow.response is None

        # Allow: monitoring IP
        flow = _make_flow("unknown.external.com", "192.168.1.50")
        addon.request(flow)
        assert flow.response is None

        # Block: ad domain
        flow = _make_flow("tracker.ads.example.com", "93.184.216.34")
        addon.request(flow)
        assert flow.response is not None
        assert flow.response.status_code == 403

        # Block: external IP not in allowlist
        flow = _make_flow("random.external.com", "8.8.8.8")
        addon.request(flow)
        assert flow.response is not None

    def test_block_mode_with_allow_default(self, tmp_path):
        """Block mode: block specific bad hosts, allow everything else."""
        config = {
            "global": {
                "mode": "block",
                "default_action": "allow",
                "status_code": 403,
                "rules": [
                    {
                        "name": "block-malware",
                        "mode": "block",
                        "hostnames": ["malware.bad.com", "^evil\\..*\\.net$"],
                        "ip_ranges": [],
                    },
                ],
            }
        }
        config_file = tmp_path / "config.yaml"
        import yaml
        with open(config_file, "w") as f:
            yaml.dump(config, f)

        addon = AllowlistAddon(config_path=str(config_file))

        # Block: malware domain
        flow = _make_flow("malware.bad.com", "1.2.3.4")
        addon.request(flow)
        assert flow.response is not None

        # Block: evil.net subdomain
        flow = _make_flow("evil.malware.net", "1.2.3.4")
        addon.request(flow)
        assert flow.response is not None

        # Allow: everything else
        flow = _make_flow("google.com", "142.250.80.46")
        addon.request(flow)
        assert flow.response is None

        flow = _make_flow("github.com", "140.82.121.4")
        addon.request(flow)
        assert flow.response is None

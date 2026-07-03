"""
mitmproxy addon: Allowlist

Filters HTTP traffic so that only requests to allowlisted domains and/or
IP address ranges are permitted through the proxy. All other connections
are blocked (with an HTTP 403 by default, or connection close via 444).

This addon supports two modes:
  - ``allow`` (default): only allowlisted hosts/IPs are permitted; everything
    else is blocked.
  - ``block``: only blocked hosts/IPs are denied; everything else is allowed.

Hostname patterns support:
  - Plain hostnames (e.g. ``api.example.com``)
  - Glob-style wildcards (e.g. ``*.example.com``, ``api*.internal.net``)
  - Full regular expressions (e.g. ``^auth\\..*\\.example\\.com$``)

IP patterns support:
  - Single IP addresses (e.g. ``192.168.1.1``)
  - CIDR ranges (e.g. ``10.0.0.0/8``, ``192.168.0.0/16``)
  - IPv6 addresses and CIDR ranges (e.g. ``fd00::/8``)

Both hostname and IP patterns can be combined in a single rule. If both
``hostnames`` and ``ip_ranges`` are specified, a request must match at
least one hostname pattern AND at least one IP pattern to be allowed
(AND logic). If only one of the two is specified, that criterion alone
determines allowance.

Example loading:
    mitmweb --set scripts=allowlist.py

Configuration file (YAML):
    See allowlist_config.yaml.
"""

import ipaddress
import logging
import os
import re

import yaml
from mitmproxy import ctx, http

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers: hostname pattern matching
# ---------------------------------------------------------------------------


def _is_raw_regex(pattern: str) -> bool:
    """Detect whether a pattern is a full regex vs a glob.

    A pattern is treated as a raw regex if it contains any regex metacharacter
    other than ``*`` and ``?`` (i.e. ``^ $ + { } [ ] ( ) |``).
    """
    regex_metachars = set(r"^$+{}[]()|")
    return any(c in regex_metachars for c in pattern)


def _glob_to_regex(pattern: str) -> str:
    """Convert a glob-style pattern to a regex string.

    ``*`` → ``.*`` (match any number of characters)
    ``?`` → ``.`` (match exactly one character)
    """
    regex_pattern = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return regex_pattern


def _matches_hostname(hostname: str, patterns: list[str]) -> bool:
    """Check if a hostname matches any of the patterns.

    Args:
        hostname: The hostname to check (without port).
        patterns: List of glob or regex patterns.

    Returns:
        True if the hostname matches at least one pattern.
    """
    for pattern in patterns:
        if _is_raw_regex(pattern):
            if re.search(pattern, hostname, re.IGNORECASE):
                return True
        else:
            regex_pattern = _glob_to_regex(pattern)
            if re.match(regex_pattern, hostname, re.IGNORECASE):
                return True
    return False


# ---------------------------------------------------------------------------
# Helpers: IP matching
# ---------------------------------------------------------------------------


def _parse_ip_patterns(patterns: list[str]) -> list:
    """Parse IP address / CIDR patterns into ``ipaddress`` objects.

    Returns a list where each element is either an
    ``ipaddress.IPv4Network`` / ``ipaddress.IPv6Network`` (for CIDR ranges)
    or an ``ipaddress.IPv4Address`` / ``ipaddress.IPv6Address`` (for single
    IPs).
    """
    parsed = []
    for pattern in patterns:
        pattern = pattern.strip()
        try:
            if "/" in pattern:
                parsed.append(ipaddress.ip_network(pattern, strict=False))
            else:
                parsed.append(ipaddress.ip_address(pattern))
        except ValueError as e:
            log.warning(f"[allowlist] Invalid IP pattern '{pattern}': {e}")
    return parsed


def _ip_matches(ip_str: str, parsed_patterns: list) -> bool:
    """Check if an IP address matches any of the parsed IP patterns.

    Args:
        ip_str: IP address string (e.g. ``"192.168.1.1"``).
        parsed_patterns: List of ``ipaddress`` objects from ``_parse_ip_patterns``.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    for pattern in parsed_patterns:
        if isinstance(pattern, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            # For IPv4-mapped IPv6 networks, also check the IPv4 version.
            if isinstance(addr, ipaddress.IPv4Address):
                if pattern.version == 4 and addr in pattern:
                    return True
            elif isinstance(addr, ipaddress.IPv6Address):
                if pattern.version == 6 and addr in pattern:
                    return True
                # Check IPv4-mapped IPv6
                if pattern.version == 6 and addr.ipv4_mapped and ipaddress.ip_network(pattern).version == 4:
                    return False  # Handled by IPv4 check above
        else:
            # Single IP comparison (also covers IPv4-mapped IPv6)
            if addr == pattern:
                return True
            # For IPv6 addresses that are IPv4-mapped, compare against
            # IPv4 patterns.
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped and addr.ipv4_mapped == pattern:
                return True
    return False


def _parse_server_address(address) -> str | None:
    """Extract IP address string from a mitmproxy server address tuple.

    Returns the IP string or None if the address is unavailable.
    """
    if not address or len(address) < 1:
        return None
    ip = address[0]
    # Handle bracketed IPv6: [::1]:port or [::1] → strip brackets
    if ip.startswith("["):
        bracket_end = ip.find("]")
        if bracket_end != -1:
            return ip[1:bracket_end]
        return ip[1:]
    return ip


# ---------------------------------------------------------------------------
# Helpers: connection matching
# ---------------------------------------------------------------------------


def _get_server_ip(flow) -> str | None:
    """Extract server IP from a flow.

    Returns the server IP string or None if unavailable.
    """
    if flow and flow.server_conn and flow.server_conn.address:
        return _parse_server_address(flow.server_conn.address)
    return None


def _strip_port(hostname: str) -> str:
    """Strip trailing port from hostname."""
    if hostname.startswith("["):
        bracket_end = hostname.find("]")
        if bracket_end != -1 and bracket_end + 1 < len(hostname) and hostname[bracket_end + 1] == ":":
            return hostname[: bracket_end + 1]
        return hostname
    if hostname.count(":") > 1:
        return hostname
    if ":" in hostname:
        return hostname.rsplit(":", 1)[0]
    return hostname


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------


class AllowlistRule:
    """A single allowlist rule.

    A rule defines a set of hostname patterns and/or IP patterns. A request
    matches this rule if (depending on ``mode``):
      - In ``allow`` mode: the request's hostname matches a hostname pattern
        AND/OR the request's server IP matches an IP pattern.
      - In ``block`` mode: the request's hostname matches a hostname pattern
        OR the request's server IP matches an IP pattern → deny.
    """

    def __init__(self, rule_def: dict):
        self.name: str = rule_def.get("name", "<unnamed>")
        self.mode: str = rule_def.get("mode", "allow")  # "allow" or "block"
        self.hostnames: list[str] = rule_def.get("hostnames", [])
        self.ip_ranges: list[str] = rule_def.get("ip_ranges", [])

        # Pre-parse IP patterns for efficiency
        self._parsed_ips: list = _parse_ip_patterns(self.ip_ranges)

        # Pre-compile hostname regex patterns
        self._hostname_regexes = []
        for h in self.hostnames:
            if _is_raw_regex(h):
                self._hostname_regexes.append((h, True))
            else:
                self._hostname_regexes.append((_glob_to_regex(h), False))

        self.enabled: bool = rule_def.get("enabled", True)

    def __repr__(self) -> str:
        return f"AllowlistRule(name={self.name!r}, mode={self.mode!r}, hostnames={len(self.hostnames)}, ip_ranges={len(self.ip_ranges)})"

    def _hostname_matches(self, hostname: str) -> bool:
        """Check if hostname matches any hostname pattern in this rule."""
        for pattern, is_regex in self._hostname_regexes:
            if is_regex:
                if re.search(pattern, hostname, re.IGNORECASE):
                    return True
            else:
                if re.match(pattern, hostname, re.IGNORECASE):
                    return True
        return False

    def _ip_matches_rule(self, ip_str: str) -> bool:
        """Check if an IP matches any IP pattern in this rule."""
        return _ip_matches(ip_str, self._parsed_ips)

    def evaluate(self, hostname: str, server_ip: str | None) -> str | None:
        """Evaluate this rule against a request.

        Returns:
            ``"allow"`` if the request should be allowed,
            ``"deny"`` if the request should be blocked,
            ``None`` if this rule does not match (skip to next rule).

        In ``allow`` mode:
            - If hostname matches → "allow"
            - If IP matches → "allow"
            - Otherwise → None (no match, continue checking)

        In ``block`` mode:
            - If hostname matches → "deny"
            - If IP matches → "deny"
            - Otherwise → None (no match, continue checking)
        """
        if not self.enabled:
            return None

        hostname_match = self._hostname_matches(hostname)
        ip_match = server_ip is not None and self._ip_matches_rule(server_ip)

        if self.mode == "allow":
            # If the request matches this allowlist rule on any dimension,
            # it's allowed.
            if hostname_match or ip_match:
                return "allow"
        elif self.mode == "block" and (hostname_match or ip_match):
            # If the request matches this blocklist rule on any dimension,
            # it's denied.
            return "deny"

        return None


# ---------------------------------------------------------------------------
# Main addon class
# ---------------------------------------------------------------------------


class AllowlistAddon:
    """
    mitmproxy addon that filters HTTP traffic based on an allowlist or blocklist
    of domains and IP addresses.
    """

    def __init__(self, config_path: str = ""):
        self.rules: list[AllowlistRule] = []
        self.global_config: dict = {}
        self._mode: str = "allow"
        self._default_action: str = "block"
        self._status_code: int = 403
        self._log_blocked: bool = True
        self._log_allowed: bool = False
        self._dry_run: bool = False
        self._load_config(config_path)

    def __repr__(self) -> str:
        return f"AllowlistAddon(rules={len(self.rules)}, mode={self._mode!r})"

    def load(self, loader):
        """Register custom mitmproxy options so users can override defaults
        via ``--set`` on the command line without modifying the config file.
        """
        loader.add_option(
            "allowlist_mode",
            str,
            self._mode,
            """
            Allowlist operating mode. ``allow`` means only allowlisted hosts/IPs
            are permitted (everything else is blocked). ``block`` means only
            blocked hosts/IPs are denied (everything else is allowed).
            """,
        )
        loader.add_option(
            "allowlist_default_action",
            str,
            self._default_action,
            """
            Default action for requests that don't match any rule.
            ``block`` or ``allow``.
            """,
        )
        loader.add_option(
            "allowlist_status_code",
            int,
            self._status_code,
            """
            HTTP status code to return when blocking a request.
            Set to 444 to close the connection without sending a response.
            """,
        )
        loader.add_option(
            "allowlist_log_blocked",
            bool,
            self._log_blocked,
            """
            Log blocked requests to the mitmproxy console.
            """,
        )
        loader.add_option(
            "allowlist_log_allowed",
            bool,
            self._log_allowed,
            """
            Log allowed requests to the mitmproxy console.
            Useful for auditing which requests pass through.
            """,
        )

    def configure(self, updated):
        """Rebuild rules and apply option changes."""
        if "allowlist_mode" in updated:
            self._mode = ctx.options.allowlist_mode
        if "allowlist_default_action" in updated:
            self._default_action = ctx.options.allowlist_default_action
        if "allowlist_status_code" in updated:
            self._status_code = ctx.options.allowlist_status_code
        if "allowlist_log_blocked" in updated:
            self._log_blocked = ctx.options.allowlist_log_blocked
        if "allowlist_log_allowed" in updated:
            self._log_allowed = ctx.options.allowlist_log_allowed

    def _load_config(self, config_path: str):
        """Load rules from the YAML config file."""
        self.rules = []
        self.global_config = {}

        if not config_path or not os.path.isfile(config_path):
            log.info(
                "[allowlist] No config file provided or file not found; "
                "allowlist will only use mitmproxy --set options."
            )
            return

        with open(config_path) as f:
            config = yaml.safe_load(f)

        if not config:
            log.warning("[allowlist] Config file is empty.")
            return

        self.global_config = config.get("global", {})
        self._mode = self.global_config.get("mode", "allow")
        self._default_action = self.global_config.get("default_action", "block")
        self._status_code = self.global_config.get("status_code", 403)
        self._log_blocked = self.global_config.get("log_blocked", True)
        self._log_allowed = self.global_config.get("log_allowed", False)
        self._dry_run = self.global_config.get("dry_run", False)

        # Load rules from config
        for rule_def in self.global_config.get("rules", []):
            try:
                rule = AllowlistRule(rule_def)
                self.rules.append(rule)
                log.info(
                    f"[allowlist] Loaded rule '{rule.name}': "
                    f"mode={rule.mode}, hostnames={len(rule.hostnames)}, "
                    f"ip_ranges={len(rule.ip_ranges)}"
                )
            except Exception as e:
                log.warning(f"[allowlist] Failed to parse rule: {e}")

        # If no named rules are defined, fall back to flat hostnames/ip_ranges
        # from the global config section (a simple allowlist without rule objects).
        if not self.rules:
            flat_hostnames = self.global_config.get("hostnames", [])
            flat_ips = self.global_config.get("ip_ranges", [])
            if flat_hostnames or flat_ips:
                rule = AllowlistRule(
                    {
                        "name": "global-allowlist",
                        "mode": self._mode,
                        "hostnames": flat_hostnames,
                        "ip_ranges": flat_ips,
                        "enabled": True,
                    }
                )
                self.rules.append(rule)
                log.info(
                    f"[allowlist] No named rules; using flat allowlist: "
                    f"hostnames={len(flat_hostnames)}, ip_ranges={len(flat_ips)}"
                )

    def _is_localhost(self, hostname: str) -> bool:
        """Check if a hostname is a localhost variant."""
        h = hostname.lower()
        return h in ("localhost", "127.0.0.1", "::1", "[::1]")

    def _is_private_ip(self, ip_str: str) -> bool:
        """Check if an IP is a private/loopback/reserved address."""
        try:
            addr = ipaddress.ip_address(ip_str)
            return addr.is_loopback or addr.is_private or addr.is_reserved or addr.is_link_local
        except ValueError:
            return False

    def _check_rules(self, hostname: str, server_ip: str | None) -> str | None:
        """Check all rules against the request.

        Returns:
            ``"allow"``, ``"deny"``, or ``None`` (no rule matched).
        """
        for rule in self.rules:
            result = rule.evaluate(hostname, server_ip)
            if result is not None:
                return result
        return None

    def request(self, flow: http.HTTPFlow) -> None:
        """Main mitmproxy hook: filter HTTP requests based on allowlist rules."""
        # Skip flows that already have a response or error
        if flow.response or flow.error or not flow.live:
            return

        # pretty_host is the Host header / SNI hostname. In transparent mode
        # flow.request.host is the destination IP (the client already resolved
        # DNS), so hostname rules must match against pretty_host.
        hostname = _strip_port(flow.request.pretty_host or "")
        server_ip = _get_server_ip(flow)

        # Always allow localhost
        if self._is_localhost(hostname) or (server_ip and self._is_private_ip(server_ip)):
            if self._log_allowed:
                log.info(f"[allowlist] ALLOW (localhost/private) {hostname}{' ' + server_ip if server_ip else ''}")
            return

        # Check rules
        result = self._check_rules(hostname, server_ip)

        if result == "allow":
            if self._log_allowed:
                log.info(f"[allowlist] ALLOW {hostname}{' ' + server_ip if server_ip else ''}")
            return

        if result == "deny":
            if self._log_blocked:
                log.info(f"[allowlist] DENY (rule matched) {hostname}{' ' + server_ip if server_ip else ''}")
            if self._dry_run:
                return
            self._block_flow(flow, reason="rule matched")
            return

        # No rule matched — apply default action
        if self._default_action == "allow":
            if self._log_allowed:
                log.info(f"[allowlist] ALLOW (default) {hostname}{' ' + server_ip if server_ip else ''}")
            return

        # Default is block
        if self._log_blocked:
            log.info(f"[allowlist] DENY (default action) {hostname}{' ' + server_ip if server_ip else ''}")
        if self._dry_run:
            return
        self._block_flow(flow, reason="default action")

    def _block_flow(self, flow: http.HTTPFlow, reason: str = "") -> None:
        """Block an HTTP flow by returning an error response or killing it.

        Also sets ``flow.marked`` and ``flow.comment`` so that blocked flows
        are visually distinguishable in the mitmweb UI:
          - A 🔴 marker appears in the flow list
          - A comment "Blocked by allowlist" appears in the flow detail panel
          - The reason (rule name or "default action") is appended to the comment
        """
        # Visual indicator for mitmweb UI — marked flows show a 🔴 in the
        # flow list and can be filtered with the ``@marked`` view spec.
        flow.marked = ":no_entry_sign:"
        flow.comment = "Blocked by allowlist"
        if reason:
            flow.comment += f" ({reason})"

        if self._status_code == 444:
            flow.kill()
        else:
            from mitmproxy.net.http.status_codes import NO_RESPONSE

            if self._status_code == NO_RESPONSE:
                flow.kill()
            else:
                flow.response = http.Response.make(
                    self._status_code,
                    content=b"Forbidden - this host is not allowlisted.",
                    headers={"Content-Type": "text/plain"},
                )


# ---------------------------------------------------------------------------
# Module-level addon instance for mitmproxy script loading
# ---------------------------------------------------------------------------

_config_path = os.environ.get(
    "ALLOWLIST_CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "allowlist_config.yaml"),
)

addon = AllowlistAddon(config_path=_config_path)

# mitmproxy discovers a script's addons via a module-level ``addons`` list.
# Without this, the module is imported (config loads) but the addon's event
# hooks (request/response) are never registered, so no filtering happens.
addons = [addon]

"""
mitmproxy addon: Token Replacer

Conditionally replaces sensitive token values in HTTP requests when the
request target hostname and request content match configured patterns.

This module is designed to be loaded as a mitmproxy script via the
``scripts`` option. When loaded, mitmproxy will register the module-level
``addon`` instance defined at the bottom of this file.

Example loading (from mitmproxy command line):
    mitmweb --set scripts=token_replacer.py

Configuration file (YAML):
    See README.md for the full schema.
"""

import hashlib
import json
import logging
import os
import re
import uuid
from urllib import parse as urlparse

import yaml
from mitmproxy import http

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Environment variable resolution
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(r"^\$\{ENV:([^,}]+)(?:,([^}]*))?\}$")


def _resolve_env_refs(value: str) -> str:
    """Resolve ``${ENV:VAR}`` or ``${ENV:VAR,default}`` references in a string.

    The pattern matches the *entire* string. If ``value`` does not match the
    ``${ENV:...}`` form it is returned unchanged — plain strings, partial
    matches, and other syntax pass through transparently.

    Args:
        value: The string to resolve.

    Returns:
        The string with the env-var reference replaced by the environment
        variable's value, or the string unchanged if no reference was found.

    Raises:
        KeyError: If the referenced environment variable is not set and no
            default value was provided via the optional ``,default`` suffix.

    Examples:
        >>> os.environ["MY_VAR"] = "secret123"
        >>> _resolve_env_refs("${ENV:MY_VAR}")
        'secret123'
        >>> _resolve_env_refs("${ENV:MISSING,fallback}")
        'fallback'
        >>> _resolve_env_refs("plain text")
        'plain text'
    """
    m = _ENV_VAR_PATTERN.match(value)
    if not m:
        return value
    var_name, default = m.group(1), m.group(2)
    env_val = os.environ.get(var_name)
    if env_val is None:
        if default is not None:
            return default
        raise KeyError(
            f"Environment variable '{var_name}' referenced in config "
            f"but not set. Use '${{ENV:{var_name},fallback}}' to provide a default."
        )
    return env_val


def _matches_hostname(hostname: str, patterns: list[str]) -> bool:
    """Check if hostname matches any of the patterns.

    Supports both glob-style wildcards (e.g. ``*.example.com``) and full
    regular expressions (e.g. ``^auth\\..*\\.example\\.com$``).
    A pattern is treated as a raw regex if it contains regex metacharacters
    other than ``*`` (i.e. ``^ $ . + ? { } [ ] | ( )").
    """
    for pattern in patterns:
        if _is_raw_regex(pattern):
            if re.search(pattern, hostname, re.IGNORECASE):
                return True
        else:
            # Escape the pattern, then convert glob wildcards back to regex:
            # ``*`` → ``.*`` (match any number of characters)
            # ``?`` → ``.`` (match exactly one character)
            regex_pattern = (
                "^" + re.escape(pattern)
                .replace(r"\*", ".*")
                .replace(r"\?", ".")
                + "$"
            )
            if re.match(regex_pattern, hostname, re.IGNORECASE):
                return True
    return False


def _is_raw_regex(pattern: str) -> bool:
    """Detect whether a pattern is a full regex vs a glob.

    A pattern is treated as a raw regex if it contains any regex metacharacter
    other than ``*`` and ``?`` (i.e. ``^ $ + { } [ ] ( ) |``).  A pattern
    consisting of plain characters and ``*`` / ``?`` glob wildcards is treated
    as a glob.

    Glob wildcards:
    - ``*`` matches any number of characters (including zero).
    - ``?`` matches exactly one character.
    """
    regex_metachars = set(r"^$+{}[]()|")
    return any(c in regex_metachars for c in pattern)


def _apply_strategy(original_value: str, strategy: str, replacement_value: str) -> str:
    """Apply the replacement strategy to a matched token value.

    Args:
        original_value: The original token value to replace.
        strategy: One of 'static', 'hash', or 'uuid'.
        replacement_value: The static replacement string (used only for 'static' strategy).
    """
    if strategy == "static":
        return replacement_value
    elif strategy == "hash":
        # Hash strategy uses the original value (replacement_value is ignored)
        return hashlib.sha256(original_value.encode("utf-8")).hexdigest()
    elif strategy == "uuid":
        return str(uuid.uuid4())
    else:
        log.warning(f"Unknown replacement strategy '{strategy}', falling back to static")
        return replacement_value


# ---------------------------------------------------------------------------
# Content matchers
# ---------------------------------------------------------------------------

class JsonBodyMatcher:
    """Match and replace tokens inside a JSON request body using JSONPath-like dots."""

    def __init__(self, json_path: str, regex: str | None, strategy: str, replacement_value: str):
        self.json_path = json_path
        self.regex = re.compile(regex) if regex else None
        self.strategy = strategy
        self.replacement_value = replacement_value

    def match_and_replace(self, body_data: dict, collect_only: bool = False) -> tuple[dict, list[str]]:
        """Walk the JSON data, find matching values, replace them.

        Args:
            body_data: The parsed JSON data (modified in-place if not collect_only).
            collect_only: If True, only collect findings without modifying data.

        Returns (modified_data, list_of_original_tokens found).
        """
        found_tokens = []
        # Strip only the leading '$' root indicator and one optional '.'.
        # Do NOT use lstrip("$.") — that strips ALL leading '$' and '.'
        # characters, which silently drops the '..' (descendant) operator
        # and merges '$..data.api_key' with '$.data.api_key'.
        clean = self.json_path
        if clean.startswith("$"):
            clean = clean[1:]
        if clean.startswith("."):
            clean = clean[1:]
        keys = clean.split(".") if clean else []

        # Warn about unsupported JSONPath features (validated at load time
        # via _validate_rule, but check here too for safety).
        if ".." in self.json_path:
            log.warning(
                f"[token-replacer] JSONPath descendant operator '..' is not "
                f"supported in path '{self.json_path}'. Use body.raw with a "
                f"regex instead."
            )
            return body_data, found_tokens
        for key in keys:
            if "[" in key:
                log.warning(
                    f"[token-replacer] Array indices not supported in JSON path "
                    f"'{self.json_path}', key '{key}'. Use body.raw with a regex "
                    f"instead."
                )
                return body_data, found_tokens

        self._walk_and_replace(body_data, keys, 0, found_tokens, collect_only)
        return body_data, found_tokens

    def _walk_and_replace(self, obj, keys, depth, found_tokens, collect_only: bool = False):
        if depth == len(keys):
            # At the target key — but this shouldn't happen if we got here correctly.
            return
        key = keys[depth]
        if isinstance(obj, dict) and key in obj:
            if depth == len(keys) - 1:
                # This is the target leaf
                val = obj[key]
                if isinstance(val, str):
                    if self._regex_matches(val):
                        original = val
                        found_tokens.append(original)
                        if not collect_only:
                            obj[key] = _apply_strategy(val, self.strategy, self.replacement_value)
                else:
                    # Log warning for non-string values — they can't be replaced.
                    # Always emit (even in collect-only / detection phase) so that
                    # operators see the warning regardless of dry_run mode.
                    log.warning(
                        f"[token-replacer] Skipping non-string value at JSON path "
                        f"'{self.json_path}', key '{key}': type={type(val).__name__}"
                    )
            else:
                # Not a leaf — walk deeper into the nested dict.
                self._walk_and_replace(obj[key], keys, depth + 1, found_tokens, collect_only)
        else:
            # ``obj`` is not a dict at this level (e.g. a list or primitive) or
            # the key is missing. Warn so operators know the path can't be traversed further.
            if key in obj:
                log.warning(
                    f"[token-replacer] JSON path '{self.json_path}' cannot traverse "
                    f"into non-dict value at key '{key}' "
                    f"(type={type(obj[key]).__name__}). Use body.raw with a regex to "
                    f"match tokens inside arrays."
                )
            else:
                log.warning(
                    f"[token-replacer] JSON path '{self.json_path}' key '{key}' not "
                    f"found in object (type={type(obj).__name__})."
                )
            return

    def _regex_matches(self, value: str) -> bool:
        """Check if the value matches the additional regex filter (if set)."""
        if self.regex is None:
            return True
        return bool(self.regex.search(value))


class HeaderMatcher:
    """Match and replace tokens inside a specific HTTP header."""

    def __init__(self, header_name: str, regex: str, strategy: str, replacement_value: str):
        self.header_name = header_name  # case-insensitive comparison
        self.regex = re.compile(regex)
        self.strategy = strategy
        self.replacement_value = replacement_value

    def match_and_replace(self, headers, collect_only: bool = False) -> list[str]:
        """Find and replace tokens in the header.

        Args:
            headers: The request headers object.
            collect_only: If True, only collect findings without modifying headers.

        Returns list of original token values found.
        """
        found_tokens = []
        # Headers in mitmproxy are case-insensitive but stored with original casing.
        # We need to find the actual key.
        actual_key = None
        for key in headers.keys():
            if key.lower() == self.header_name.lower():
                actual_key = key
                break
        if actual_key is None:
            return found_tokens

        value = headers[actual_key]
        matches = list(self.regex.finditer(value))
        if matches:
            # Collect all originals from the original value
            originals = [m.group(0) for m in matches]
            found_tokens.extend(originals)

            if collect_only:
                return found_tokens

            # Build a replacement function that preserves all capture groups.
            # When the regex has capture groups (e.g. "(session=)[a-f0-9]{32}"),
            # each group is preserved in the output while the strategy is
            # applied to the non-captured portions (the sensitive values).
            def _replacement_func(m):
                if m.groups():
                    # Preserve all capture groups, apply strategy to
                    # non-captured portions.
                    result_parts = []
                    last_end = 0
                    for i in range(1, len(m.groups()) + 1):
                        group_start = m.start(i)
                        group_end = m.end(i)
                        # Replace the non-captured portion before this group
                        if group_start > last_end:
                            non_captured = m.group(0)[last_end:group_start]
                            result_parts.append(
                                _apply_strategy(
                                    non_captured, self.strategy, self.replacement_value
                                )
                            )
                        # Preserve the capture group as-is
                        result_parts.append(m.group(i))
                        last_end = group_end
                    # Handle any non-captured portion after the last group
                    if last_end < len(m.group(0)):
                        non_captured = m.group(0)[last_end:]
                        result_parts.append(
                            _apply_strategy(
                                non_captured, self.strategy, self.replacement_value
                            )
                        )
                    return "".join(result_parts)
                else:
                    # No capture groups — replace the full match
                    return _apply_strategy(m.group(0), self.strategy, self.replacement_value)

            replaced = self.regex.sub(_replacement_func, value)

            # Update header value — mitmproxy headers are mutable and support
            # direct assignment without deleting first, which preserves
            # header insertion order.
            headers[actual_key] = replaced
        return found_tokens


class FormBodyMatcher:
    """Match and replace tokens in URL-encoded form bodies."""

    def __init__(self, field_name: str, regex: str, strategy: str, replacement_value: str):
        self.field_name = field_name
        self.regex = re.compile(regex)
        self.strategy = strategy
        self.replacement_value = replacement_value

    def match_and_replace_pairs(self, pairs: list[tuple[str, str]], collect_only: bool = False) -> list[str]:
        """Operate on a list of (key, value) tuples to preserve duplicate keys.

        Args:
            pairs: The list of form pairs (modified in-place if not collect_only).
            collect_only: If True, only collect findings without modifying pairs.

        Returns list of original token values found.
        """
        found_tokens = []
        for i, (key, val) in enumerate(pairs):
            if key == self.field_name and isinstance(val, str) and self.regex.search(val):
                original = val
                found_tokens.append(original)
                if not collect_only:
                    pairs[i] = (key, _apply_strategy(val, self.strategy, self.replacement_value))
        return found_tokens


class QueryStringMatcher:
    """Match and replace tokens in URL query string parameters."""

    def __init__(self, field_name: str, regex: str, strategy: str, replacement_value: str):
        self.field_name = field_name
        self.regex = re.compile(regex)
        self.strategy = strategy
        self.replacement_value = replacement_value

    def match_and_replace_pairs(self, pairs: list[tuple[str, str]], collect_only: bool = False) -> list[str]:
        """Operate on a list of (key, value) tuples to preserve duplicate keys.

        Args:
            pairs: The list of query pairs (modified in-place if not collect_only).
            collect_only: If True, only collect findings without modifying pairs.

        Returns list of original token values found.
        """
        found_tokens = []
        for i, (key, val) in enumerate(pairs):
            if key == self.field_name and isinstance(val, str) and self.regex.search(val):
                original = val
                found_tokens.append(original)
                if not collect_only:
                    pairs[i] = (key, _apply_strategy(val, self.strategy, self.replacement_value))
        return found_tokens


class RawBodyMatcher:
    """Match and replace tokens in raw (arbitrary) body text."""

    def __init__(self, regex: str, strategy: str, replacement_value: str):
        self.regex = re.compile(regex)
        self.strategy = strategy
        self.replacement_value = replacement_value

    def match_and_replace(self, raw_body: str, collect_only: bool = False) -> tuple[str, list[str]]:
        found_tokens = []
        matches = list(self.regex.finditer(raw_body))
        if matches:
            # Collect all originals
            for m in matches:
                found_tokens.append(m.group(0))

            if collect_only:
                return raw_body, found_tokens

            # Replace all matches (process in reverse to preserve positions)
            for m in reversed(matches):
                original = m.group(0)
                replacement = _apply_strategy(original, self.strategy, self.replacement_value)
                raw_body = raw_body[: m.start()] + replacement + raw_body[m.end() :]
        return raw_body, found_tokens


# ---------------------------------------------------------------------------
# Rule schema validation
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = {"static", "hash", "uuid"}
_VALID_FIELDS = {"body.json", "body.form", "body.raw", "body.query", "headers"}


def _validate_rule(rule: dict) -> None:
    """Validate a single rule's schema and raise ValueError on issues.

    Catches configuration errors at load time rather than at request time.
    """
    name = rule.get("name", "<unnamed>")
    replace = rule.get("replace_with", {})
    strategy = replace.get("strategy", "static")
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"Rule '{name}': invalid replacement strategy '{strategy}'. "
            f"Valid strategies: {sorted(_VALID_STRATEGIES)}"
        )

    content_patterns = rule.get("content_patterns")
    if not isinstance(content_patterns, list):
        raise ValueError(
            f"Rule '{name}': 'content_patterns' must be a list, got "
            f"'{type(content_patterns).__name__}'"
        )

    for i, pattern_def in enumerate(content_patterns):
        field = pattern_def.get("field", "<missing>")
        if field not in _VALID_FIELDS:
            raise ValueError(
                f"Rule '{name}' pattern[{i}]: invalid field type '{field}'. "
                f"Valid fields: {sorted(_VALID_FIELDS)}"
            )

        if field == "body.json":
            json_path = pattern_def.get("path", "")
            if not json_path:
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'path' is required for body.json"
                )
            if not pattern_def.get("regex"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'regex' is required for body.json. "
                    f"Without a regex, ALL string values at path '{json_path}' "
                    f"will be replaced."
                )
            if ".." in json_path:
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: JSONPath descendant operator '..' "
                    f"is not supported in path '{json_path}'."
                )
        elif field == "body.form":
            if not pattern_def.get("field_name"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'field_name' is required for body.form"
                )
            if not pattern_def.get("regex"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'regex' is required for body.form. "
                    f"Without a regex, ALL values for field '{pattern_def.get('field_name')}' "
                    f"will be replaced."
                )
        elif field == "body.query":
            if not pattern_def.get("field_name"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'field_name' is required for body.query"
                )
            if not pattern_def.get("regex"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'regex' is required for body.query"
                )
        elif field == "headers":
            if not pattern_def.get("header_name"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'header_name' is required for headers"
                )
            if not pattern_def.get("regex"):
                raise ValueError(
                    f"Rule '{name}' pattern[{i}]: 'regex' is required for headers"
                )


# ---------------------------------------------------------------------------
# Hostname helpers
# ---------------------------------------------------------------------------


def _strip_port(hostname: str) -> str:
    """Strip trailing port from hostname.

    mitmproxy includes the port in ``flow.request.host`` (e.g. ``api.example.com:443``),
    but hostname patterns in the config don't include ports. This helper ensures
    pattern matching works regardless.

    Handles both plain hostnames (``api.example.com:443``) and bracketed IPv6
    (``[::1]:443``). Bare IPv6 addresses without a port (``::1``) are returned
    unchanged — they contain multiple colons which would otherwise be mistaken
    for a port separator.
    """
    if hostname.startswith("["):
        # Bracketed IPv6 (e.g. ``[::1]:443``).
        bracket_end = hostname.find("]")
        if bracket_end != -1 and bracket_end + 1 < len(hostname) and hostname[bracket_end + 1] == ":":
            return hostname[: bracket_end + 1]
        return hostname
    # Bare hostname — count colons to distinguish IPv4:port from bare IPv6.
    if hostname.count(":") > 1:
        # Bare IPv6 (e.g. ``::1`` or ``2001:db8::1``) — no port to strip.
        return hostname
    if ":" in hostname:
        return hostname.rsplit(":", 1)[0]
    return hostname


def _remove_chunked_encoding_if_present(headers) -> None:
    """Remove `Transfer-Encoding: chunked` if present in headers.

    When the addon replaces a body and sets a new `Content-Length`, the original
    `Transfer-Encoding: chunked` header must be removed to avoid a malformed HTTP
    response (RFC 7230 §3.3.3 requires `Content-Length` to be ignored when
    `Transfer-Encoding` is present, which would cause clients to parse the
    invalid chunk-encoded body).

    Args:
        headers: mitmproxy Headers object (supports `in` and `del`).
    """
    if "Transfer-Encoding" in headers:
        del headers["Transfer-Encoding"]


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

def _build_matchers(rule: dict) -> list:
    """Build matcher instances from a rule's content_patterns definitions."""
    matchers = []
    replace = rule.get("replace_with", {})
    strategy = replace.get("strategy", "static")
    raw_value = replace.get("value", "REDACTED")
    replacement_value = _resolve_env_refs(raw_value)

    for pattern_def in rule.get("content_patterns", []):
        field = pattern_def.get("field", "body.raw")

        if field == "body.json":
            matchers.append(JsonBodyMatcher(
                json_path=pattern_def.get("path", ""),
                regex=pattern_def.get("regex"),
                strategy=strategy,
                replacement_value=replacement_value,
            ))
        elif field == "body.form":
            matchers.append(FormBodyMatcher(
                field_name=pattern_def.get("field_name", ""),
                regex=pattern_def["regex"],
                strategy=strategy,
                replacement_value=replacement_value,
            ))
        elif field == "body.query":
            matchers.append(QueryStringMatcher(
                field_name=pattern_def.get("field_name", ""),
                regex=pattern_def.get("regex", ".*"),
                strategy=strategy,
                replacement_value=replacement_value,
            ))
        elif field == "body.raw":
            matchers.append(RawBodyMatcher(
                regex=pattern_def["regex"],
                strategy=strategy,
                replacement_value=replacement_value,
            ))
        elif field == "headers":
            matchers.append(HeaderMatcher(
                header_name=pattern_def["header_name"],
                regex=pattern_def["regex"],
                strategy=strategy,
                replacement_value=replacement_value,
            ))
        else:
            log.warning(f"Unknown content field type: {field}")

    if not matchers:
        log.warning(
            f"Rule '{rule.get('name', 'unnamed')}': has no content_patterns, "
            f"will not match anything."
        )

    return matchers


class TokenReplacerAddon:
    """
    mitmproxy addon that replaces token values in HTTP requests when
    the request hostname and content match configured patterns.
    """

    def __init__(self, config_path: str = ""):
        self.rules: list[dict] = []
        self.global_config: dict = {}
        self._load_config(config_path)

    def __repr__(self) -> str:
        return f"TokenReplacerAddon(rules={len(self.rules)})"

    def _load_config(self, config_path: str):
        """Load rules from the YAML config file."""
        # Always initialize (in case __new__ bypassed __init__)
        self.rules: list[dict] = []
        self.global_config: dict = {}

        if not config_path or not os.path.isfile(config_path):
            log.info("No config file provided or file not found; no replacement rules loaded.")
            return

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        if not config:
            log.warning("Config file is empty.")
            return

        self.global_config = config.get("global", {})
        self.rules = config.get("rules", [])

        # Pre-compile hostname patterns and build matchers for each rule
        for rule in self.rules:
            _validate_rule(rule)
            rule.setdefault("hostnames", [])
            rule.setdefault("content_patterns", [])
            rule["_matchers"] = _build_matchers(rule)
            log.info(
                f"Loaded rule '{rule.get('name', 'unnamed')}': "
                f"hostnames={rule['hostnames']}, "
                f"content_patterns={len(rule['content_patterns'])}"
            )

    def _should_replace(self, hostname: str) -> list[dict]:
        """Return rules whose hostname patterns match the given hostname."""
        matching = []
        for rule in self.rules:
            if _matches_hostname(hostname, rule["hostnames"]):
                matching.append(rule)
        return matching

    def on_request(self, flow: http.HTTPFlow) -> None:
        """Main hook: inspect and potentially modify every HTTP request.

        Operates in two phases to avoid cross-rule interference:
        1. **Detection** — all matchers scan the original data and collect findings.
        2. **Application** — modifications are applied grouped by content type,
           each operating on the original parsed data (single pass).
        """
        # Strip port from hostname (mitmproxy includes it, e.g. "api.example.com:443")
        hostname = _strip_port(flow.request.host or "")

        applicable_rules = self._should_replace(hostname)
        if not applicable_rules:
            return

        dry_run = self.global_config.get("dry_run", False)
        log_replacements = self.global_config.get("log_replacements", True)

        # Phase 1: Collect all findings from every matching rule, without modifying
        # anything. Each matcher operates on the original request data.
        rule_findings: list[tuple[str, list[str]]] = []
        for rule in applicable_rules:
            rule_tokens = self._collect_findings_from_rule(flow.request, rule)
            # Deduplicate tokens within the rule to avoid double-counting
            # when multiple matchers match the same value
            rule_tokens = list(dict.fromkeys(rule_tokens))
            rule_findings.append((rule.get("name", "?"), rule_tokens))

        # Phase 2: Apply all modifications grouped by content type.  Each group
        # is processed once against the *original* request data so that one rule's
        # replacement value can never be matched by another rule's regex.
        if not dry_run:
            self._apply_all_modifications(flow.request, applicable_rules, is_response=False)

        # Phase 3: Log (only when log_replacements is enabled)
        if log_replacements:
            total_tokens_found = 0
            for rule_name, rule_tokens in rule_findings:
                if rule_tokens:
                    status = "DRY RUN (no modification)" if dry_run else "replaced"
                    for token in rule_tokens:
                        log.info(
                            f"[token-replacer] Rule '{rule_name}' matched: "
                            f"host={hostname}, token={token[:50]}..., status={status}"
                        )
                    total_tokens_found += len(rule_tokens)

            if total_tokens_found > 0:
                status = "DRY RUN (no modification)" if dry_run else "replaced"
                log.info(
                    f"[token-replacer] {total_tokens_found} token(s) {status} in request to {hostname}"
                )

    def on_response(self, flow: http.HTTPFlow) -> None:
        """Mirror of on_request: inspect and potentially modify HTTP responses.

        Applies the same hostname + content-pattern matching to response bodies
        and headers, masking sensitive tokens that may have been returned by the
        upstream server (e.g., API keys in error responses, tokens in 401 bodies).
        """
        hostname = _strip_port(flow.request.host or "")

        applicable_rules = self._should_replace(hostname)
        if not applicable_rules:
            return

        dry_run = self.global_config.get("dry_run", False)
        log_replacements = self.global_config.get("log_replacements", True)

        # Phase 1: Collect all findings from every matching rule, without modifying
        # anything. Each matcher operates on the original response data.
        rule_findings: list[tuple[str, list[str]]] = []
        for rule in applicable_rules:
            rule_tokens = self._collect_findings_from_rule(flow.response, rule)
            # Deduplicate tokens within the rule to avoid double-counting
            # when multiple matchers match the same value
            rule_tokens = list(dict.fromkeys(rule_tokens))
            rule_findings.append((rule.get("name", "?"), rule_tokens))

        # Phase 2: Apply all modifications grouped by content type.
        if not dry_run:
            self._apply_all_modifications(flow.response, applicable_rules, is_response=True)

        # Phase 3: Log (only when log_replacements is enabled)
        if log_replacements:
            total_tokens_found = 0
            status = "DRY RUN (no modification)" if dry_run else "replaced"
            for rule_name, rule_tokens in rule_findings:
                if rule_tokens:
                    for token in rule_tokens:
                        log.info(
                            f"[token-replacer] Rule '{rule_name}' matched in response: "
                            f"host={hostname}, token={token[:50]}..., status={status}"
                        )
                    total_tokens_found += len(rule_tokens)

            if total_tokens_found > 0:
                log.info(
                    f"[token-replacer] {total_tokens_found} token(s) {status} in response to {hostname}"
                )

    def _collect_findings_from_rule(self, target, rule: dict) -> list[str]:
        """Collect all token findings from a rule's matchers without modifying the target."""
        found = []
        for matcher in rule.get("_matchers", []):
            found.extend(self._collect_from_matcher(target, matcher))
        return found

    def _collect_from_matcher(self, target, matcher) -> list[str]:
        """Run a matcher against the original data and return found tokens."""
        if isinstance(matcher, JsonBodyMatcher):
            body = target.get_content()
            if body:
                try:
                    data = json.loads(body)
                    _, tokens = matcher.match_and_replace(data, collect_only=True)
                    return tokens
                except (json.JSONDecodeError, TypeError) as e:
                    log.debug(
                        f"[token-replacer] Skipping body.json matcher: "
                        f"content is not valid JSON (error: {e})"
                    )
                    return []
        elif isinstance(matcher, FormBodyMatcher):
            body = target.get_content()
            if body:
                parsed_pairs = list(urlparse.parse_qsl(body.decode("utf-8", errors="ignore")))
                tokens = matcher.match_and_replace_pairs(parsed_pairs, collect_only=True)
                return tokens
        elif isinstance(matcher, QueryStringMatcher):
            if hasattr(target, 'query'):
                pairs = list(target.query)
                tokens = matcher.match_and_replace_pairs(pairs, collect_only=True)
                return tokens
        elif isinstance(matcher, RawBodyMatcher):
            body = target.get_content()
            if body:
                raw_str = body.decode("utf-8", errors="ignore")
                _, tokens = matcher.match_and_replace(raw_str, collect_only=True)
                return tokens
        elif isinstance(matcher, HeaderMatcher):
            return matcher.match_and_replace(target.headers, collect_only=True)
        return []

    def _apply_all_modifications(
        self, target, rules: list[dict], is_response: bool = False
    ) -> None:
        """Apply modifications from all rules' matchers, grouped by content type.

        Each content type (JSON / form / raw) is parsed once from the original
        body, all matchers of that type are applied, and the body is
        written back once.  This prevents one rule's replacement value from
        being re-matched by another rule.

        Args:
            target: The request or response flow target to modify.
            rules: The list of applicable rules.
            is_response: True if target is a response (query matchers are skipped).
        """
        # Group all matchers across rules by type
        json_matchers: list[JsonBodyMatcher] = []
        form_matchers: list[FormBodyMatcher] = []
        query_matchers: list[QueryStringMatcher] = []
        raw_matchers: list[RawBodyMatcher] = []
        header_matchers: list[HeaderMatcher] = []

        for rule in rules:
            for matcher in rule.get("_matchers", []):
                if isinstance(matcher, JsonBodyMatcher):
                    json_matchers.append(matcher)
                elif isinstance(matcher, FormBodyMatcher):
                    form_matchers.append(matcher)
                elif isinstance(matcher, QueryStringMatcher):
                    query_matchers.append(matcher)
                elif isinstance(matcher, RawBodyMatcher):
                    raw_matchers.append(matcher)
                elif isinstance(matcher, HeaderMatcher):
                    header_matchers.append(matcher)

        # --- JSON body: parse once, apply all matchers, write back once ---
        if json_matchers:
            body = target.get_content()
            if body:
                try:
                    data = json.loads(body)
                    for matcher in json_matchers:
                        matcher.match_and_replace(data)
                    new_body = json.dumps(data).encode("utf-8")
                    target.set_content(new_body)
                    _remove_chunked_encoding_if_present(target.headers)
                    target.headers["Content-Length"] = str(len(new_body))
                except (json.JSONDecodeError, TypeError) as e:
                    log.debug(f"JSON parse failed during modification: {e}")

        # --- Form body: parse once, apply all matchers, write back once ---
        if form_matchers:
            body = target.get_content()
            if body:
                # Use parse_qsl to preserve duplicate keys as a list of tuples
                parsed_pairs = list(urlparse.parse_qsl(body.decode("utf-8", errors="ignore")))
                # Build a dict for quick lookup, but apply matchers to the pairs list
                # to preserve duplicate keys and order
                for matcher in form_matchers:
                    matcher.match_and_replace_pairs(parsed_pairs)
                new_body = urlparse.urlencode(parsed_pairs).encode("utf-8")
                target.set_content(new_body)
                _remove_chunked_encoding_if_present(target.headers)
                target.headers["Content-Length"] = str(len(new_body))

        # --- Raw body: apply all matchers to original string, write back once ---
        if raw_matchers:
            body = target.get_content()
            if body:
                raw_str = body.decode("utf-8", errors="ignore")
                # Each matcher operates on the ORIGINAL string to prevent
                # one rule's replacement from being re-matched by another.
                # We collect all replacements and apply them in a single pass.
                replacements: list[tuple[int, int, str]] = []
                for matcher in raw_matchers:
                    for m in matcher.regex.finditer(raw_str):
                        original = m.group(0)
                        new_text = _apply_strategy(original, matcher.strategy, matcher.replacement_value)
                        replacements.append((m.start(), m.end(), new_text))

                if replacements:
                    # Check for overlapping replacements and resolve conflicts
                    # (earlier match takes priority; if same start, first matcher wins)
                    replacements.sort(key=lambda r: (r[0], -r[1]))
                    filtered: list[tuple[int, int, str]] = []
                    end_pos = -1
                    for start, end, new_text in replacements:
                        if start >= end_pos:
                            # No overlap with previous replacement
                            filtered.append((start, end, new_text))
                            end_pos = end
                        else:
                            # Overlap detected — skip this replacement
                            log.debug(
                                f"[token-replacer] Skipping overlapping raw body replacement "
                                f"at position [{start}, {end})"
                            )

                    # Apply replacements from end to start to preserve positions
                    for start, end, new_text in reversed(filtered):
                        raw_str = raw_str[:start] + new_text + raw_str[end:]
                    new_bytes = raw_str.encode("utf-8")
                    target.set_content(new_bytes)
                    _remove_chunked_encoding_if_present(target.headers)
                    target.headers["Content-Length"] = str(len(new_bytes))

        # --- Query string: parse once, apply all matchers, write back once ---
        # Note: Query strings are request-only; skip query matchers for responses
        if query_matchers and hasattr(target, 'query') and not is_response:
            pairs = list(target.query)
            for matcher in query_matchers:
                matcher.match_and_replace_pairs(pairs)
            if pairs != list(target.query):
                target._set_query(pairs)

        # --- Headers: group matchers by header name, apply all replacements
        #     from original header values in a single pass to prevent
        #     cross-rule interference (one rule's replacement matching another's
        #     regex). ---
        # Group matchers by lowercase header name
        header_groups: dict[str, list[HeaderMatcher]] = {}
        for matcher in header_matchers:
            key = matcher.header_name.lower()
            header_groups.setdefault(key, []).append(matcher)

        for header_key_lower, matchers_for_header in header_groups.items():
            # Find the actual header key (preserves original casing)
            actual_key = None
            for key in target.headers.keys():
                if key.lower() == header_key_lower:
                    actual_key = key
                    break
            if actual_key is None:
                continue

            original_value = target.headers[actual_key]

            # Collect all replacements from the ORIGINAL value
            replacements: list[tuple[int, int, str]] = []
            for matcher in matchers_for_header:
                for m in matcher.regex.finditer(original_value):
                    original = m.group(0)
                    # Apply capture-group-aware replacement:
                    # preserve all capture groups, apply strategy to non-captured
                    # portions of the match.
                    if m.groups():
                        parts = []
                        last_end = 0
                        for gi in range(1, len(m.groups()) + 1):
                            gs = m.start(gi)
                            ge = m.end(gi)
                            if gs > last_end:
                                parts.append(
                                    _apply_strategy(
                                        m.group(0)[last_end:gs],
                                        matcher.strategy, matcher.replacement_value,
                                    )
                                )
                            parts.append(m.group(gi))
                            last_end = ge
                        if last_end < len(m.group(0)):
                            parts.append(
                                _apply_strategy(
                                    m.group(0)[last_end:],
                                    matcher.strategy, matcher.replacement_value,
                                )
                            )
                        new_text = "".join(parts)
                    else:
                        new_text = _apply_strategy(
                            original, matcher.strategy, matcher.replacement_value
                        )
                    replacements.append((m.start(), m.end(), new_text))

            if replacements:
                # Resolve overlapping replacements (earlier start wins;
                # for same start, longer match wins)
                replacements.sort(key=lambda r: (r[0], -r[1]))
                filtered: list[tuple[int, int, str]] = []
                end_pos = -1
                for start, end, new_text in replacements:
                    if start >= end_pos:
                        filtered.append((start, end, new_text))
                        end_pos = end
                    else:
                        log.debug(
                            f"[token-replacer] Skipping overlapping header "
                            f"replacement for '{actual_key}' at position "
                            f"[{start}, {end})"
                        )

                # Apply replacements from end to start to preserve positions
                for start, end, new_text in reversed(filtered):
                    original_value = (
                        original_value[:start] + new_text + original_value[end:]
                    )
                target.headers[actual_key] = original_value


# ---------------------------------------------------------------------------
# Module-level addon instance for mitmproxy script loading
# ---------------------------------------------------------------------------

# This instance is registered by mitmproxy when the script is loaded via
# `--set scripts=token_replacer.py`. The config_path points to the YAML
# configuration file that defines the replacement rules.
#
# To use a different config path, modify this line or set the environment
# variable TOKEN_REPLACER_CONFIG_PATH.
import os

_config_path = os.environ.get(
    "TOKEN_REPLACER_CONFIG_PATH",
    os.path.join(os.path.dirname(__file__), "token_replacer_config.yaml"),
)

addon = TokenReplacerAddon(config_path=_config_path)

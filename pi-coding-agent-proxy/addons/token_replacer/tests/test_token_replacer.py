"""
Unit tests for the Token Replacer mitmproxy addon.

Run with:
    python -m pytest tests/test_token_replacer.py -v
"""

import json
import re
import yaml
from unittest.mock import MagicMock, patch
from urllib import parse as urlparse

import pytest

# We import the module directly — these tests do not require a running mitmproxy instance.
# For flow-level testing we use MagicMock to simulate mitmproxy HTTPFlow objects.

import sys
import os

# Ensure the parent directory is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from token_replacer import (
    _apply_strategy,
    _is_raw_regex,
    _matches_hostname,
    _resolve_env_refs,
    _strip_port,
    _validate_rule,
    HeaderMatcher,
    JsonBodyMatcher,
    RawBodyMatcher,
    FormBodyMatcher,
    QueryStringMatcher,
    TokenReplacerAddon,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(method: str = "POST", host: str = "api.example.com",
               path: str = "/", headers: dict = None, body: bytes = b""):
    """Build a MagicMock HTTPFlow with the given properties."""
    flow = MagicMock()
    flow.request = MagicMock()
    flow.request.method = method
    flow.request.host = host
    flow.request.path = path
    flow.request.pretty_url = f"http://{host}{path}"
    flow.request.headers = MagicMock()
    flow.request.headers.__iter__ = MagicMock(return_value=iter([]))
    flow.request.headers.keys = MagicMock(return_value=iter([]))
    flow.request.headers.__setitem__ = MagicMock()
    flow.request.headers.__delitem__ = MagicMock()
    flow.request.get_content = MagicMock(return_value=body)
    flow.request.set_content = MagicMock()
    flow.log = MagicMock()
    return flow


def _make_response_flow(host: str = "api.example.com", body: bytes = b""):
    """Build a MagicMock HTTPFlow with a mocked response."""
    flow = MagicMock()
    flow.request = MagicMock()
    flow.request.host = host
    flow.request.get_content = MagicMock(return_value=b"")
    flow.response = MagicMock()
    flow.response.headers = MagicMock()
    flow.response.headers.keys = MagicMock(return_value=iter([]))
    flow.response.headers.__setitem__ = MagicMock()
    flow.response.headers.__delitem__ = MagicMock()
    flow.response.get_content = MagicMock(return_value=body)
    flow.response.set_content = MagicMock()
    return flow


def _make_headers_mock(headers_dict: dict[str, str]):
    """Create a MagicMock that simulates mitmproxy's MutableHeaders interface.

    Provides keys(), __getitem__, __setitem__, __delitem__, and __contains__
    backed by a real dict so that mutation behavior (order, deletion, etc.)
    can be inspected by tests.
    """
    mock = MagicMock()
    mock.keys = MagicMock(side_effect=lambda: iter(headers_dict.keys()))

    def _getitem(k):
        return headers_dict.get(k, "")
    mock.__getitem__ = MagicMock(side_effect=_getitem)

    def _setitem(k, v):
        headers_dict[k] = v
    mock.__setitem__ = MagicMock(side_effect=_setitem)

    def _delitem(k):
        headers_dict.pop(k, None)
    mock.__delitem__ = MagicMock(side_effect=_delitem)

    mock.__contains__ = MagicMock(side_effect=lambda k: k in headers_dict)
    return mock


# ---------------------------------------------------------------------------
# _matches_hostname tests
# ---------------------------------------------------------------------------

class TestMatchesHostname:
    def test_exact_match(self):
        assert _matches_hostname("api.example.com", ["api.example.com"]) is True

    def test_no_match(self):
        assert _matches_hostname("evil.com", ["api.example.com"]) is False

    def test_wildcard_subdomain(self):
        assert _matches_hostname("auth.api.example.com", ["*.api.example.com"]) is True

    def test_wildcard_no_match(self):
        assert _matches_hostname("other.com", ["*.api.example.com"]) is False

    def test_regex_style_pattern(self):
        assert _matches_hostname("staging.api.example.com", ["^.*api.*\\.example\\.com$"]) is True

    def test_case_insensitive(self):
        assert _matches_hostname("API.EXAMPLE.COM", ["api.example.com"]) is True

    def test_qmark_glob_single_char(self):
        """? should match exactly one character in glob patterns."""
        assert _matches_hostname("auth1.example.com", ["auth?.example.com"]) is True
        assert _matches_hostname("authx.example.com", ["auth?.example.com"]) is True
        assert _matches_hostname("auth.example.com", ["auth?.example.com"]) is False
        assert _matches_hostname("auth12.example.com", ["auth?.example.com"]) is False

    def test_empty_patterns(self):
        assert _matches_hostname("api.example.com", []) is False


# ---------------------------------------------------------------------------
# _apply_strategy tests
# ---------------------------------------------------------------------------

class TestIsRawRegex:
    def test_star_is_glob(self):
        assert _is_raw_regex("*.example.com") is False

    def test_qmark_is_glob(self):
        # ? should be treated as a single-character glob, not a regex metachar
        assert _is_raw_regex("auth?.example.com") is False

    def test_caret_is_regex(self):
        assert _is_raw_regex("^auth.*.example.com$") is True

    def test_plain_text(self):
        assert _is_raw_regex("api.example.com") is False

    def test_dollar_is_regex(self):
        assert _is_raw_regex("example.com$") is True


class TestApplyStrategy:
    def test_static(self):
        assert _apply_strategy("secret123", "static", "MASKED") == "MASKED"

    def test_hash(self):
        import hashlib
        result = _apply_strategy("secret123", "hash", "anything")
        expected = hashlib.sha256(b"secret123").hexdigest()
        assert result == expected

    def test_uuid(self):
        result = _apply_strategy("secret123", "uuid", "anything")
        # Should be a valid UUID v4
        parsed = re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", result)
        assert parsed is not None

    def test_unknown_fallback(self):
        # Unknown strategy falls back to static with replacement_value
        result = _apply_strategy("secret", "bogus", "REPLACED")
        assert result == "REPLACED"




# ---------------------------------------------------------------------------
# JsonBodyMatcher tests
# ---------------------------------------------------------------------------

class TestJsonBodyMatcher:
    def test_simple_replacement(self):
        matcher = JsonBodyMatcher(
            json_path="$.credentials.api_key",
            regex=None,
            strategy="static",
            replacement_value="REDACTED",
        )
        data = {"credentials": {"api_key": "ak_abcdefghijklmnopqrstuv"}}
        modified, found = matcher.match_and_replace(data)
        assert modified["credentials"]["api_key"] == "REDACTED"
        assert len(found) == 1
        assert found[0] == "ak_abcdefghijklmnopqrstuv"

    def test_no_match_when_regex_does_not_fit(self):
        matcher = JsonBodyMatcher(
            json_path="$.token",
            regex="ak_[A-Z]{5}",          # expects uppercase only
            strategy="static",
            replacement_value="X",
        )
        data = {"token": "ak_abcde"}   # lowercase — no match
        modified, found = matcher.match_and_replace(data)
        assert modified["token"] == "ak_abcde"  # unchanged
        assert found == []

    def test_nested_path_no_array_support(self):
        matcher = JsonBodyMatcher(
            json_path="$.data.items[0].secret",
            regex=None,
            strategy="hash",
            replacement_value="ignored",
        )
        data = {"data": {"items": [{"secret": "topsecret123"}]}}
        # Array indices are not supported — matcher logs a warning and
        # returns no matches without modifying the data.
        modified, found = matcher.match_and_replace(data)
        assert modified == data
        assert found == []

    def test_multiple_fields(self):
        matcher = JsonBodyMatcher(
            json_path="$.secret",
            regex=None,
            strategy="static",
            replacement_value="X",
        )
        data = {"secret": "value1"}
        modified, found = matcher.match_and_replace(data)
        assert modified["secret"] == "X"
        assert found == ["value1"]

    def test_non_string_value_ignored(self):
        matcher = JsonBodyMatcher(
            json_path="$.count",
            regex=None,
            strategy="static",
            replacement_value="X",
        )
        data = {"count": 42}
        modified, found = matcher.match_and_replace(data)
        assert modified["count"] == 42  # unchanged (not a string)
        assert found == []

    def test_non_string_value_warns_in_detection(self):
        """Non-string leaf values must emit a warning even in collect_only (Phase 1)."""
        matcher = JsonBodyMatcher(
            json_path="$.count",
            regex=None,
            strategy="static",
            replacement_value="X",
        )
        data = {"count": 42}
        with patch("token_replacer.log") as mock_log:
            _, found = matcher.match_and_replace(data, collect_only=True)
        assert found == []
        mock_log.warning.assert_called_once()
        assert "non-string" in str(mock_log.warning.call_args)

    def test_list_intermediate_warns_in_detection(self):
        """Traversing into a list (non-dict) must emit a warning in collect_only."""
        matcher = JsonBodyMatcher(
            json_path="$.items.secret",
            regex=None,
            strategy="static",
            replacement_value="X",
        )
        data = {"items": [{"secret": "in_array"}]}
        with patch("token_replacer.log") as mock_log:
            _, found = matcher.match_and_replace(data, collect_only=True)
        assert found == []
        mock_log.warning.assert_called_once()
        assert "non-dict" in str(mock_log.warning.call_args) or "type=list" in str(mock_log.warning.call_args)

    def test_missing_key_warns_in_detection(self):
        """Missing intermediate keys must emit a warning in collect_only."""
        matcher = JsonBodyMatcher(
            json_path="$.missing.secret",
            regex=None,
            strategy="static",
            replacement_value="X",
        )
        data = {"other": "value"}
        with patch("token_replacer.log") as mock_log:
            _, found = matcher.match_and_replace(data, collect_only=True)
        assert found == []
        mock_log.warning.assert_called_once()
        assert "not found" in str(mock_log.warning.call_args)


# ---------------------------------------------------------------------------
# HeaderMatcher tests
# ---------------------------------------------------------------------------

class TestHeaderMatcher:
    def _make_headers(self, items: dict):
        return _make_headers_mock(items)

    def test_bearer_token_replacement(self):
        matcher = HeaderMatcher(
            header_name="Authorization",
            regex=r"(?<=Bearer\s)\S+",
            strategy="static",
            replacement_value="REPLACED_BEARER",
        )
        headers = {"Authorization": "Bearer my_secret_token_12345"}
        found = matcher.match_and_replace(headers)
        assert len(found) == 1
        assert found[0] == "my_secret_token_12345"

    def test_missing_header(self):
        matcher = HeaderMatcher(
            header_name="Authorization",
            regex=r"(.*)",
            strategy="static",
            replacement_value="X",
        )
        headers = {"Content-Type": "application/json"}
        found = matcher.match_and_replace(headers)
        assert found == []

    def test_preserves_header_order(self):
        """HeaderMatcher must not delete-and-re-set the header, preserving order."""
        matcher = HeaderMatcher(
            header_name="Authorization",
            regex=r"(?<=Bearer\s)\S+",
            strategy="static",
            replacement_value="REPLACED",
        )
        # Use a real dict so we can inspect setitem order
        headers_dict: dict[str, str] = {"Content-Type": "application/json",
                                        "Authorization": "Bearer secret123",
                                        "X-Custom": "value"}

        h = _make_headers_mock(headers_dict)
        # Track setitem order: intercept the mock's side_effect
        set_order: list[str] = []
        original_setitem = h.__setitem__.side_effect
        h.__setitem__.side_effect = lambda k, v: (set_order.append(k), original_setitem(k, v))
        # __contains__ needs to work too (HeaderMatcher uses `in` for lookup)
        h.__contains__ = MagicMock(side_effect=lambda k: k in headers_dict)

        matcher.match_and_replace(h)

        # setitem should have been called exactly once (no del+set = 2 calls)
        assert h.__setitem__.call_count == 1
        # The header should have been set at its original position, not moved
        assert set_order[0] == "Authorization"
        # __delitem__ must NOT have been called
        h.__delitem__.assert_not_called()


# ---------------------------------------------------------------------------
# FormBodyMatcher tests
# ---------------------------------------------------------------------------

class TestFormBodyMatcher:
    def test_form_field_replacement(self):
        matcher = FormBodyMatcher(
            field_name="access_token",
            regex="at_[A-Za-z0-9]{32,}",
            strategy="static",
            replacement_value="REDACTED",
        )
        pairs = [("access_token", "at_abcdefghijklmnopqrstuvwxyz012345"), ("other", "value")]
        found = matcher.match_and_replace_pairs(pairs)
        assert pairs[0] == ("access_token", "REDACTED")
        assert pairs[1] == ("other", "value")
        assert len(found) == 1

    def test_no_match_wrong_field(self):
        matcher = FormBodyMatcher(
            field_name="access_token",
            regex=".*",
            strategy="static",
            replacement_value="X",
        )
        pairs = [("username", "admin"), ("other", "value")]
        found = matcher.match_and_replace_pairs(pairs)
        assert found == []
        assert pairs == [("username", "admin"), ("other", "value")]

    def test_regex_filter_excludes(self):
        matcher = FormBodyMatcher(
            field_name="token",
            regex="secret_[A-Z]{5}",
            strategy="static",
            replacement_value="X",
        )
        pairs = [("token", "secret_abcde")]   # lowercase — no match
        found = matcher.match_and_replace_pairs(pairs)
        assert found == []
        assert pairs == [("token", "secret_abcde")]

    def test_match_and_replace_pairs_preserves_duplicates(self):
        """match_and_replace_pairs must preserve duplicate keys."""
        matcher = FormBodyMatcher(
            field_name="token",
            regex=".*",
            strategy="static",
            replacement_value="REDACTED",
        )
        pairs = [("token", "first"), ("other", "keep"), ("token", "second")]
        found = matcher.match_and_replace_pairs(pairs)
        # Both tokens should be replaced
        assert len(found) == 2
        assert found == ["first", "second"]
        # Duplicate keys preserved in order
        assert pairs == [("token", "REDACTED"), ("other", "keep"), ("token", "REDACTED")]

    def test_match_and_replace_pairs_no_match(self):
        """No replacement when regex doesn't match."""
        matcher = FormBodyMatcher(
            field_name="token",
            regex="SECRET_[A-Z]{5}",
            strategy="static",
            replacement_value="X",
        )
        pairs = [("token", "secret_lower"), ("other", "value")]
        found = matcher.match_and_replace_pairs(pairs)
        assert found == []
        assert pairs == [("token", "secret_lower"), ("other", "value")]


# ---------------------------------------------------------------------------
# QueryStringMatcher tests
# ---------------------------------------------------------------------------

class TestQueryStringMatcher:
    def test_query_field_replacement(self):
        matcher = QueryStringMatcher(
            field_name="api_key",
            regex="ak_[A-Za-z0-9]{20,}",
            strategy="static",
            replacement_value="REDACTED",
        )
        pairs = [("api_key", "ak_abcdefghijklmnopqrstuvwxyz012345"), ("page", "1")]
        found = matcher.match_and_replace_pairs(pairs)
        assert pairs[0] == ("api_key", "REDACTED")
        assert pairs[1] == ("page", "1")
        assert len(found) == 1

    def test_no_match_wrong_field(self):
        matcher = QueryStringMatcher(
            field_name="api_key",
            regex=".*",
            strategy="static",
            replacement_value="X",
        )
        pairs = [("username", "admin"), ("page", "1")]
        found = matcher.match_and_replace_pairs(pairs)
        assert found == []
        assert pairs == [("username", "admin"), ("page", "1")]

    def test_regex_filter_excludes(self):
        matcher = QueryStringMatcher(
            field_name="token",
            regex="SECRET_[A-Z]{5}",
            strategy="static",
            replacement_value="X",
        )
        pairs = [("token", "secret_lower"), ("other", "value")]
        found = matcher.match_and_replace_pairs(pairs)
        assert found == []
        assert pairs == [("token", "secret_lower"), ("other", "value")]

    def test_preserves_duplicate_keys(self):
        """QueryStringMatcher must preserve duplicate query parameter keys."""
        matcher = QueryStringMatcher(
            field_name="tag",
            regex=".*",
            strategy="static",
            replacement_value="REDACTED",
        )
        pairs = [("tag", "first"), ("other", "keep"), ("tag", "second")]
        found = matcher.match_and_replace_pairs(pairs)
        assert len(found) == 2
        assert found == ["first", "second"]
        assert pairs == [("tag", "REDACTED"), ("other", "keep"), ("tag", "REDACTED")]


# ---------------------------------------------------------------------------
# RawBodyMatcher tests
# ---------------------------------------------------------------------------

class TestRawBodyMatcher:
    def test_jwt_replacement(self):
        matcher = RawBodyMatcher(
            regex=r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
            strategy="static",
            replacement_value="REDACTED_JWT",
        )
        body = 'Some text before eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123XYZ more text'
        modified, found = matcher.match_and_replace(body)
        assert "REDACTED_JWT" in modified
        assert "eyJhbGciOiJIUzI1NiJ9" not in modified
        assert len(found) == 1

    def test_no_match(self):
        matcher = RawBodyMatcher(
            regex=r"SECRET_[A-Z]{10}",
            strategy="static",
            replacement_value="X",
        )
        body = "hello world"
        modified, found = matcher.match_and_replace(body)
        assert modified == "hello world"
        assert found == []

    def test_multiple_matches(self):
        matcher = RawBodyMatcher(
            regex=r"token\d+",
            strategy="static",
            replacement_value="MASKED",
        )
        body = "use token1 and token2 here"
        modified, found = matcher.match_and_replace(body)
        assert modified == "use MASKED and MASKED here"
        assert len(found) == 2


# ---------------------------------------------------------------------------
# TokenReplacerAddon integration tests
# ---------------------------------------------------------------------------

class TestStripPort:
    def test_with_port(self):
        assert _strip_port("api.example.com:443") == "api.example.com"

    def test_without_port(self):
        assert _strip_port("api.example.com") == "api.example.com"

    def test_with_port_80(self):
        assert _strip_port("localhost:8080") == "localhost"

    def test_ipv6_with_port(self):
        # rsplit(":", 1) strips the last colon, which correctly removes :443 from [::1]:443
        assert _strip_port("[::1]:443") == "[::1]"

    def test_bare_ipv6_loopback(self):
        # Bare IPv6 without port must not be corrupted
        assert _strip_port("::1") == "::1"

    def test_bare_ipv6_full(self):
        assert _strip_port("2001:db8::1") == "2001:db8::1"

    def test_bare_ipv6_mapped(self):
        assert _strip_port("::ffff:192.168.1.1") == "::ffff:192.168.1.1"

    def test_empty_string(self):
        assert _strip_port("") == ""


class TestRawBodyMatcherTokenOrder:
    """Verify found_tokens preserves left-to-right order (no spurious reverse)."""

    def test_tokens_in_order(self):
        matcher = RawBodyMatcher(
            regex=r"TOKEN\d", strategy="static", replacement_value="X",
        )
        _, found = matcher.match_and_replace("TOKEN1 TOKEN2 TOKEN3")
        assert found == ["TOKEN1", "TOKEN2", "TOKEN3"]

    def test_collect_only_preserves_order(self):
        matcher = RawBodyMatcher(
            regex=r"TOKEN\d", strategy="static", replacement_value="X",
        )
        _, found = matcher.match_and_replace("TOKEN1 TOKEN2", collect_only=True)
        assert found == ["TOKEN1", "TOKEN2"]


class TestValidateRule:
    def test_valid_rule(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.json", "path": "$.key", "regex": ".*"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        _validate_rule(rule)  # should not raise

    def test_invalid_strategy(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.json", "path": "$.key"}],
            "replace_with": {"strategy": "invalid", "value": "X"},
        }
        with pytest.raises(ValueError, match="invalid replacement strategy"):
            _validate_rule(rule)

    def test_missing_body_json_path(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.json", "regex": ".*"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'path' is required"):
            _validate_rule(rule)

    def test_missing_body_json_regex(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.json", "path": "$.key"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'regex' is required"):
            _validate_rule(rule)

    def test_descendant_operator_not_supported(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.json", "path": "$..api_key", "regex": ".*"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'..' is not supported"):
            _validate_rule(rule)

    def test_content_patterns_must_be_list(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": "body.json",  # string, not list
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'content_patterns' must be a list"):
            _validate_rule(rule)

    def test_missing_body_form_field_name(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.form", "regex": ".*"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'field_name' is required"):
            _validate_rule(rule)

    def test_missing_header_name(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "headers", "regex": ".*"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'header_name' is required"):
            _validate_rule(rule)

    def test_missing_header_regex(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "headers", "header_name": "Authorization"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'regex' is required"):
            _validate_rule(rule)

    def test_invalid_field_type(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [{"field": "body.xml", "path": "$.key"}],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="invalid field type"):
            _validate_rule(rule)

    def test_empty_content_patterns(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": [],
            "replace_with": {"strategy": "static", "value": "X"},
        }
        _validate_rule(rule)  # should not raise

    def test_content_patterns_none(self):
        rule = {
            "name": "test",
            "hostnames": ["example.com"],
            "content_patterns": None,
            "replace_with": {"strategy": "static", "value": "X"},
        }
        with pytest.raises(ValueError, match="'content_patterns' must be a list"):
            _validate_rule(rule)


class TestTokenReplacerAddon:
    def test_load_config(self, tmp_path):
        config_data = {
            "global": {"log_replacements": True, "dry_run": False},
            "rules": [
                {
                    "name": "test rule",
                    "hostnames": ["api.example.com"],
                    "content_patterns": [
                        {
                            "field": "body.json",
                            "path": "$.api_key",
                            "regex": ".*",
                        }
                    ],
                    "replace_with": {"strategy": "static", "value": "REDACTED"},
                }
            ],
        }
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml.dump(config_data))
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon._load_config(str(config_file))
        assert len(addon.rules) == 1
        assert addon.rules[0]["name"] == "test rule"
        assert len(addon.rules[0]["_matchers"]) == 1

    def test_no_config(self):
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon._load_config("")
        assert addon.rules == []

    def test_empty_content_patterns_warns(self, tmp_path):
        """A rule with empty content_patterns should log a warning at load time."""
        config_data = {
            "global": {"log_replacements": False, "dry_run": False},
            "rules": [
                {
                    "name": "empty_rule",
                    "hostnames": ["example.com"],
                    "content_patterns": [],
                    "replace_with": {"strategy": "static", "value": "X"},
                }
            ],
        }
        config_file = tmp_path / "empty_config.yaml"
        config_file.write_text(yaml.dump(config_data))

        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        with patch("token_replacer.log") as mock_log:
            addon._load_config(str(config_file))
        mock_log.warning.assert_called()
        call_text = str(mock_log.warning.call_args)
        assert "no content_patterns" in call_text

    def test_should_replace_hostname_match(self):
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule1",
                "hostnames": ["api.example.com"],
                "content_patterns": [],
                "_matchers": [],
            },
            {
                "name": "rule2",
                "hostnames": ["other.com"],
                "content_patterns": [],
                "_matchers": [],
            },
        ]
        matching = addon._should_replace("api.example.com")
        assert len(matching) == 1
        assert matching[0]["name"] == "rule1"

    def test_should_replace_no_match(self):
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule1",
                "hostnames": ["api.example.com"],
                "content_patterns": [],
                "_matchers": [],
            },
        ]
        matching = addon._should_replace("other.com")
        assert len(matching) == 0

    def test_on_request_json_replacement(self):
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "body.json",
                        "path": "$.credentials.api_key",
                        "regex": None,
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.credentials.api_key",
                        regex=None,
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"credentials": {"api_key": "ak_real_secret_key_here"}}).encode()
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # Verify set_content was called (body was modified)
        flow.request.set_content.assert_called()
        # Verify the new content contains the replacement
        new_body = flow.request.set_content.call_args[0][0]
        new_data = json.loads(new_body)
        assert new_data["credentials"]["api_key"] == "REDACTED"

    def test_on_request_strips_port_from_host(self):
        """Hostname pattern should match even when flow.request.host includes a port."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key",
                        regex=None,
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "ak_secret"}).encode()
        flow = _make_flow(host="api.example.com:443", body=payload)

        addon.on_request(flow)

        # Should have replaced the token even with port in host
        flow.request.set_content.assert_called()
        new_data = json.loads(flow.request.set_content.call_args[0][0])
        assert new_data["api_key"] == "REDACTED"

    def test_on_request_no_match_hostname(self):
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key",
                        regex=None,
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "should_not_touch"}).encode()
        flow = _make_flow(host="other.com", body=payload)

        addon.on_request(flow)

        # set_content should NOT have been called
        flow.request.set_content.assert_not_called()

    def test_on_request_dry_run(self):
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key",
                        regex=None,
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": True, "dry_run": True}

        payload = json.dumps({"api_key": "ak_secret"}).encode()
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # In dry run, set_content should NOT be called
        flow.request.set_content.assert_not_called()
        # But log should have been called (module-level logger)
        with patch("token_replacer.log") as mock_log:
            # Re-run on_request with the patched logger
            addon.on_request(flow)
        assert mock_log.info.call_count > 0

    def test_cross_rule_no_interference(self):
        """Rule A's replacement value must not be re-matched by Rule B."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule_a",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.key_a", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_A"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.key_a",
                        regex=None,
                        strategy="static",
                        replacement_value="REPLACED_A",
                    )
                ],
            },
            {
                "name": "rule_b",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "body.json",
                        "path": "$.key_b",
                        "regex": "REPLACED_A",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_B"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.key_b",
                        regex="REPLACED_A",
                        strategy="static",
                        replacement_value="REPLACED_B",
                    )
                ],
            },
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"key_a": "original_a", "key_b": "original_b"}).encode()
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # Verify set_content was called exactly once (single-pass per type)
        assert flow.request.set_content.call_count == 1
        new_body = json.loads(flow.request.set_content.call_args[0][0])
        # Rule A replaced key_a
        assert new_body["key_a"] == "REPLACED_A"
        # Rule B's regex "REPLACED_A" must NOT match the original value of key_b
        # (which is "original_b"), so key_b remains unchanged.
        assert new_body["key_b"] == "original_b"

    def test_on_request_raw_body_no_chaining(self):
        """Raw body matchers must each operate on the original string, not chain."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule_a",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"TOKEN_A"}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_A"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"TOKEN_A",
                        strategy="static",
                        replacement_value="REPLACED_A",
                    )
                ],
            },
            {
                "name": "rule_b",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"TOKEN_B"}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_B"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"TOKEN_B",
                        strategy="static",
                        replacement_value="REPLACED_B",
                    )
                ],
            },
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = b"use TOKEN_A and TOKEN_B here"
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # Verify set_content was called exactly once
        assert flow.request.set_content.call_count == 1
        new_body = flow.request.set_content.call_args[0][0]
        assert b"REPLACED_A" in new_body
        assert b"REPLACED_B" in new_body
        # Neither replacement should have been applied twice
        assert new_body == b"use REPLACED_A and REPLACED_B here"

    def test_on_request_form_body_preserves_duplicate_keys(self):
        """Form body with duplicate keys must preserve all keys after replacement."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "form_token",
                "hostnames": ["login.example.com"],
                "content_patterns": [
                    {
                        "field": "body.form",
                        "field_name": "access_token",
                        "regex": r"at_[A-Za-z0-9]{32,}",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    FormBodyMatcher(
                        field_name="access_token",
                        regex=r"at_[A-Za-z0-9]{32,}",
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        # Form body with duplicate 'access_token' keys (both tokens match regex)
        payload = b"username=admin&access_token=at_abcdefghijklmnopqrstuvwxyz012345&other=val&access_token=at_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        flow = _make_flow(host="login.example.com", body=payload)

        addon.on_request(flow)

        # Verify set_content was called
        assert flow.request.set_content.called
        new_body = flow.request.set_content.call_args[0][0]
        # Decode and parse to check duplicate keys are preserved
        parsed = dict(urlparse.parse_qsl(new_body.decode("utf-8")))
        # Both tokens should be replaced
        assert parsed["access_token"] == "REDACTED"
        # Count occurrences of access_token in the encoded body
        assert new_body.count(b"access_token=") == 2
        # Both should be REDACTED
        assert new_body.count(b"REDACTED") == 2

    def test_on_request_raw_body_overlapping_regex(self):
        """Overlapping raw body regex matches must not corrupt the body."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule_a",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"TOKEN_A"}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_A"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"TOKEN_A",
                        strategy="static",
                        replacement_value="REPLACED_A",
                    )
                ],
            },
            {
                "name": "rule_b",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"TOKEN_A_B"}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_AB"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"TOKEN_A_B",
                        strategy="static",
                        replacement_value="REPLACED_AB",
                    )
                ],
            },
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        # TOKEN_A_B contains TOKEN_A — overlapping matches
        payload = b"prefix TOKEN_A_B suffix"
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # set_content should be called exactly once
        assert flow.request.set_content.call_count == 1
        new_body = flow.request.set_content.call_args[0][0]
        # The longer match (TOKEN_A_B) should take priority since it starts first
        # and the first matcher's replacement shouldn't re-trigger the second
        assert b"REPLACED_AB" in new_body
        # The overlapping TOKEN_A match should be skipped
        assert new_body == b"prefix REPLACED_AB suffix"

    def test_on_request_header_capture_group_preserved(self):
        """HeaderMatcher must preserve capture groups when replacing tokens."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "session_cookie",
                "hostnames": ["session.example.com"],
                "content_patterns": [
                    {"field": "headers", "header_name": "Cookie", "regex": r"(session=)[a-f0-9]{32}"},
                ],
                "replace_with": {"strategy": "hash", "value": "ignored"},
                "_matchers": [
                    HeaderMatcher(
                        header_name="Cookie",
                        regex=r"(session=)[a-f0-9]{32}",
                        strategy="hash",
                        replacement_value="ignored",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        import hashlib

        flow = _make_flow(host="session.example.com")
        original_cookie = "session=abcdef1234567890abcdef1234567890"

        flow.request.headers = _make_headers_mock({"Cookie": original_cookie})

        addon.on_request(flow)

        # Verify the header was modified
        flow.request.headers.__setitem__.assert_called()
        call_args = flow.request.headers.__setitem__.call_args[0]
        new_value = call_args[1]
        # The session= prefix must be preserved
        assert new_value.startswith("session=")
        # The value after session= should be a SHA-256 hash (64 hex chars)
        hash_part = new_value[len("session="):]
        assert len(hash_part) == 64
        assert all(c in '0123456789abcdef' for c in hash_part)

    def test_on_request_header_no_cross_rule_interference(self):
        """Header Phase 2 must operate on original values — one rule's replacement
        must not be re-matched by another rule's regex."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule_a",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "headers", "header_name": "Authorization",
                     "regex": r"(Bearer\s+)\S+"},
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_A"},
                "_matchers": [
                    HeaderMatcher(
                        header_name="Authorization",
                        regex=r"(Bearer\s+)\S+",
                        strategy="static",
                        replacement_value="REPLACED_A",
                    )
                ],
            },
            {
                "name": "rule_b",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "headers", "header_name": "Authorization",
                     "regex": "REPLACED_A"},
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_B"},
                "_matchers": [
                    HeaderMatcher(
                        header_name="Authorization",
                        regex="REPLACED_A",
                        strategy="static",
                        replacement_value="REPLACED_B",
                    )
                ],
            },
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        flow = _make_flow(host="api.example.com")
        flow.request.headers = _make_headers_mock(
            {"Authorization": "Bearer my_secret_token_12345"}
        )

        addon.on_request(flow)

        new_value = flow.request.headers.__setitem__.call_args[0][1]
        assert new_value == "Bearer REPLACED_A"
        # Rule B's regex should NOT have matched Rule A's replacement
        assert "REPLACED_B" not in new_value


    def test_on_request_query_string_replacement(self):
        """Query string tokens should be replaced via _set_query."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "query_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "body.query",
                        "field_name": "api_key",
                        "regex": r"ak_[A-Za-z0-9]{20,}",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    QueryStringMatcher(
                        field_name="api_key",
                        regex=r"ak_[A-Za-z0-9]{20,}",
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        # Build a flow with a query string in the URL
        flow = MagicMock()
        flow.request = MagicMock()
        flow.request.host = "api.example.com"
        flow.request.url = "http://api.example.com/path?api_key=ak_abcdefghijklmnopqrstuvwxyz012345&page=1"
        flow.request.get_content = MagicMock(return_value=b"")
        flow.request.set_content = MagicMock()
        flow.request.headers = MagicMock()
        flow.request.headers.keys = MagicMock(return_value=iter([]))
        flow.request.headers.__setitem__ = MagicMock()
        flow.request.headers.__delitem__ = MagicMock()
        # Simulate _get_query returning tuple of pairs
        flow.request.query = (
            ("api_key", "ak_abcdefghijklmnopqrstuvwxyz012345"),
            ("page", "1"),
        )
        # Track calls to _set_query
        flow.request._set_query = MagicMock()
        flow.request._set_query.side_effect = lambda pairs: None

        addon.on_request(flow)

        # _set_query should have been called with the modified pairs
        flow.request._set_query.assert_called()
        call_pairs = flow.request._set_query.call_args[0][0]
        # The api_key should be replaced
        api_key_value = dict(call_pairs).get("api_key", "")
        # There may be duplicate keys, so check all values
        key_values = [v for k, v in call_pairs if k == "api_key"]
        assert "REDACTED" in key_values
        # The page parameter should be unchanged
        page_values = [v for k, v in call_pairs if k == "page"]
        assert "1" in page_values

    def test_on_request_query_string_dry_run(self):
        """In dry_run mode, query string tokens are detected but not modified."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "query_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "body.query",
                        "field_name": "api_key",
                        "regex": r"ak_[A-Za-z0-9]{20,}",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    QueryStringMatcher(
                        field_name="api_key",
                        regex=r"ak_[A-Za-z0-9]{20,}",
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": True, "dry_run": True}

        flow = MagicMock()
        flow.request = MagicMock()
        flow.request.host = "api.example.com"
        flow.request.url = "http://api.example.com/path?api_key=ak_abcdefghijklmnopqrstuvwxyz012345"
        flow.request.get_content = MagicMock(return_value=b"")
        flow.request.set_content = MagicMock()
        flow.request.headers = MagicMock()
        flow.request.headers.keys = MagicMock(return_value=iter([]))
        flow.request.headers.__setitem__ = MagicMock()
        flow.request.headers.__delitem__ = MagicMock()
        flow.request.query = (
            ("api_key", "ak_abcdefghijklmnopqrstuvwxyz012345"),
        )
        flow.request._set_query = MagicMock()

        addon.on_request(flow)

        # In dry run, _set_query should NOT have been called
        flow.request._set_query.assert_not_called()


# ---------------------------------------------------------------------------
# on_response integration tests
# ---------------------------------------------------------------------------

class TestOnResponse:
    def test_on_response_json_replacement(self):
        """Response JSON body tokens should be replaced just like request tokens."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key", regex=None,
                        strategy="static", replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "ak_secret_in_response"}).encode()
        flow = _make_response_flow(host="api.example.com", body=payload)

        addon.on_response(flow)

        # Verify set_content was called on the response
        flow.response.set_content.assert_called()
        new_body = json.loads(flow.response.set_content.call_args[0][0])
        assert new_body["api_key"] == "REDACTED"

    def test_on_response_no_match_hostname(self):
        """No replacement when response hostname doesn't match."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key", regex=None,
                        strategy="static", replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "keep_this"}).encode()
        flow = _make_response_flow(host="other.com", body=payload)

        addon.on_response(flow)

        flow.response.set_content.assert_not_called()

    def test_on_response_dry_run(self):
        """In dry_run mode, response tokens are logged but not modified."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key", regex=None,
                        strategy="static", replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": True, "dry_run": True}

        payload = json.dumps({"api_key": "ak_secret"}).encode()
        flow = _make_response_flow(host="api.example.com", body=payload)

        addon.on_response(flow)

        # In dry run, set_content should NOT be called
        flow.response.set_content.assert_not_called()

    def test_on_response_log_replacements_off(self):
        """When log_replacements is False, no logging should occur."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key", regex=None,
                        strategy="static", replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "ak_secret"}).encode()
        flow = _make_response_flow(host="api.example.com", body=payload)

        with patch("token_replacer.log") as mock_log:
            addon.on_response(flow)

        # No logging should have occurred
        mock_log.info.assert_not_called()

    def test_on_response_raw_body(self):
        """Raw body tokens in responses should be replaced."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "jwt_in_response",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED_JWT"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                        strategy="static", replacement_value="REDACTED_JWT",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = b'Some text eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123XYZ more text'
        flow = _make_response_flow(host="api.example.com", body=payload)

        addon.on_response(flow)

        flow.response.set_content.assert_called()
        new_body = flow.response.set_content.call_args[0][0]
        assert b"REDACTED_JWT" in new_body
        assert b"eyJhbGciOiJIUzI1NiJ9" not in new_body

    def test_on_response_header_replacement(self):
        """Header tokens in responses should be replaced."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "bearer_token",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "headers",
                        "header_name": "X-Auth-Token",
                        "regex": r"(token=)[a-f0-9]{32}",
                    }
                ],
                "replace_with": {"strategy": "hash", "value": "ignored"},
                "_matchers": [
                    HeaderMatcher(
                        header_name="X-Auth-Token",
                        regex=r"(token=)[a-f0-9]{32}",
                        strategy="hash", replacement_value="ignored",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        import hashlib

        flow = _make_response_flow(host="api.example.com")
        original_token = "token=abcdef1234567890abcdef1234567890"
        flow.response.headers = _make_headers_mock({"X-Auth-Token": original_token})

        addon.on_response(flow)

        flow.response.headers.__setitem__.assert_called()
        call_args = flow.response.headers.__setitem__.call_args[0]
        new_value = call_args[1]
        assert new_value.startswith("token=")
        hash_part = new_value[len("token="):]
        assert len(hash_part) == 64
        assert all(c in '0123456789abcdef' for c in hash_part)

    def test_on_response_removes_chunked_encoding(self):
        """When modifying a chunked response body, Transfer-Encoding must be removed."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "json_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.json", "path": "$.api_key", "regex": None}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    JsonBodyMatcher(
                        json_path="$.api_key", regex=None,
                        strategy="static", replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "ak_secret"}).encode()
        flow = _make_response_flow(host="api.example.com", body=payload)

        # Simulate mitmproxy's real headers behavior with Transfer-Encoding
        headers_dict: dict[str, str] = {
            "Transfer-Encoding": "chunked",
            "Content-Type": "application/json",
        }
        flow.response.headers = _make_headers_mock(headers_dict)

        addon.on_response(flow)

        # Transfer-Encoding should be removed
        assert "Transfer-Encoding" not in headers_dict
        # Content-Length should be set
        assert "Content-Length" in headers_dict
        # Body should be modified
        new_body = json.loads(flow.response.set_content.call_args[0][0])
        assert new_body["api_key"] == "REDACTED"


# ---------------------------------------------------------------------------
# Edge case tests for hostname matching
# ---------------------------------------------------------------------------


class TestIsRawRegexEdgeCases:
    """Test _is_raw_regex with all regex metacharacter edge cases."""

    def test_bracket_open_is_regex(self):
        """Pattern containing '[' should be treated as full regex."""
        assert _is_raw_regex("host[1]") is True

    def test_bracket_close_is_regex(self):
        """Pattern containing ']' should be treated as full regex."""
        assert _is_raw_regex("host]") is True

    def test_plus_is_regex(self):
        """Pattern containing '+' should be treated as full regex."""
        assert _is_raw_regex("host+") is True

    def test_curly_open_is_regex(self):
        """Pattern containing '{' should be treated as full regex."""
        assert _is_raw_regex("host{") is True

    def test_curly_close_is_regex(self):
        """Pattern containing '}' should be treated as full regex."""
        assert _is_raw_regex("host}") is True

    def test_paren_open_is_regex(self):
        """Pattern containing '(' should be treated as full regex."""
        assert _is_raw_regex("host(") is True

    def test_paren_close_is_regex(self):
        """Pattern containing ')' should be treated as full regex."""
        assert _is_raw_regex("host)") is True

    def test_pipe_is_regex(self):
        """Pattern containing '|' should be treated as full regex."""
        assert _is_raw_regex("host|other") is True

    def test_dot_is_glob_not_regex(self):
        """Pattern containing '.' is NOT treated as regex — it is escaped to match literal dot."""
        assert _is_raw_regex("host.example") is False

    def test_dollar_is_regex(self):
        """Pattern containing '$' should be treated as full regex."""
        assert _is_raw_regex("example$") is True

    def test_caret_is_regex(self):
        """Pattern containing '^' should be treated as full regex."""
        assert _is_raw_regex("^example") is True

    def test_star_and_qmark_are_glob(self):
        """Pattern with only '*' and '?' should be treated as glob."""
        assert _is_raw_regex("*.example.com?") is False

    def test_empty_pattern_is_glob(self):
        """Empty pattern contains no metacharacters, so it's a glob."""
        assert _is_raw_regex("") is False


class TestMatchesHostnameEdgeCases:
    """Test _matches_hostname with regex patterns containing metacharacters."""

    def test_bracket_pattern_is_regex(self):
        """A hostname pattern with '[' is treated as regex and matches."""
        assert _matches_hostname("host1", ["host[0-9]+"]) is True

    def test_bracket_pattern_no_match(self):
        """Regex pattern with '[' that doesn't match the hostname."""
        assert _matches_hostname("hostx", ["host[0-9]+"]) is False

    def test_dot_pattern_matches_literal_dot(self):
        """A pattern with '.' (treated as regex) matches the hostname."""
        assert _matches_hostname("host.example", ["host.example"]) is True

    def test_dot_pattern_does_not_match_any_char(self):
        """Dot in glob pattern matches only literal dot, not any character."""
        assert _matches_hostname("hostXexample", ["host.example"]) is False

    def test_full_regex_pattern(self):
        """Full regex pattern with anchors should match correctly."""
        assert _matches_hostname("auth.api.example.com", ["^auth\\..*\\.example\\.com$"]) is True

    def test_pipe_pattern(self):
        """Pipe character in pattern enables regex alternation."""
        assert _matches_hostname("host1", ["host[1|2]"]) is True
        assert _matches_hostname("host3", ["host[1|2]"]) is False

    def test_glob_star_matches_multiple_segments(self):
        """Glob '*' should match across dots (any number of characters)."""
        assert _matches_hostname("a.b.c.example.com", ["*.example.com"]) is True

    def test_glob_question_matches_single_char(self):
        """Glob '?' converts to regex '.' and matches exactly one character."""
        assert _matches_hostname("a.example.com", ["?.example.com"]) is True
        assert _matches_hostname("ab.example.com", ["?.example.com"]) is False


class TestStripPortEdgeCases:
    """Additional _strip_port edge cases."""

    def test_port_zero(self):
        """Port 0 should be stripped."""
        assert _strip_port("localhost:0") == "localhost"

    def test_high_port_number(self):
        """High port numbers should be handled correctly."""
        assert _strip_port("example.com:65535") == "example.com"

    def test_single_colon_only(self):
        """A string that is just ':' should be returned as-is (bare IPv6-like)."""
        # ':' has only one colon, so rsplit(':', 1) would strip it — but this
        # is an edge case that shouldn't occur in practice (mitmproxy always
        # provides a hostname with port).
        result = _strip_port(":")
        # count(':') == 1, so it hits rsplit and returns empty string
        assert result == ""

    def test_double_bracket_ipv6(self):
        """Bracketed IPv6 with port should work correctly."""
        assert _strip_port("[::1]:8080") == "[::1]"

    def test_ipv6_with_terminating_bracket(self):
        """Bracketed IPv6 with no port after ']' should be returned unchanged."""
        assert _strip_port("[::1]") == "[::1]"


# ---------------------------------------------------------------------------
# Edge case tests for form/query/raw body handling
# ---------------------------------------------------------------------------


class TestFormBodyNonUtf8:
    """Test form body handling with non-UTF-8 encoded bodies."""

    def test_form_with_invalid_utf8_bytes(self):
        """Form body with invalid UTF-8 bytes should not crash the addon."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "form_token",
                "hostnames": ["login.example.com"],
                "content_patterns": [
                    {
                        "field": "body.form",
                        "field_name": "access_token",
                        "regex": r"at_[A-Za-z0-9]{32,}",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    FormBodyMatcher(
                        field_name="access_token",
                        regex=r"at_[A-Za-z0-9]{32,}",
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        # Body with invalid UTF-8 bytes mixed with valid form data
        payload = b"username=admin&access_token=at_abcdefghijklmnopqrstuvwxyz012345&bad=\xff\xfe"
        flow = _make_flow(host="login.example.com", body=payload)

        # Should not raise, even though the body has invalid UTF-8
        addon.on_request(flow)

        # The valid token should still be replaced
        flow.request.set_content.assert_called()
        new_body = flow.request.set_content.call_args[0][0]
        assert b"REDACTED" in new_body


class TestRawBodyNoMatch:
    """Test that raw body matchers with no matches don't modify the body."""

    def test_no_match_no_modification(self):
        """When raw body matcher finds no matches, set_content should not be called."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "jwt_in_body",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"}
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED_JWT"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                        strategy="static",
                        replacement_value="REDACTED_JWT",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = b"This is just regular text with no JWT tokens"
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # set_content should NOT be called since no matches were found
        flow.request.set_content.assert_not_called()


class TestQueryNoChange:
    """Test query string replacement when the value doesn't actually change."""

    def test_query_no_match_does_not_call_set_query(self):
        """When query matcher finds no matches, _set_query should not be called."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "query_api_key",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "body.query",
                        "field_name": "api_key",
                        "regex": r"ak_[A-Za-z0-9]{20,}",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REDACTED"},
                "_matchers": [
                    QueryStringMatcher(
                        field_name="api_key",
                        regex=r"ak_[A-Za-z0-9]{20,}",
                        strategy="static",
                        replacement_value="REDACTED",
                    )
                ],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        flow = MagicMock()
        flow.request = MagicMock()
        flow.request.host = "api.example.com"
        flow.request.get_content = MagicMock(return_value=b"")
        flow.request.set_content = MagicMock()
        flow.request.headers = MagicMock()
        flow.request.headers.keys = MagicMock(return_value=iter([]))
        flow.request.headers.__setitem__ = MagicMock()
        flow.request.headers.__delitem__ = MagicMock()
        # Query with no matching api_key
        flow.request.query = (("page", "1"), ("sort", "asc"))
        flow.request._set_query = MagicMock()

        addon.on_request(flow)

        # _set_query should NOT have been called
        flow.request._set_query.assert_not_called()


class TestEmptyMatchersRule:
    """Test behavior when a rule has no matchers (empty content_patterns)."""

    def test_empty_matchers_rule_does_nothing(self):
        """A rule with no content_patterns should not cause errors."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "empty_rule",
                "hostnames": ["api.example.com"],
                "content_patterns": [],
                "replace_with": {"strategy": "static", "value": "X"},
                "_matchers": [],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "should_not_touch"}).encode()
        flow = _make_flow(host="api.example.com", body=payload)

        # Should not raise or modify anything
        addon.on_request(flow)

        flow.request.set_content.assert_not_called()

    def test_empty_matchers_rule_in_response(self):
        """A rule with no matchers should not cause errors in response processing."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "empty_rule",
                "hostnames": ["api.example.com"],
                "content_patterns": [],
                "replace_with": {"strategy": "static", "value": "X"},
                "_matchers": [],
            }
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = json.dumps({"api_key": "should_not_touch"}).encode()
        flow = _make_response_flow(host="api.example.com", body=payload)

        # Should not raise or modify anything
        addon.on_response(flow)

        flow.response.set_content.assert_not_called()


class TestHeaderMultipleCaptureGroups:
    """Test header replacement with multiple capture groups."""

    def test_header_single_capture_group_still_works(self):
        """Single capture group pattern should still work after multi-group refactor."""
        matcher = HeaderMatcher(
            header_name="Cookie",
            regex=r"(session=)[a-f0-9]{32}",
            strategy="hash",
            replacement_value="ignored",
        )

        headers = {"Cookie": "session=abcdef1234567890abcdef1234567890"}
        found = matcher.match_and_replace(headers)
        assert len(found) == 1
        new_value = headers["Cookie"]
        assert new_value.startswith("session=")
        hash_part = new_value[len("session="):]
        assert len(hash_part) == 64

    def test_header_multi_group_preserves_all_groups(self):
        """When all parts of a match are captured in groups, all groups are
        preserved and the strategy is applied to non-captured portions only."""
        # Pattern: (key1=)(value1)(key2=)(value2)(suffix=)
        # All 5 groups cover the entire match, so nothing is replaced.
        matcher = HeaderMatcher(
            header_name="X-Custom",
            regex=r"(key1=)([a-f0-9]{8})(key2=)([a-f0-9]{8})(suffix=)",
            strategy="hash",
            replacement_value="ignored",
        )

        headers = {"X-Custom": "key1=11111111key2=22222222suffix="}
        found = matcher.match_and_replace(headers)
        assert len(found) == 1
        # All capture groups are preserved
        new_value = headers["X-Custom"]
        assert new_value == "key1=11111111key2=22222222suffix="

    def test_header_multi_group_replaces_non_captured_portions(self):
        """Non-captured portions between groups are replaced with strategy."""
        # Pattern: (key=)(secret1)DELIM(secret2)DELIM — groups cover key, secret1,
        # secret2. The 'DELIM' text between groups is non-captured and gets replaced.
        matcher = HeaderMatcher(
            header_name="X-Custom",
            regex=r"(key=)([a-f0-9]{8})DELIM([a-f0-9]{8})DELIM",
            strategy="static",
            replacement_value="REPLACED",
        )

        headers = {"X-Custom": "key=11111111DELIM22222222DELIM"}
        found = matcher.match_and_replace(headers)
        assert len(found) == 1
        new_value = headers["X-Custom"]
        # Groups are preserved, non-captured DELIMs are replaced
        assert new_value == "key=11111111REPLACED22222222REPLACED"


class TestHeaderNoCrossRuleInterferenceInResponse:
    """Test that cross-rule interference is prevented in response processing."""

    def test_response_header_no_cross_rule_interference(self):
        """Response header replacement should also prevent cross-rule interference."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule_a",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "headers",
                        "header_name": "Authorization",
                        "regex": r"(Bearer\s+)\S+",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_A"},
                "_matchers": [
                    HeaderMatcher(
                        header_name="Authorization",
                        regex=r"(Bearer\s+)\S+",
                        strategy="static",
                        replacement_value="REPLACED_A",
                    )
                ],
            },
            {
                "name": "rule_b",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {
                        "field": "headers",
                        "header_name": "Authorization",
                        "regex": "REPLACED_A",
                    }
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_B"},
                "_matchers": [
                    HeaderMatcher(
                        header_name="Authorization",
                        regex="REPLACED_A",
                        strategy="static",
                        replacement_value="REPLACED_B",
                    )
                ],
            },
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        flow = _make_response_flow(host="api.example.com")
        flow.response.headers = _make_headers_mock(
            {"Authorization": "Bearer my_secret_token_12345"}
        )

        addon.on_response(flow)

        new_value = flow.response.headers.__setitem__.call_args[0][1]
        assert new_value == "Bearer REPLACED_A"
        assert "REPLACED_B" not in new_value


class TestRawBodyOverlappingReplacements:
    """Test raw body replacement with overlapping regex matches from different rules."""

    def test_raw_body_overlapping_from_different_rules(self):
        """Overlapping regex matches from different rules should be resolved correctly."""
        addon = TokenReplacerAddon.__new__(TokenReplacerAddon)
        addon.rules = [
            {
                "name": "rule_a",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"SHORT"}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_SHORT"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"SHORT",
                        strategy="static",
                        replacement_value="REPLACED_SHORT",
                    )
                ],
            },
            {
                "name": "rule_b",
                "hostnames": ["api.example.com"],
                "content_patterns": [
                    {"field": "body.raw", "regex": r"SHORT_TOKEN"}
                ],
                "replace_with": {"strategy": "static", "value": "REPLACED_LONG"},
                "_matchers": [
                    RawBodyMatcher(
                        regex=r"SHORT_TOKEN",
                        strategy="static",
                        replacement_value="REPLACED_LONG",
                    )
                ],
            },
        ]
        addon.global_config = {"log_replacements": False, "dry_run": False}

        payload = b"before SHORT_TOKEN after"
        flow = _make_flow(host="api.example.com", body=payload)

        addon.on_request(flow)

        # set_content should be called exactly once
        assert flow.request.set_content.call_count == 1
        new_body = flow.request.set_content.call_args[0][0]
        # The longer match (SHORT_TOKEN) should take priority
        assert b"REPLACED_LONG" in new_body
        # The shorter match (SHORT) should be skipped (overlapping)
        assert new_body == b"before REPLACED_LONG after"


# ---------------------------------------------------------------------------
# _resolve_env_refs tests
# ---------------------------------------------------------------------------


class TestResolveEnvRefs:
    def test_env_var_present(self, monkeypatch):
        monkeypatch.setenv("TEST_REDACT_TOKEN", "MASKED_SECRET")
        assert _resolve_env_refs("${ENV:TEST_REDACT_TOKEN}") == "MASKED_SECRET"

    def test_env_var_absent_with_default(self, monkeypatch):
        # Ensure the env var is not set
        monkeypatch.delenv("TEST_MISSING_VAR", raising=False)
        result = _resolve_env_refs("${ENV:TEST_MISSING_VAR,fallback_value}")
        assert result == "fallback_value"

    def test_env_var_absent_no_default_raises(self, monkeypatch):
        monkeypatch.delenv("TEST_MISSING_REQUIRED", raising=False)
        with pytest.raises(KeyError, match="TEST_MISSING_REQUIRED"):
            _resolve_env_refs("${ENV:TEST_MISSING_REQUIRED}")

    def test_plain_string_unchanged(self):
        assert _resolve_env_refs("plain text") == "plain text"
        assert _resolve_env_refs("${ENV:something else") == "${ENV:something else"
        assert _resolve_env_refs("literal $ENV:text") == "literal $ENV:text"
        assert _resolve_env_refs("${ENV:}") == "${ENV:}"

    def test_env_var_with_empty_value(self, monkeypatch):
        monkeypatch.setenv("TEST_EMPTY", "")
        assert _resolve_env_refs("${ENV:TEST_EMPTY}") == ""

    def test_e2e_env_var_in_config(self, monkeypatch, tmp_path):
        """Env-var reference in replace_with.value must resolve during config load
        and produce a working matcher at request time."""
        monkeypatch.setenv("TEST_REDACT_E2E", "E2E_REDACTED")

        config_data = {
            "global": {"log_replacements": False, "dry_run": False},
            "rules": [
                {
                    "name": "env_var_rule",
                    "hostnames": ["api.example.com"],
                    "content_patterns": [
                        {
                            "field": "body.json",
                            "path": "$.api_key",
                            "regex": ".*",
                        }
                    ],
                    "replace_with": {"strategy": "static", "value": "${ENV:TEST_REDACT_E2E}"},
                }
            ],
        }
        config_file = tmp_path / "env_config.yaml"
        config_file.write_text(yaml.dump(config_data))

        addon = TokenReplacerAddon(str(config_file))
        assert len(addon.rules) == 1
        matcher = addon.rules[0]["_matchers"][0]
        # The matcher should have the resolved value, not the env-ref syntax
        assert matcher.replacement_value == "E2E_REDACTED"

        payload = json.dumps({"api_key": "ak_real_secret_here"}).encode()
        flow = _make_flow(host="api.example.com", body=payload)
        addon.on_request(flow)

        new_data = json.loads(flow.request.set_content.call_args[0][0])
        assert new_data["api_key"] == "E2E_REDACTED"

    def test_e2e_env_var_missing_raises_at_load(self, monkeypatch, tmp_path):
        """A missing env var without default must raise KeyError at config load."""
        monkeypatch.delenv("MISSING_REQUIRED_E2E", raising=False)

        config_data = {
            "global": {"log_replacements": False, "dry_run": False},
            "rules": [
                {
                    "name": "missing_env_rule",
                    "hostnames": ["api.example.com"],
                    "content_patterns": [
                        {
                            "field": "body.json",
                            "path": "$.api_key",
                            "regex": ".*",
                        }
                    ],
                    "replace_with": {"strategy": "static", "value": "${ENV:MISSING_REQUIRED_E2E}"},
                }
            ],
        }
        config_file = tmp_path / "missing_env.yaml"
        config_file.write_text(yaml.dump(config_data))

        with pytest.raises(KeyError, match="MISSING_REQUIRED_E2E"):
            TokenReplacerAddon(str(config_file))

"""Tests for yaml_strict.py — duplicate mapping keys are a parse error."""

import sys
from typing import TYPE_CHECKING

import pytest
import yaml

from yaml_strict import (
    DuplicateKeyError,
    check_duplicate_keys,
    load_yaml_file,
    load_yaml_strict,
)

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True


# ─── The regression this exists for ────────────────────────────────────────


#: The exact shape that shipped a silently-dropped port: an entry added above
#: the seeded empty list rather than replacing it.
PUBLISH_DUPLICATE = """
nested_containers:
  enabled: true
  ports:
    expose: localhost
    publish:
      - "18080:8080"
    publish: []
"""


def test_safe_load_silently_drops_the_earlier_value() -> None:
    """Characterise PyYAML's behaviour — the reason this module exists.

    If a future PyYAML starts rejecting duplicates on its own, this test fails
    and the strict loader can be reconsidered.
    """
    data = yaml.safe_load(PUBLISH_DUPLICATE)
    assert data["nested_containers"]["ports"]["publish"] == []


def test_strict_load_rejects_the_same_document() -> None:
    with pytest.raises(DuplicateKeyError) as exc:
        load_yaml_strict(PUBLISH_DUPLICATE)
    assert "publish" in str(exc.value)


def test_error_points_at_the_duplicate_line() -> None:
    """The mark must name the *second* occurrence, which is the line to delete."""
    with pytest.raises(DuplicateKeyError) as exc:
        load_yaml_strict(PUBLISH_DUPLICATE)
    # Line 8 (1-indexed) is the second `publish:`; PyYAML marks are 0-indexed.
    assert exc.value.problem_mark is not None
    assert exc.value.problem_mark.line + 1 == 8


# ─── Drop-in compatibility with yaml.safe_load ─────────────────────────────


@pytest.mark.parametrize(
    "document",
    [
        "",
        "a: 1\nb: 2\n",
        "- 1\n- 2\n",
        "nested:\n  deep:\n    key: value\n",
        "list_of_maps:\n  - a: 1\n  - a: 2\n",  # same key in *different* maps is fine
        "a: {x: 1, y: 2}\n",  # flow mapping, no duplicates
    ],
)
def test_matches_safe_load_on_valid_documents(document: str) -> None:
    assert load_yaml_strict(document) == yaml.safe_load(document)


def test_is_a_yaml_error_so_existing_handlers_catch_it() -> None:
    """Callers already wrap loads in ``except yaml.YAMLError``; keep that working."""
    with pytest.raises(yaml.YAMLError):
        load_yaml_strict("a: 1\na: 2\n")


def test_rejects_python_object_tags_like_safe_load() -> None:
    """StrictLoader must inherit SafeLoader's refusal to construct objects."""
    with pytest.raises(yaml.YAMLError):
        load_yaml_strict("!!python/object/apply:os.system ['echo pwned']\n")


def test_duplicates_detected_in_flow_mappings() -> None:
    with pytest.raises(DuplicateKeyError):
        load_yaml_strict("a: {x: 1, x: 2}\n")


def test_duplicates_detected_in_nested_mappings() -> None:
    with pytest.raises(DuplicateKeyError):
        load_yaml_strict("outer:\n  inner:\n    k: 1\n    k: 2\n")


def test_non_string_duplicate_keys_are_caught() -> None:
    with pytest.raises(DuplicateKeyError):
        load_yaml_strict("1: a\n1: b\n")


def test_merge_keys_are_not_treated_as_duplicates() -> None:
    """``<<`` may legitimately appear alongside the keys it merges."""
    document = "base: &base\n  a: 1\nderived:\n  <<: *base\n  b: 2\n"
    assert load_yaml_strict(document)["derived"] == {"a": 1, "b": 2}


def test_explicit_key_may_override_a_merged_key() -> None:
    """Overriding a merged key is the point of ``<<``, not a duplicate.

    Guards the scan-before-flatten ordering: the base constructor prepends the
    merged pairs to the node, so a post-flatten scan would reject this.
    """
    document = "base: &base\n  a: 1\nderived:\n  <<: *base\n  a: 2\n"
    assert load_yaml_strict(document)["derived"] == {"a": 2}


def test_unhashable_key_still_raises_pyyaml_own_error() -> None:
    """A list-as-key is PyYAML's error to report, not ours — do not mask it."""
    with pytest.raises(yaml.YAMLError) as exc:
        load_yaml_strict("? [1, 2]\n: value\n")
    assert not isinstance(exc.value, DuplicateKeyError)


# ─── File helpers ──────────────────────────────────────────────────────────


def test_load_yaml_file_reads_and_rejects(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(PUBLISH_DUPLICATE)
    with pytest.raises(DuplicateKeyError):
        load_yaml_file(path)


def test_check_duplicate_keys_reports_path_and_position(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(PUBLISH_DUPLICATE)
    errors = check_duplicate_keys(path)
    assert len(errors) == 1
    assert str(path) in errors[0]
    assert "line 8" in errors[0]
    assert "publish" in errors[0]


def test_check_duplicate_keys_passes_clean_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("a: 1\nb: 2\n")
    assert check_duplicate_keys(path) == []


def test_check_duplicate_keys_ignores_missing_file(tmp_path: Path) -> None:
    """Absence is the caller's concern — allowlist.yaml is optional."""
    assert check_duplicate_keys(tmp_path / "nope.yaml") == []


def test_check_duplicate_keys_ignores_other_syntax_errors(tmp_path: Path) -> None:
    """Other malformed YAML is reported by whoever consumes the file."""
    path = tmp_path / "config.yaml"
    path.write_text("a: [1, 2\n")
    assert check_duplicate_keys(path) == []

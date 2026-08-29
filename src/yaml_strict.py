import sys

sys.dont_write_bytecode = True

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

"""Strict YAML parser enforcing unique mapping keys to prevent silent config overrides."""


class DuplicateKeyError(yaml.MarkedYAMLError):
    """Exception raised when a YAML mapping contains duplicate keys."""


class StrictLoader(yaml.SafeLoader):
    """Custom YAML SafeLoader that raises DuplicateKeyError when duplicate keys are encountered."""

    #: YAML's merge key.
    _MERGE_TAG = "tag:yaml.org,2002:merge"

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set = set()
        for key_node, _ in node.value:
            if key_node.tag == self._MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:
                continue
            if duplicate:
                raise DuplicateKeyError(
                    context="while constructing a mapping",
                    context_mark=node.start_mark,
                    problem=(
                        f"duplicate key {key!r} — YAML keeps the last one and silently discards the earlier value"
                    ),
                    problem_mark=key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_yaml_strict(stream: str | bytes) -> Any:
    """Parse a YAML string strictly, raising DuplicateKeyError if duplicate mapping keys are present."""
    return yaml.load(stream, Loader=StrictLoader)  # noqa: S506 — StrictLoader derives from SafeLoader


def load_yaml_file(path: Path) -> Any:
    """Load and strictly parse a YAML file from disk."""
    return load_yaml_strict(path.read_text())


def check_duplicate_keys(path: Path) -> list[str]:
    """Check a YAML file for duplicate mapping keys and return any formatted error messages."""
    if not path.exists():
        return []
    try:
        load_yaml_file(path)
    except DuplicateKeyError as e:
        mark = e.problem_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return [f"  {path}{where}: {e.problem}"]
    except OSError, yaml.YAMLError:
        return []
    return []

import sys

sys.dont_write_bytecode = True

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

"""Strict YAML loading: duplicate mapping keys are an error, not a silent drop.

The YAML spec says mapping keys must be unique, but PyYAML's ``safe_load``
does not enforce it — a repeated key overwrites the earlier one and parsing
succeeds. For hand-edited config that is the worst possible outcome: the file
looks right, every validator passes, the run starts, and the setting simply
does not take effect. The failure surfaces much later as absent behaviour with
nothing to grep for.

The case that motivated this:

    ports:
      publish:
        - "18080:8080"
      publish: []          # ← wins; the entry above is discarded

Adding an entry above the seeded ``publish: []`` rather than replacing it is
an easy edit to make, and nothing downstream can tell the difference between
"the user asked for no ports" and "the user's ports were thrown away".

``StrictLoader`` rejects that at parse time and points at the offending line.
It subclasses ``yaml.SafeLoader``, so it is exactly as safe as ``safe_load``
(no arbitrary object construction) and raises ``yaml.YAMLError`` subclasses,
which existing ``except yaml.YAMLError`` handlers already catch.
"""


class DuplicateKeyError(yaml.MarkedYAMLError):
    """A mapping contains the same key twice.

    A ``MarkedYAMLError`` subclass so ``str(e)`` carries the file position of
    both the mapping and the repeated key, and so callers that already handle
    ``yaml.YAMLError`` keep working without change.
    """


class StrictLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses duplicate mapping keys."""

    #: YAML's merge key. ``<<`` may appear next to the keys it merges, and an
    #: explicit key overriding a merged one is the feature working as intended
    #: — neither is a duplicate. Skipped here for a second reason too: the base
    #: constructor resolves ``<<`` in ``flatten_mapping`` before it constructs
    #: anything, so asking for its value directly raises "could not determine a
    #: constructor for the tag".
    _MERGE_TAG = "tag:yaml.org,2002:merge"

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        # Scans the node as written, before the base class flattens merges into
        # it — flattening prepends the merged pairs, which would make a lawful
        # override indistinguishable from a repeated key.
        seen: set = set()
        for key_node, _ in node.value:
            if key_node.tag == self._MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:
                # Unhashable key (a list or dict used as a key). Not our error
                # to report — the base constructor raises "unhashable key" with
                # its own marks, so leave it alone rather than masking it.
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
    """``yaml.safe_load`` that raises ``DuplicateKeyError`` on a repeated key.

    A drop-in replacement for ``yaml.safe_load``: same accepted input, same
    return values (including ``None`` for an empty document), same exception
    base class.
    """
    return yaml.load(stream, Loader=StrictLoader)  # noqa: S506 — StrictLoader derives from SafeLoader


def load_yaml_file(path: Path) -> Any:
    """Strict-load a YAML file. Raises ``OSError`` or ``yaml.YAMLError``."""
    return load_yaml_strict(path.read_text())


def check_duplicate_keys(path: Path) -> list[str]:
    """Parse ``path`` strictly and report duplicate keys as validation errors.

    Returns a list of human-readable messages, empty when the file parses
    cleanly **or does not exist** — absence is the caller's concern, and every
    per-project YAML file except ``config.yaml`` is optional.

    Only duplicate keys are reported. Other YAML syntax errors are left to the
    component that actually consumes the file, which can say what the file was
    for; a parse error here would just duplicate that with less context.
    """
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

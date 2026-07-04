#!/usr/bin/env python3
"""Validate the seed models.json schema before commit.

Checks that pi-coding-agent/default/agent/models.json conforms to the
expected schema (serverCustomParameters.hfModels structure, flag types,
and --chat-template-file path existence).

Exit codes:
    0: Validation passed
    1: Validation failed (schema errors or missing files)
"""

import sys
from pathlib import Path

# Add src to path for config_schema import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from config_schema import validate_models


def main() -> int:
    """Validate the seed models.json file."""
    seed_models_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "pi-coding-agent"
        / "default"
        / "agent"
        / "models.json"
    )

    if not seed_models_path.exists():
        print(f"ERROR: Seed models.json not found: {seed_models_path}", file=sys.stderr)
        return 1

    is_valid, errors = validate_models(seed_models_path)

    if not is_valid:
        print(f"Seed models.json validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    print("Seed models.json: schema valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())

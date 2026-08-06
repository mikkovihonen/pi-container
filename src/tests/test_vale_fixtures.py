"""
Integration tests for Vale rule fixtures.

Each test runs `vale` against one fixture and asserts the rule names that fire.
Positive fixtures must fire their named rule and nothing else.
Negative fixtures must not fire their named rule.
A negative fixture may fire other rules — those are documented in the test body.

Run with:
    python -m pytest src/tests/test_vale_fixtures.py -v
"""

import subprocess
from pathlib import Path

import pytest

# Resolve the vale/ directory relative to this test file.
# src/tests/test_vale_fixtures.py -> src -> pi-coding-agent -> vale
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALE_DIR = REPO_ROOT / "pi-coding-agent" / "vale"

# Skip if Vale is not installed (CI without the container).
VALE_BIN = subprocess.run(["which", "vale"], capture_output=True, text=True).stdout.strip()
pytestmark = pytest.mark.skipif(not VALE_BIN, reason="vale not installed")

# Each fixture maps to the rule name it MUST fire (positive) or MUST NOT fire
# (negative). Negative fixtures are listed with an optional `also_fires` set of
# rule names they are known to trigger for unrelated reasons — those are not
# failures.
FIXTURES = {
    "contractions_positive.md": {
        "must_fire": {"STE100.Contractions"},
        "must_not_fire": set(),
        "also_fires": set(),
    },
    "contractions_negative.md": {
        "must_fire": set(),
        "must_not_fire": {"STE100.Contractions"},
        "also_fires": set(),
    },
    "ing_forms_positive.md": {
        "must_fire": {"STE100.IngForms"},
        "must_not_fire": set(),
        "also_fires": set(),
    },
    "ing_forms_negative.md": {
        "must_fire": set(),
        "must_not_fire": {"STE100.IngForms"},
        "also_fires": set(),
    },
    "passive_voice_positive.md": {
        "must_fire": {"STE100.PassiveVoice"},
        "must_not_fire": set(),
        "also_fires": set(),
    },
    "passive_voice_negative.md": {
        "must_fire": set(),
        "must_not_fire": {"STE100.PassiveVoice"},
        "also_fires": set(),
    },
    "sentence_length_positive.md": {
        "must_fire": {"STE100.SentenceLength"},
        "must_not_fire": set(),
        "also_fires": {"STE100.Shall"},
    },
    "sentence_length_negative.md": {
        "must_fire": set(),
        "must_not_fire": {"STE100.SentenceLength"},
        "also_fires": set(),
    },
    "shall_positive.md": {
        "must_fire": {"STE100.Shall"},
        "must_not_fire": set(),
        "also_fires": set(),
    },
    "shall_negative.md": {
        "must_fire": set(),
        "must_not_fire": {"STE100.Shall"},
        "also_fires": set(),
    },
}


@pytest.fixture
def tmp_work_dir(tmp_path):
    """Copy vale/ into a tmp directory so tests do not mutate the repo."""
    dest = tmp_path / "vale"
    dest.mkdir()
    for item in VALE_DIR.iterdir():
        if item.is_dir():
            import shutil

            shutil.copytree(item, dest / item.name)
        else:
            import shutil

            shutil.copy2(item, dest)
    return dest


def _run_vale(fixture_name, work_dir, min_alert_level="suggestion"):
    """Run vale against a fixture and return (stdout, stderr, code)."""
    cfg = work_dir / "fallback.ini"
    md = work_dir / "tests" / fixture_name
    result = subprocess.run(
        [
            VALE_BIN,
            f"--config={cfg}",
            f"--minAlertLevel={min_alert_level}",
            "--output=line",
            str(md),
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout, result.stderr, result.returncode


def _extract_rule_names(stdout):
    """Extract STE100.CheckName tokens from vale line output."""
    rules = set()
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        # Line format: <path>:<line>:<col>:<CheckName>:<message>
        parts = line.split(":")
        if len(parts) >= 4:
            check = parts[3]
            if check.startswith("STE100."):
                rules.add(check)
    return rules


@pytest.mark.parametrize("fixture_name", list(FIXTURES.keys()))
def test_fixture_rules(fixture_name, tmp_work_dir):
    """Each fixture produces exactly the expected set of rule names."""
    expected = FIXTURES[fixture_name]
    stdout, stderr, code = _run_vale(fixture_name, tmp_work_dir)

    # Exit code must be 0 (no error-severity alerts) — not 2 (schema failure).
    assert code != 2, f"{fixture_name}: vale exited 2 (schema/config error).\nstderr: {stderr}"

    fired = _extract_rule_names(stdout)
    allowed = expected["also_fires"]

    # Must-fire rules must fire.
    missing = expected["must_fire"] - fired
    assert missing == set(), f"{fixture_name}: expected {expected['must_fire']}, got {fired}. Missing: {missing}"

    # Must-not-fire rules must not fire (unless documented as also_fires).
    unexpected = (expected["must_not_fire"] - allowed) & fired
    assert unexpected == set(), f"{fixture_name}: unexpected rules fired: {unexpected}. Fired: {fired}"

    # Only the expected rules should fire.
    unexpected_only = fired - expected["must_fire"] - allowed
    assert unexpected_only == set(), f"{fixture_name}: unexpected rules fired: {unexpected_only}. Fired: {fired}"

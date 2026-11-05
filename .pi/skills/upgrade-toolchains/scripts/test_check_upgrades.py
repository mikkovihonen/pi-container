from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.dont_write_bytecode = True

# Add scripts directory to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_upgrades import (
    check_tool,
    format_table,
    get_current_tool_version,
    read_containerfile_args,
    run_verify,
    update_containerfile_arg,
    upgrade_tool,
)
from upstreams.base import ToolReleaseInfo, parse_version_tuple


class TestVersionParsing(unittest.TestCase):
    def test_parse_version_tuple_standard(self) -> None:
        self.assertEqual(parse_version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version_tuple("1.20.5"), (1, 20, 5))
        self.assertEqual(parse_version_tuple("0.4.5-alpha.1"), (0, 4, 5, 1))

    def test_parse_version_tuple_dates(self) -> None:
        self.assertEqual(parse_version_tuple("2026-03-01"), (2026, 3, 1))
        self.assertEqual(parse_version_tuple("2026.04.12"), (2026, 4, 12))

    def test_parse_version_tuple_sorting(self) -> None:
        versions = ["v1.2.0", "v1.10.0", "v1.9.5", "v2.0.0"]
        sorted_vers = sorted(versions, key=parse_version_tuple)
        self.assertEqual(sorted_vers, ["v1.2.0", "v1.9.5", "v1.10.0", "v2.0.0"])


class TestContainerfileParsing(unittest.TestCase):
    def test_read_containerfile_args(self) -> None:
        from tempfile import NamedTemporaryFile

        sample = """
# Header comment
ARG MOLD_VERSION=2.40.1
ARG MOLD_COMMIT=abc1234
ARG EMPTY_VAL=
ARG QUOTED_VAL="test-1.0"
"""
        with NamedTemporaryFile("w+", delete=False) as tf:
            tf.write(sample)
            tf.flush()
            p = Path(tf.name)

        try:
            args = read_containerfile_args(p)
            self.assertEqual(args["MOLD_VERSION"], "2.40.1")
            self.assertEqual(args["MOLD_COMMIT"], "abc1234")
            self.assertEqual(args["EMPTY_VAL"], "")
            self.assertEqual(args["QUOTED_VAL"], '"test-1.0"')
        finally:
            p.unlink(missing_ok=True)

    def test_get_current_tool_version(self) -> None:
        args = {
            "MOLD_VERSION": "2.40.1",
            "TREE_SITTER_VERSION": '"v0.25.3"',
            "AARDVARK_VERSION": "v1.14.0",
            "COLGREP_VERSION": "v0.1.0",
            "SQLITE_VEC_VERSION": "v0.1.6",
        }
        self.assertEqual(get_current_tool_version("mold", args), "2.40.1")
        self.assertEqual(get_current_tool_version("tree-sitter-cli", args), "v0.25.3")
        self.assertEqual(get_current_tool_version("aardvark-dns", args), "v1.14.0")
        self.assertEqual(get_current_tool_version("colgrep", args), "v0.1.0")
        self.assertEqual(get_current_tool_version("sqlite-vec", args), "v0.1.6")
        self.assertEqual(get_current_tool_version("nonexistent", args), "")

    def test_update_containerfile_arg_single_line(self) -> None:
        content = "FROM alpine:3.20\nARG MOLD_VERSION=2.40.1\nARG MOLD_COMMIT=oldhash\nRUN echo building...\n"
        updated = update_containerfile_arg(content, "MOLD_VERSION", "2.41.0")
        self.assertIn("ARG MOLD_VERSION=2.41.0\n", updated)
        self.assertIn("ARG MOLD_COMMIT=oldhash\n", updated)

        updated2 = update_containerfile_arg(updated, "MOLD_COMMIT", "newhash")
        self.assertIn("ARG MOLD_COMMIT=newhash\n", updated2)

    def test_update_containerfile_arg_multiline(self) -> None:
        content = 'ARG UV_WHEEL_HASHES="\\\n  --hash=sha256:111\\\n  --hash=sha256:222"\nRUN uv --version\n'
        new_val = '"\\\n  --hash=sha256:333\\\n  --hash=sha256:444"'
        updated = update_containerfile_arg(content, "UV_WHEEL_HASHES", new_val)
        self.assertIn('ARG UV_WHEEL_HASHES="\\\n  --hash=sha256:333\\\n  --hash=sha256:444"', updated)
        self.assertIn("RUN uv --version\n", updated)


class TestCheckTool(unittest.TestCase):
    def test_check_tool_up_to_date(self) -> None:
        fake_info = ToolReleaseInfo(
            tool="test-tool",
            version="1.0.0",
            args={"TEST_TOOL_VERSION": "1.0.0", "TEST_TOOL_COMMIT": "abc"},
            category="compilers",
        )
        with patch.dict("check_upgrades.RESOLVERS", {"test-tool": lambda: fake_info}):
            current_args = {"TEST_TOOL_VERSION": "1.0.0", "TEST_TOOL_COMMIT": "abc"}
            res = check_tool("test-tool", current_args)
            self.assertTrue(res["up_to_date"])
            self.assertEqual(res["current"], "1.0.0")
            self.assertEqual(res["latest"], "1.0.0")

    def test_check_tool_outdated(self) -> None:
        fake_info = ToolReleaseInfo(
            tool="test-tool",
            version="1.1.0",
            args={"TEST_TOOL_VERSION": "1.1.0", "TEST_TOOL_COMMIT": "def"},
            category="compilers",
        )
        with patch.dict("check_upgrades.RESOLVERS", {"test-tool": lambda: fake_info}):
            current_args = {"TEST_TOOL_VERSION": "1.0.0", "TEST_TOOL_COMMIT": "abc"}
            res = check_tool("test-tool", current_args)
            self.assertFalse(res["up_to_date"])
            self.assertEqual(res["current"], "1.0.0")
            self.assertEqual(res["latest"], "1.1.0")

    def test_check_tool_same_version_different_commit(self) -> None:
        fake_info = ToolReleaseInfo(
            tool="test-tool",
            version="1.0.0",
            args={"TEST_TOOL_VERSION": "1.0.0", "TEST_TOOL_COMMIT": "newhash"},
            category="compilers",
        )
        with patch.dict("check_upgrades.RESOLVERS", {"test-tool": lambda: fake_info}):
            current_args = {"TEST_TOOL_VERSION": "1.0.0", "TEST_TOOL_COMMIT": "oldhash"}
            res = check_tool("test-tool", current_args)
            self.assertFalse(res["up_to_date"])

    def test_check_tool_resolver_error(self) -> None:
        def failing_resolver() -> ToolReleaseInfo:
            raise RuntimeError("Network timeout")

        with patch.dict("check_upgrades.RESOLVERS", {"test-tool": failing_resolver}):
            current_args = {"TEST_TOOL_VERSION": "1.0.0"}
            res = check_tool("test-tool", current_args)
            self.assertIn("Network timeout", res["error"])
            self.assertEqual(res["latest"], "error")

    def test_check_tool_unregistered(self) -> None:
        res = check_tool("unknown-tool", {})
        self.assertEqual(res["error"], "No resolver registered")


class TestFormatTable(unittest.TestCase):
    def test_format_table(self) -> None:
        results = [
            {
                "tool": "mold",
                "category": "compilers",
                "current": "v2.40.1",
                "latest": "v2.40.2",
                "up_to_date": False,
            },
            {
                "tool": "git",
                "category": "vcs",
                "current": "v2.49.0",
                "latest": "v2.49.0",
                "up_to_date": True,
            },
            {
                "tool": "broken",
                "category": "error",
                "current": "1.0",
                "latest": "error",
                "up_to_date": True,
                "error": "Failed",
            },
        ]
        table = format_table(results)
        self.assertIn("mold", table)
        self.assertIn("compilers", table)
        self.assertIn("⚑ Upgrade available", table)
        self.assertIn("✓ Up to date", table)
        self.assertIn("✗ Error: Failed", table)


class TestUpgradeTool(unittest.TestCase):
    def test_upgrade_tool_applies_args(self, tmp_path: Path | None = None) -> None:
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("ARG MOLD_VERSION=2.40.1\nARG MOLD_COMMIT=old\n")
            tf.flush()
            tmp_path = Path(tf.name)

        try:
            info = ToolReleaseInfo(
                tool="mold",
                version="2.40.2",
                args={"MOLD_VERSION": "2.40.2", "MOLD_COMMIT": "new"},
                category="compilers",
            )
            upgrade_tool("mold", info, tmp_path)
            content = tmp_path.read_text()
            self.assertIn("ARG MOLD_VERSION=2.40.2\n", content)
            self.assertIn("ARG MOLD_COMMIT=new\n", content)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestVerify(unittest.TestCase):
    @patch("subprocess.run")
    def test_run_verify_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(run_verify())
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_run_verify_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(run_verify())


if __name__ == "__main__":
    unittest.main()

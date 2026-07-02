"""
Unit tests for src/build.py

Run with:
    python -m pytest src/tests/test_build.py -v
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import build module functions — we patch the heavy deps (subprocess, validate_environment)
from build import (
    PROXY_IMAGE_TAG,
    REPO_ROOT,
    build_agent,
    build_proxy,
    main,
)

# ---------------------------------------------------------------------------
# build_proxy / build_agent
# ---------------------------------------------------------------------------


class TestBuildProxy:
    def test_calls_runtime_build(self):
        """build_proxy should invoke the container runtime with correct args."""
        with patch("build.subprocess.run") as mock_run:
            build_proxy("docker")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "docker"
            assert cmd[1] == "build"
            assert "--tag" in cmd
            assert "pi-coding-agent-proxy:local" in cmd
            # Should include the Containerfile path
            assert any("Containerfile" in str(c) for c in cmd)
            # Should include the repo root as build context
            assert str(REPO_ROOT) in cmd

    def test_uses_passed_tag(self):
        with patch("build.subprocess.run") as mock_run:
            build_proxy("podman")
            cmd = mock_run.call_args[0][0]
            tag_idx = cmd.index("--tag")
            assert cmd[tag_idx + 1] == PROXY_IMAGE_TAG

    def test_build_agent_calls_runtime(self):
        """build_agent should invoke the container runtime with correct args."""
        with patch("build.subprocess.run") as mock_run:
            build_agent("docker")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "docker"
            assert cmd[1] == "build"
            assert "pi-coding-agent:local" in cmd
            assert any("Containerfile" in str(c) for c in cmd)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_builds_both_images(self):
        """main should build proxy first, then agent."""
        with (
            patch("build.validate_environment", return_value="docker"),
            patch("build.build_proxy") as mock_proxy,
            patch("build.build_agent") as mock_agent,
            patch("sys.exit"),
        ):
            main()
            mock_proxy.assert_called_once_with("docker")
            mock_agent.assert_called_once_with("docker")

    def test_main_exits_on_environment_error(self):
        """main should call sys.exit(1) when validate_environment raises EnvironmentError."""
        from util import EnvironmentError

        # Use a real exit tracker to verify sys.exit(1) is called
        exit_calls = []

        def track_exit(code=0):
            exit_calls.append(code)
            raise SystemExit(code)

        with (
            patch("build.validate_environment", side_effect=EnvironmentError("test error")),
            patch("sys.exit", side_effect=track_exit),
            patch("builtins.print"),
            pytest.raises(SystemExit),
        ):
            main()
        assert exit_calls == [1]

    def test_main_exits_on_build_failure(self):
        """main should exit 1 when subprocess raises CalledProcessError."""
        import subprocess

        with (
            patch("build.validate_environment", return_value="docker"),
            patch("build.build_proxy", side_effect=subprocess.CalledProcessError(1, "cmd")),
            patch("sys.exit") as mock_exit,
            patch("builtins.print"),
        ):
            main()
            mock_exit.assert_called_once_with(1)

    def test_main_exits_on_file_not_found(self):
        """main should exit 1 when runtime command is not found."""
        with (
            patch("build.validate_environment", return_value="nonexistent_runtime"),
            patch("build.build_proxy", side_effect=FileNotFoundError),
            patch("sys.exit") as mock_exit,
            patch("builtins.print"),
        ):
            main()
            mock_exit.assert_called_once_with(1)

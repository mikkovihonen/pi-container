"""
Unit tests for src/build.py

Run with:
    python -m pytest src/tests/test_build.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import build module functions — we patch the heavy deps (subprocess, validate_environment)
from build import (
    PROXY_IMAGE_TAG,
    REPO_ROOT,
    build_agent,
    build_project_image,
    build_proxy,
    main,
)

# ---------------------------------------------------------------------------
# build_proxy / build_agent
# ---------------------------------------------------------------------------


class TestBuildProxy:
    def _mock_popen(self):
        """Return a mock Popen that completes successfully with no output."""
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.wait.return_value = 0
        return mock_process

    def test_calls_runtime_build(self):
        """build_proxy should invoke the container runtime with correct args."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_proxy("podman")
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "podman"
            assert cmd[1] == "build"
            assert "--tag" in cmd
            assert "pi-coding-agent-proxy:local" in cmd
            # Should include the Containerfile path
            assert any("Containerfile" in str(c) for c in cmd)
            # Should include the repo root as build context
            assert str(REPO_ROOT) in cmd

    def test_uses_passed_tag(self):
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_proxy("podman")
            cmd = mock_popen.call_args[0][0]
            tag_idx = cmd.index("--tag")
            assert cmd[tag_idx + 1] == PROXY_IMAGE_TAG

    def test_includes_build_time_label(self):
        """build_proxy should set pi-container.build.time label."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_proxy("podman")
            cmd = mock_popen.call_args[0][0]
            assert "--label" in cmd
            labels = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--label"]
            assert any(lbl.startswith("pi-container.build.time=") for lbl in labels)
            assert any(lbl == "pi-container.type=shared" for lbl in labels)

    def test_includes_shared_type_label(self):
        """build_proxy should set pi-container.type=shared label."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_proxy("podman")
            cmd = mock_popen.call_args[0][0]
            labels = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--label"]
            assert "pi-container.type=shared" in labels

    def test_build_agent_calls_runtime(self):
        """build_agent should invoke the container runtime with correct args."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_agent("podman")
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "podman"
            assert cmd[1] == "build"
            assert "pi-coding-agent:local" in cmd
            assert any("Containerfile" in str(c) for c in cmd)

    def test_build_agent_labels_image_as_shared(self):
        """The shared base must be labelled `shared`, never `project`.

        It is built from the same Containerfile as the per-project images. When that
        file hardcoded `type=project`, the base was stamped as a project image too and
        run.py's orphan cleanup could not tell it apart from a real project's image.
        """
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_agent("podman")
            cmd = mock_popen.call_args[0][0]
            assert "pi-container.type=shared" in cmd
            assert "pi-container.type=project" not in cmd

    def test_containerfile_does_not_hardcode_type_label(self):
        """`pi-container.type` must come from the CLI, not the Containerfile.

        The agent Containerfile builds both the shared base and the per-project
        images, so it cannot know which type it is producing. Setting the label per
        build also keeps it off build intermediates, so a half-finished build is never
        picked up as an orphan.
        """
        from build import REPO_ROOT

        containerfile = (REPO_ROOT / "pi-coding-agent" / "Containerfile").read_text()
        assert "LABEL pi-container.type" not in containerfile

    def test_build_agent_does_not_forward_python_args(self, monkeypatch):
        """PYTHON_OPTIMIZE belongs to the builder image, which is where Python is compiled.

        Passing it here would be a lie: the agent image only copies the result, and an
        unused --build-arg would still change the agent image's cache key.
        """
        import build as build_mod

        monkeypatch.setenv("PYTHON_OPTIMIZE", "0")
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_mod.build_agent("podman")
            cmd = mock_popen.call_args[0][0]
            assert not any("PYTHON_OPTIMIZE" in str(c) for c in cmd)


class TestBuildBuilder:
    """Tests for build_builder() — the toolchain image."""

    @staticmethod
    def _mock_popen():
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.wait.return_value = 0
        return mock_process

    def test_builds_the_builder_containerfile(self):
        import build as build_mod

        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_mod.build_builder("podman")
            cmd = mock_popen.call_args[0][0]
            assert cmd[:2] == ["podman", "build"]
            assert build_mod.BUILDER_IMAGE_TAG in cmd
            assert any("pi-coding-agent-builder" in str(c) and "Containerfile" in str(c) for c in cmd)

    def test_carries_a_build_time_label(self):
        """run.py dates project images against this label; without it, it exits."""
        import build as build_mod

        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_mod.build_builder("podman")
            cmd = mock_popen.call_args[0][0]
            labels = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--label"]
            assert any(lbl.startswith("pi-container.build.time=") for lbl in labels)
            assert "pi-container.type=shared" in labels

    def test_forwards_python_optimize(self, monkeypatch):
        """This is the image that compiles CPython, so PYTHON_OPTIMIZE applies here."""
        import build as build_mod

        monkeypatch.setenv("PYTHON_OPTIMIZE", "0")
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_mod.build_builder("podman")
            cmd = mock_popen.call_args[0][0]
            assert "PYTHON_OPTIMIZE=0" in cmd

    def test_forwards_node_source(self, monkeypatch):
        import build as build_mod

        monkeypatch.setenv("NODE_SOURCE", "build")
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_mod.build_builder("podman")
            cmd = mock_popen.call_args[0][0]
            assert "NODE_SOURCE=build" in cmd


class TestNodeBuildArgs:
    """Tests for _node_build_args() — prebuilt-vs-compiled Node."""

    def test_omitted_when_env_unset(self, monkeypatch):
        """Unset means the Containerfile's own default (prebuilt) decides."""
        import build

        monkeypatch.delenv("NODE_SOURCE", raising=False)
        assert build._node_build_args() == []

    def test_both_modes_forwarded(self, monkeypatch):
        import build

        for value in ("prebuilt", "build"):
            monkeypatch.setenv("NODE_SOURCE", value)
            assert build._node_build_args() == ["--build-arg", f"NODE_SOURCE={value}"]

    def test_case_and_whitespace_tolerated(self, monkeypatch):
        import build

        monkeypatch.setenv("NODE_SOURCE", "  BUILD  ")
        assert build._node_build_args() == ["--build-arg", "NODE_SOURCE=build"]

    def test_unknown_value_rejected_before_any_build_starts(self, monkeypatch):
        """A typo must fail here, not after the proxy image has been rebuilt.

        The build script would reject it too, but only once the toolchain image is
        already being built — by which point `build.sh` has regenerated the proxy's
        mitmproxy CA and invalidated every project image.
        """
        import build
        from util import EnvironmentError as UtilEnvironmentError

        monkeypatch.setenv("NODE_SOURCE", "prebuild")
        with pytest.raises(UtilEnvironmentError, match="NODE_SOURCE must be one of"):
            build._node_build_args()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_builds_all_three_images(self):
        """main should build the toolchain, then the proxy, then the agent.

        Order is load-bearing in both links: the proxy image COPYs its CPython and uv
        from the toolchain image, and the agent image COPYs from both. Any of them
        missing or stale makes a build fail or bake in stale content.
        """
        calls = []
        with (
            patch("build.validate_environment", return_value="podman"),
            patch("build.check_build_memory", return_value=True),
            patch("build.build_proxy", side_effect=lambda r: calls.append("proxy")),
            patch("build.build_builder", side_effect=lambda r: calls.append("builder")),
            patch("build.build_agent", side_effect=lambda r: calls.append("agent")),
            patch("sys.exit"),
        ):
            main()
        assert calls == ["builder", "proxy", "agent"]

    def test_main_rejects_bad_node_source_before_building_anything(self, monkeypatch):
        """An invalid NODE_SOURCE must cost nothing at all.

        It is consumed by the first image built, so the check has to happen before that
        build starts rather than inside it.
        """
        monkeypatch.setenv("NODE_SOURCE", "prebuild")
        with (
            patch("build.validate_environment", return_value="podman"),
            patch("build.check_build_memory", return_value=True),
            patch("build.build_proxy") as mock_proxy,
            patch("build.build_builder") as mock_builder,
            patch("build.build_agent") as mock_agent,
            patch("sys.exit", side_effect=SystemExit(1)),
            pytest.raises(SystemExit),
        ):
            main()
        mock_proxy.assert_not_called()
        mock_builder.assert_not_called()
        mock_agent.assert_not_called()

    def test_main_stops_before_building_when_memory_is_short(self):
        """The preflight has to run before the first build, not between them."""
        with (
            patch("build.validate_environment", return_value="podman"),
            patch("build.check_build_memory", return_value=False),
            patch("build.build_proxy") as mock_proxy,
            patch("build.build_builder") as mock_builder,
            patch("build.build_agent") as mock_agent,
            patch("sys.exit", side_effect=SystemExit(1)),
            pytest.raises(SystemExit),
        ):
            main()
        mock_proxy.assert_not_called()
        mock_builder.assert_not_called()
        mock_agent.assert_not_called()

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

        # build_builder is the one made to fail because it is the first image built;
        # the other two are patched so a failure here can never reach a real podman.
        with (
            patch("build.validate_environment", return_value="podman"),
            patch("build.check_build_memory", return_value=True),
            patch("build.build_builder", side_effect=subprocess.CalledProcessError(1, "cmd")),
            patch("build.build_proxy"),
            patch("build.build_agent"),
            patch("sys.exit") as mock_exit,
            patch("builtins.print"),
        ):
            main()
            mock_exit.assert_called_once_with(1)

    def test_main_exits_on_file_not_found(self):
        """main should exit 1 when runtime command is not found."""
        with (
            patch("build.validate_environment", return_value="nonexistent_runtime"),
            patch("build.check_build_memory", return_value=True),
            patch("build.build_builder", side_effect=FileNotFoundError),
            patch("build.build_proxy"),
            patch("build.build_agent"),
            patch("sys.exit") as mock_exit,
            patch("builtins.print"),
        ):
            main()
            mock_exit.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# build_project_image
# ---------------------------------------------------------------------------


class TestBuildProjectImage:
    def _mock_popen(self):
        """Return a mock Popen that completes successfully with no output."""
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.wait.return_value = 0
        return mock_process

    def test_build_project_image_calls_runtime(self):
        """build_project_image should invoke the container runtime with correct args."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_project_image(
                "podman",
                "/path/to/root/commands.sh",
                "/path/to/pi/commands.sh",
                "pi-coding-agent-test.local",
                "abc123",
            )
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "podman"
            assert cmd[1] == "build"
            assert "--tag" in cmd
            assert "pi-coding-agent-test.local" in cmd
            assert "LABEL_HASH=abc123" in cmd
            # Should include the Containerfile path
            assert any("Containerfile" in str(c) for c in cmd)

    def test_build_project_image_includes_project_hash(self):
        """When project_hash is provided, it should be passed as a build arg."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_project_image(
                "podman",
                "/path/to/root/commands.sh",
                "/path/to/pi/commands.sh",
                "pi-container-project-a1b2c-d4e5f.local",
                "abc123",
                project_hash="a1b2c",
                build_timestamp="2025-01-01T00:00:00Z",
            )
            cmd = mock_popen.call_args[0][0]
            assert "--build-arg" in cmd
            cmd.index("--build-arg")
            # PROJECT_HASH should appear after LABEL_HASH
            label_idx = cmd.index("LABEL_HASH=abc123")
            project_idx = cmd.index("PROJECT_HASH=a1b2c")
            assert project_idx > label_idx

    def test_build_project_image_includes_build_timestamp(self):
        """When build_timestamp is provided, it should be passed as a build arg."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_project_image(
                "podman",
                "/path/to/root/commands.sh",
                "/path/to/pi/commands.sh",
                "pi-container-project-a1b2c-d4e5f.local",
                "abc123",
                project_hash="a1b2c",
                build_timestamp="2025-01-01T00:00:00Z",
            )
            cmd = mock_popen.call_args[0][0]
            assert "BUILD_TIMESTAMP=2025-01-01T00:00:00Z" in cmd

    def test_build_project_image_includes_type_label(self):
        """build_project_image should always set pi-container.type=project label."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_project_image(
                "podman",
                "/path/to/root/commands.sh",
                "/path/to/pi/commands.sh",
                "test.local",
                "abc123",
            )
            cmd = mock_popen.call_args[0][0]
            assert "--label" in cmd
            label_idx = cmd.index("--label")
            assert cmd[label_idx + 1] == "pi-container.type=project"

    def test_build_project_image_without_optional_args(self):
        """build_project_image should work without project_hash or build_timestamp."""
        with patch("build.subprocess.Popen", return_value=self._mock_popen()) as mock_popen:
            build_project_image(
                "podman",
                "/path/to/root/commands.sh",
                "/path/to/pi/commands.sh",
                "test.local",
                "abc123",
            )
            cmd = mock_popen.call_args[0][0]
            # No PROJECT_HASH or BUILD_TIMESTAMP build args should be present
            for i, part in enumerate(cmd):
                if part == "--build-arg":
                    assert cmd[i + 1] != "PROJECT_HASH="
                    assert cmd[i + 1] != "BUILD_TIMESTAMP="


# ---------------------------------------------------------------------------
# Build-memory preflight
# ---------------------------------------------------------------------------

_MEMINFO = """MemTotal:        1984568 kB
MemFree:           44404 kB
MemAvailable:     302080 kB
Buffers:            1234 kB
"""


class TestParseMemAvailable:
    def test_reads_mem_available_not_mem_free(self):
        """MemFree and MemAvailable differ by an order of magnitude on a warm
        builder; the reclaimable page cache is usable, so MemAvailable is right."""
        from build import _parse_mem_available_mib

        assert _parse_mem_available_mib(_MEMINFO) == 302080 // 1024

    def test_returns_none_when_absent(self):
        from build import _parse_mem_available_mib

        assert _parse_mem_available_mib("MemTotal: 123 kB\n") is None


class TestReadAvailableMemoryMib:
    def test_asks_the_vm_when_there_is_no_local_procfs(self, monkeypatch):
        """On macOS the build happens inside podman's VM, so the host's own free
        memory is irrelevant — the VM must be the one asked."""
        import build

        monkeypatch.setattr(build.Path, "exists", lambda self: False)
        seen = {}

        def mock_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return MagicMock(returncode=0, stdout=_MEMINFO, stderr="")

        monkeypatch.setattr(build.subprocess, "run", mock_run)
        assert build.read_available_memory_mib("podman") == 295
        assert seen["cmd"][:3] == ["podman", "machine", "ssh"]

    def test_returns_none_when_vm_unreachable(self, monkeypatch):
        import build

        monkeypatch.setattr(build.Path, "exists", lambda self: False)
        monkeypatch.setattr(
            build.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=125, stdout="", stderr="no machine")
        )
        assert build.read_available_memory_mib("podman") is None


class TestCheckBuildMemory:
    def test_passes_when_memory_is_sufficient(self, monkeypatch):
        import build

        monkeypatch.delenv("PYTHON_OPTIMIZE", raising=False)
        monkeypatch.delenv("PI_MEMORY_PREFLIGHT", raising=False)
        monkeypatch.setattr(build, "read_available_memory_mib", lambda rt: 4096)
        assert build.check_build_memory("podman") is True

    def test_blocks_the_build_when_memory_is_short(self, monkeypatch):
        """The point of the check: say so BEFORE the build starts."""
        import build

        monkeypatch.delenv("PYTHON_OPTIMIZE", raising=False)
        monkeypatch.delenv("PI_MEMORY_PREFLIGHT", raising=False)
        monkeypatch.setattr(build, "read_available_memory_mib", lambda rt: 276)
        assert build.check_build_memory("podman") is False

    def test_warns_but_proceeds_when_not_fatal(self, monkeypatch):
        """run.py's project-image build normally reuses the cached Python layer,
        so a short-memory reading there is advisory, not a stop."""
        import build

        monkeypatch.delenv("PYTHON_OPTIMIZE", raising=False)
        monkeypatch.delenv("PI_MEMORY_PREFLIGHT", raising=False)
        monkeypatch.setattr(build, "read_available_memory_mib", lambda rt: 276)
        assert build.check_build_memory("podman", fatal=False) is True

    def test_non_pgo_lowers_the_requirement(self, monkeypatch):
        import build

        monkeypatch.delenv("PI_MEMORY_PREFLIGHT", raising=False)
        monkeypatch.setattr(build, "read_available_memory_mib", lambda rt: 500)
        monkeypatch.setenv("PYTHON_OPTIMIZE", "1")
        assert build.check_build_memory("podman") is False
        monkeypatch.setenv("PYTHON_OPTIMIZE", "0")
        assert build.check_build_memory("podman") is True

    def test_escape_hatch_skips_the_check(self, monkeypatch):
        import build

        monkeypatch.setenv("PI_MEMORY_PREFLIGHT", "0")

        def unexpected(rt):
            raise AssertionError("memory must not be read when the preflight is disabled")

        monkeypatch.setattr(build, "read_available_memory_mib", unexpected)
        assert build.check_build_memory("podman") is True

    def test_unknown_memory_does_not_block(self, monkeypatch):
        """A reading we cannot take must never stop a build that might be fine."""
        import build

        monkeypatch.delenv("PI_MEMORY_PREFLIGHT", raising=False)
        monkeypatch.setattr(build, "read_available_memory_mib", lambda rt: None)
        assert build.check_build_memory("podman") is True


class TestPythonBuildArgs:
    def test_omitted_when_env_unset(self, monkeypatch):
        """Unset means the Containerfile's own default (PGO on) decides."""
        import build

        monkeypatch.delenv("PYTHON_OPTIMIZE", raising=False)
        assert build._python_build_args() == []

    def test_forwarded_when_set(self, monkeypatch):
        import build

        monkeypatch.setenv("PYTHON_OPTIMIZE", "0")
        assert build._python_build_args() == ["--build-arg", "PYTHON_OPTIMIZE=0"]
        monkeypatch.setenv("PYTHON_OPTIMIZE", "1")
        assert build._python_build_args() == ["--build-arg", "PYTHON_OPTIMIZE=1"]


# ---------------------------------------------------------------------------
# Toolchain pins: Containerfile ARGs vs. what the build scripts require
# ---------------------------------------------------------------------------
#
# Every version, commit and sha256 lives in pi-coding-agent-builder/Containerfile as
# an ARG, and the build scripts define no fallbacks — they assert presence with
# require_env and fail. That is deliberate (a stale pin baked into a script would
# defeat the point of hoisting them), but it means a renamed or forgotten ARG is a
# *build-time* failure, minutes into a compile, in a stage that may not even be the
# one being changed. These tests turn that into a millisecond.

BUILDER_DIR = REPO_ROOT / "pi-coding-agent-builder"


def _join_continuations(text: str) -> list[str]:
    """Collapse backslash-continued lines, in both Containerfiles and shell."""
    return text.replace("\\\n", " ").splitlines()


def _containerfile_stages() -> dict[str, dict]:
    """Map each build stage to the ARGs it declares and the scripts it runs.

    A stage's usable ARGs are only the ones it declares itself. A pre-FROM ARG is
    *not* inherited automatically — a stage must re-declare it (verified against
    podman), which is exactly the mistake worth catching here.
    """
    stages: dict[str, dict] = {}
    current: dict | None = None

    for line in _join_continuations((BUILDER_DIR / "Containerfile").read_text()):
        stripped = line.strip()
        if stripped.startswith("FROM ") and " AS " in stripped:
            name = stripped.split(" AS ", 1)[1].strip()
            current = {"args": set(), "scripts": set()}
            stages[name] = current
        elif stripped.startswith("ARG ") and current is not None:
            current["args"].add(stripped[4:].strip().split("=", 1)[0].strip())
        elif stripped.startswith("RUN ") and current is not None:
            for token in stripped.split():
                if token.endswith(".sh"):
                    current["scripts"].add(Path(token).name)

    return stages


def _required_env(script: Path) -> set[str]:
    """Names passed to require_env anywhere in a shell script."""
    required: set[str] = set()
    for line in _join_continuations(script.read_text()):
        stripped = line.strip()
        if not stripped.startswith("require_env "):
            continue
        required.update(stripped.split()[1:])
    return required


class TestToolchainPinsAreDeclared:
    def test_every_required_arg_is_declared_on_its_stage(self):
        """Each script's require_env names must be ARGs on the stage that runs it."""
        stages = _containerfile_stages()
        # install_rust lives in common.sh, so its RUST_* requirements belong to
        # whichever stages actually call it rather than to a stage of their own.
        rust_required = _required_env(BUILDER_DIR / "common.sh")
        assert rust_required, "expected install_rust to assert its RUST_* pins"

        checked = 0
        for stage_name, stage in stages.items():
            for script_name in stage["scripts"]:
                script = BUILDER_DIR / script_name
                assert script.is_file(), f"{stage_name} runs missing script {script_name}"

                required = _required_env(script)
                if "install_rust" in script.read_text():
                    required |= rust_required

                missing = required - stage["args"]
                assert not missing, (
                    f"stage '{stage_name}' runs {script_name}, which requires "
                    f"{sorted(missing)} — declare them as ARG on that stage in "
                    f"pi-coding-agent-builder/Containerfile "
                    f"(a pre-FROM ARG must be re-declared to be inherited)"
                )
                checked += 1

        assert checked >= 4, f"expected to check all four component scripts, saw {checked}"

    def test_scripts_hardcode_no_pins(self):
        """No sha256 or commit-looking literal may sit in a build script."""
        import re

        # 40 hex = git commit, 64 hex = sha256. Both belong in the Containerfile.
        literal = re.compile(r"\b[0-9a-f]{40,64}\b")
        for script in sorted(BUILDER_DIR.glob("*.sh")):
            hits = literal.findall(script.read_text())
            assert not hits, (
                f"{script.name} hardcodes {hits} — hashes and commits belong in "
                f"pi-coding-agent-builder/Containerfile as ARGs"
            )

    def test_every_declared_pin_is_actually_used(self):
        """An ARG no script reads is a pin that silently does nothing."""
        stages = _containerfile_stages()
        rust_required = _required_env(BUILDER_DIR / "common.sh")
        # Mode/tuning knobs are read directly with a shell default rather than through
        # require_env, so they are exempt from the "must be required" rule below.
        knobs = {"PYTHON_OPTIMIZE", "MAKE_JOBS", "NODE_SOURCE"}

        for stage_name, stage in stages.items():
            if not stage["scripts"]:
                continue
            required: set[str] = set()
            for script_name in stage["scripts"]:
                script = BUILDER_DIR / script_name
                required |= _required_env(script)
                if "install_rust" in script.read_text():
                    required |= rust_required

            unused = stage["args"] - required - knobs
            assert not unused, (
                f"stage '{stage_name}' declares ARG {sorted(unused)} that no script "
                f"it runs requires — remove it, or pass it to require_env"
            )

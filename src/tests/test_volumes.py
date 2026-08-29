import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import volumes

sys.dont_write_bytecode = True


class TestEnsureNestedVolume:
    def test_existing_volume_is_reused(self, monkeypatch):
        """A present store must not be recreated — that is what keeps the cache."""
        monkeypatch.setattr(volumes, "volume_exists", lambda rt, name: True)

        def unexpected(*args, **kwargs):
            raise AssertionError("volume create must not run for an existing volume")

        monkeypatch.setattr(volumes.subprocess, "run", unexpected)
        assert volumes.ensure_nested_volume("podman", "pi-nested-abc", "pi-proxy-abc", "/tmp/proj") is True

    def test_creates_with_project_labels(self, monkeypatch):
        monkeypatch.setattr(volumes, "volume_exists", lambda rt, name: False)
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", mock_run)
        assert volumes.ensure_nested_volume("podman", "pi-nested-abc", "pi-proxy-abc", "/tmp/proj") is True

        cmd = calls[0]
        assert cmd[:3] == ["podman", "volume", "create"]
        assert cmd[-1] == "pi-nested-abc"
        # Labelled like project images, so the same orphan rule reclaims it.
        assert "pi-container.type=nested-storage" in cmd
        assert "pi-container.project.hash=pi-proxy-abc" in cmd
        assert "pi-container.project.path=/tmp/proj" in cmd

    def test_returns_false_when_create_fails(self, monkeypatch):
        monkeypatch.setattr(volumes, "volume_exists", lambda rt, name: False)
        monkeypatch.setattr(
            volumes.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=125, stdout="", stderr="out of space"),
        )
        assert volumes.ensure_nested_volume("podman", "pi-nested-abc", "pi-proxy-abc", "/tmp/proj") is False


class TestUnusedVolumes:
    """Tests for unused_volumes() — the guard against removing a volume in use."""

    def test_returns_dangling_volume_names(self, monkeypatch):
        monkeypatch.setattr(
            volumes.subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="vol-a\nvol-b\n", stderr=""),
        )
        assert volumes.unused_volumes("podman") == {"vol-a", "vol-b"}

    def test_queries_the_dangling_filter(self, monkeypatch):
        """The whole check rests on `dangling=true` meaning "no container references it"."""
        seen: list[list[str]] = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", _run)
        volumes.unused_volumes("podman")
        assert "dangling=true" in seen[0]

    def test_returns_none_on_runtime_failure(self, monkeypatch):
        """None means "unknown", which the caller distinguishes from "none are unused"."""
        import subprocess

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(volumes.subprocess, "run", boom)
        assert volumes.unused_volumes("podman") is None


class TestCleanupOrphanedNestedVolumes:
    def _mock_ls(self, monkeypatch, names: str, dangling: str | None = None):
        """Answer `volume ls` by label and by `dangling=true` separately."""

        def _run(cmd, **kw):
            stdout = (names if dangling is None else dangling) if "dangling=true" in cmd else names
            return MagicMock(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", _run)

    def test_removes_volume_with_missing_path(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(
            volumes,
            "get_volume_label",
            lambda rt, name, label: "/nonexistent/project/path",
        )
        removed: list[str] = []
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name: removed.append(name) or True)

        assert volumes.cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]
        assert removed == ["pi-nested-abc1234567"]

    def test_keeps_volume_with_existing_path(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: str(Path(__file__).parent))

        def unexpected(rt, name):
            raise AssertionError("a live project's store must never be removed")

        monkeypatch.setattr(volumes, "remove_volume", unexpected)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == []

    def test_removes_volume_without_path_label(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: None)
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name: True)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]

    def test_removes_volume_with_blank_path_label(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "")
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name: True)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]

    def test_keeps_orphaned_volume_still_in_use(self, monkeypatch):
        # Listed, but absent from the dangling set — something references it.
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n", dangling="")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "/nonexistent/path")

        def unexpected(rt, name):
            raise AssertionError("removal must not be attempted while a container holds the volume")

        monkeypatch.setattr(volumes, "remove_volume", unexpected)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == []

    def test_attempts_removal_when_usage_is_unknown(self, monkeypatch):
        import subprocess as sp

        def _run(cmd, **kw):
            if "dangling=true" in cmd:
                raise sp.TimeoutExpired("cmd", 10)
            return MagicMock(returncode=0, stdout="pi-nested-abc1234567\n", stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", _run)
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "/nonexistent/path")
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name: True)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]

    def test_removal_failure_is_not_fatal(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "/nonexistent/path")
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name: False)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == []

    def test_returns_empty_when_list_fails(self, monkeypatch):
        import subprocess as sp

        def boom(cmd, **kw):
            raise sp.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(volumes.subprocess, "run", boom)
        assert volumes.cleanup_orphaned_nested_volumes("podman") == []


class TestProjectVolumeName:
    def test_deterministic_and_unique(self):
        name1 = volumes.project_volume_name("abc1234567", "/workspace/node_modules")
        name2 = volumes.project_volume_name("abc1234567", "/workspace/node_modules")
        name3 = volumes.project_volume_name("abc1234567", "/workspace/.venv")
        name4 = volumes.project_volume_name("otherproj", "/workspace/node_modules")

        assert name1 == name2
        assert name1.startswith("pi-vol-abc1234567-")
        assert name1 != name3
        assert name1 != name4


class TestEnsureProjectVolume:
    def test_existing_volume_returns_true(self, monkeypatch):
        monkeypatch.setattr(volumes, "volume_exists", lambda rt, name: True)
        assert (
            volumes.ensure_project_volume(
                "podman",
                "pi-vol-test",
                "/workspace/node_modules",
                "pi-proxy-test",
                "/host/path",
            )
            is True
        )

    def test_creates_volume_with_labels(self, monkeypatch):
        monkeypatch.setattr(volumes, "volume_exists", lambda rt, name: False)
        recorded = []

        def _run(cmd, **kw):
            recorded.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", _run)
        success = volumes.ensure_project_volume(
            "podman",
            "pi-vol-test",
            "/workspace/node_modules",
            "pi-proxy-test",
            "/host/path",
        )
        assert success is True
        assert len(recorded) == 1
        cmd = recorded[0]
        assert "volume" in cmd and "create" in cmd
        assert "pi-container.type=project-volume" in cmd
        assert "pi-container.project.hash=pi-proxy-test" in cmd
        assert "pi-container.project.path=/host/path" in cmd
        assert "pi-container.volume.dest=/workspace/node_modules" in cmd
        assert "pi-vol-test" in cmd

    def test_handles_creation_failure(self, monkeypatch):
        monkeypatch.setattr(volumes, "volume_exists", lambda rt, name: False)
        monkeypatch.setattr(
            volumes.subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=1, stdout="", stderr="failed"),
        )
        assert (
            volumes.ensure_project_volume(
                "podman",
                "pi-vol-test",
                "/workspace/node_modules",
                "pi-proxy-test",
                "/host/path",
            )
            is False
        )


class TestCleanupStaleProjectVolumes:
    def _mock_ls(self, monkeypatch, names: str, dangling: str | None = None):
        def _run(cmd, **kw):
            stdout = (names if dangling is None else dangling) if "dangling=true" in cmd else names
            return MagicMock(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", _run)

    def test_removes_stale_volume(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-11111111\npi-vol-proj-22222222\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "/workspace/old")
        removed = []
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name, **kw: removed.append(name) or True)

        active = {"pi-vol-proj-11111111"}
        result = volumes.cleanup_stale_project_volumes("podman", "pi-proxy-proj", active)
        assert result == ["pi-vol-proj-22222222"]
        assert removed == ["pi-vol-proj-22222222"]

    def test_keeps_stale_volume_if_in_use(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-22222222\n", dangling="")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "/workspace/old")
        monkeypatch.setattr(
            volumes,
            "remove_volume",
            lambda *a, **kw: pytest.fail("should not remove in-use volume"),
        )

        result = volumes.cleanup_stale_project_volumes("podman", "pi-proxy-proj", set())
        assert result == []


class TestCleanupOrphanedProjectVolumes:
    def _mock_ls(self, monkeypatch, names: str, dangling: str | None = None):
        def _run(cmd, **kw):
            stdout = (names if dangling is None else dangling) if "dangling=true" in cmd else names
            return MagicMock(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(volumes.subprocess, "run", _run)

    def test_removes_orphaned_volume(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-11111111\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: "/nonexistent/path")
        removed = []
        monkeypatch.setattr(volumes, "remove_volume", lambda rt, name, **kw: removed.append(name) or True)

        result = volumes.cleanup_orphaned_project_volumes("podman")
        assert result == ["pi-vol-proj-11111111"]
        assert removed == ["pi-vol-proj-11111111"]

    def test_keeps_active_project_volume(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-11111111\n")
        monkeypatch.setattr(volumes, "get_volume_label", lambda rt, name, label: str(Path(__file__).parent))
        monkeypatch.setattr(
            volumes,
            "remove_volume",
            lambda *a, **kw: pytest.fail("should not remove live project volume"),
        )

        result = volumes.cleanup_orphaned_project_volumes("podman")
        assert result == []


class TestVolumeHelpers:
    def test_volume_exists_true_on_success(self, monkeypatch):
        monkeypatch.setattr(volumes.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="[{}]"))
        assert volumes.volume_exists("podman", "pi-nested-abc") is True

    def test_volume_exists_false_on_failure(self, monkeypatch):
        monkeypatch.setattr(volumes.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=125, stdout=""))
        assert volumes.volume_exists("podman", "pi-nested-abc") is False

    def test_get_volume_label_reads_value(self, monkeypatch):
        monkeypatch.setattr(volumes.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="/tmp/proj\n"))
        assert volumes.get_volume_label("podman", "v", "pi-container.project.path") == "/tmp/proj"

    def test_get_volume_label_treats_no_value_as_absent(self, monkeypatch):
        """podman renders a missing label key as `<no value>`, not an error."""
        monkeypatch.setattr(volumes.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="<no value>\n"))
        assert volumes.get_volume_label("podman", "v", "nope") is None

    def test_remove_volume_reports_failure(self, monkeypatch):
        monkeypatch.setattr(
            volumes.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=2, stdout="", stderr="volume is being used"),
        )
        assert volumes.remove_volume("podman", "pi-nested-abc") is False

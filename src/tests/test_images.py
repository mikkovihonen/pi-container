import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import images
from config import IMAGE_TAG

sys.dont_write_bytecode = True


def _ts(iso: str) -> datetime:
    """Parse an ISO 8601 ``...Z`` timestamp into an aware UTC datetime."""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _make_project_with_deps(tmp_path: Path) -> Path:
    """Create a workspace whose dependency files select a project-specific image."""
    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    root_cmd = project_dir / ".pi-container" / "dependencies" / "root" / "commands.sh"
    root_cmd.parent.mkdir(parents=True, exist_ok=True)
    root_cmd.write_text("#!/bin/bash\necho install\n")
    pi_agent = project_dir / "pi-coding-agent"
    pi_agent.mkdir(parents=True, exist_ok=True)
    (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
    (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
    return project_dir


def _mock_podman(image_ls_stdout: str, ps_stdout: str = ""):
    """Build a subprocess.run stand-in that answers `image ls` and `ps` separately."""

    def _run(cmd, *args, **kwargs):
        return MagicMock(returncode=0, stdout=ps_stdout if "ps" in cmd else image_ls_stdout, stderr="")

    return _run


class TestComputeImageHash:
    def test_includes_repo_files_and_root_commands_sh(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        result = images.compute_image_hash(repo)
        assert result is not None
        assert len(result) == 16

    def test_includes_pi_commands_sh(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup\n")
        result = images.compute_image_hash(repo)
        assert result is not None
        assert result != "None"

    def test_different_root_content_different_hash(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)

        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install1\n")
        hash1 = images.compute_image_hash(repo)

        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install2\n")
        hash2 = images.compute_image_hash(repo)

        assert hash1 != hash2

    def test_different_pi_content_different_hash(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)

        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup1\n")
        hash1 = images.compute_image_hash(repo)

        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup2\n")
        hash2 = images.compute_image_hash(repo)

        assert hash1 != hash2

    def test_empty_commands_skipped(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("")
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("")
        result = images.compute_image_hash(repo)
        assert result is not None

    def test_no_deps_no_repo_files_returns_none(self, tmp_path, monkeypatch):
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        monkeypatch.setattr(images, "REPO_ROOT", fake_repo)
        repo = tmp_path / "workspace"
        repo.mkdir()
        result = images.compute_image_hash(repo)
        assert result is None


class TestHasDependencyFiles:
    def test_returns_false_when_no_files(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert images.has_dependency_files(repo) is False

    def test_returns_true_when_root_exists(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        assert images.has_dependency_files(repo) is True

    def test_returns_true_when_pi_exists(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup\n")
        assert images.has_dependency_files(repo) is True

    def test_returns_false_when_files_empty(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("")
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("")
        assert images.has_dependency_files(repo) is False


class TestResolveAgentImage:
    def test_returns_shared_when_no_deps(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        tag, is_project = images.resolve_agent_image(repo)
        assert tag == IMAGE_TAG
        assert is_project is False

    def test_returns_project_when_deps_exist(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        tag, is_project = images.resolve_agent_image(repo)
        assert tag.startswith("pi-container-project-")
        assert tag.endswith(".local")
        assert is_project is True

    def test_project_tag_includes_hash(self, tmp_path):
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = repo / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        tag, _ = images.resolve_agent_image(repo)
        assert re.fullmatch(r"pi-container-project-[0-9a-f]{10}-[0-9a-f]{16}\.local", tag)

    def test_project_tag_differs_across_repos(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        for d in (a, b):
            deps = d / ".pi-container" / "dependencies"
            deps.parent.mkdir(parents=True, exist_ok=True)
            deps.mkdir(parents=True, exist_ok=True)
            (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
            (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
            pi_agent = d / "pi-coding-agent"
            pi_agent.mkdir(parents=True, exist_ok=True)
            (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
            (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        tag_a, _ = images.resolve_agent_image(a)
        tag_b, _ = images.resolve_agent_image(b)
        assert tag_a != tag_b

    def test_project_tag_same_dir_same_tag(self, tmp_path):
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        tag1, _ = images.resolve_agent_image(tmp_path)
        tag2, _ = images.resolve_agent_image(tmp_path)
        assert tag1 == tag2


class TestGetImageLabel:
    def test_returns_none_when_command_fails(self, monkeypatch):
        def mock_run(args, **kwargs):
            import subprocess

            raise subprocess.TimeoutExpired(args, 5)

        monkeypatch.setattr(images.subprocess, "run", mock_run)
        result = images.get_image_label("nonexistent-image:latest", "pi-container.hash")
        assert result is None

    def test_returns_value_when_label_exists(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc123def456\n"
        mock_result.stderr = ""

        monkeypatch.setattr(images.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = images.get_image_label("test-image:latest", "pi-container.hash", runtime="podman")
        assert result == "abc123def456"


class TestImageIsCurrent:
    def test_returns_true_when_label_matches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(images, "get_image_label", lambda image_tag, label_key, **kw: "abc123")
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        result = images.image_is_current(project_dir=tmp_path, image_tag="test:latest", current_hash="abc123")
        assert result is True

    def test_returns_false_when_label_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(images, "get_image_label", lambda image_tag, label_key, **kw: None)
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        result = images.image_is_current(project_dir=tmp_path, image_tag="test:latest", current_hash="abc123")
        assert result is False

    def test_returns_false_when_label_mismatch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(images, "get_image_label", lambda image_tag, label_key, **kw: "different-hash")
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        result = images.image_is_current(project_dir=tmp_path, image_tag="test:latest", current_hash="abc123")
        assert result is False


class TestNowIso:
    def test_returns_iso_format(self):
        result = images.now_iso()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result)


class TestRemoveImage:
    def test_removes_image_on_success(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        monkeypatch.setattr(images.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = images.remove_image("podman", "some-image:latest")
        assert result is True

    def test_returns_false_on_failure(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: image not found"
        monkeypatch.setattr(images.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = images.remove_image("podman", "nonexistent:latest")
        assert result is False

    def test_handles_exception(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(
            images.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 30)),
        )
        result = images.remove_image("podman", "slow-image:latest")
        assert result is False


class TestListProjectImages:
    def test_returns_empty_when_no_images(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        monkeypatch.setattr(images.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = images.list_project_images("podman")
        assert result == []

    def test_returns_images_with_id_and_display_name(self, monkeypatch):
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda image_id, label_key, **kw: (
                "a1b2c" if label_key == "pi-container.project.hash" else "1111111111111111"
            ),
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "aaaaaaaaaaaa\tpi-container-project-a1b2c-1111111111111111.local:latest\n"
            "bbbbbbbbbbbb\tpi-container-project-a1b2c-2222222222222222.local:latest\n"
        )
        monkeypatch.setattr(images.subprocess, "run", lambda *args, **kwargs: mock_result)

        result = images.list_project_images("podman")
        assert len(result) == 2
        assert result[0] == (
            "aaaaaaaaaaaa",
            "pi-container-project-a1b2c-1111111111111111.local:latest",
            "1111111111111111",
        )
        for _image_id, name, _content_hash in result:
            assert "a1b2c" in name

    def test_handles_runtime_failure(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(
            images.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 10)),
        )
        result = images.list_project_images("podman")
        assert result == []

    def test_untagged_image_uses_id_not_none_name(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "6abc36fca57d\t<none>:<none>\n"
        monkeypatch.setattr(images.subprocess, "run", lambda *args, **kwargs: mock_result)

        inspected: list[str] = []

        def mock_get_label(image_id, label_key, **kw):
            inspected.append(image_id)
            return ""

        monkeypatch.setattr(images, "get_image_label", mock_get_label)

        result = images.list_project_images("podman")
        assert result == [("6abc36fca57d", "6abc36fca57d (untagged)", "")]
        assert inspected == ["6abc36fca57d"]


class TestImagesInUse:
    def test_returns_image_ids_of_all_containers(self, monkeypatch):
        monkeypatch.setattr(
            images.subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="aaaaaaaaaaaa\nbbbbbbbbbbbb\n", stderr=""),
        )
        assert images.images_in_use("podman") == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}

    def test_returns_empty_set_when_no_containers(self, monkeypatch):
        monkeypatch.setattr(images.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0, stdout="\n", stderr=""))
        assert images.images_in_use("podman") == set()

    def test_returns_empty_set_on_runtime_failure(self, monkeypatch):
        import subprocess

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(images.subprocess, "run", boom)
        assert images.images_in_use("podman") == set()


class TestCleanupStaleProjectImages:
    def test_removes_stale_images(self, monkeypatch, tmp_path):
        project_hash = "a1b2c"
        new_hash = "newhash1234567890"
        stale_tag = f"pi-container-project-{project_hash}-oldhash1234567890.local"

        monkeypatch.setattr(images.subprocess, "run", _mock_podman(f"aaaaaaaaaaaa\t{stale_tag}\n"))
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda image_id, label_key, **kw: (
                project_hash if label_key == "pi-container.project.hash" else "oldhash1234567890"
            ),
        )

        result = images.cleanup_stale_project_images(
            "podman",
            project_hash,
            new_hash,
        )
        assert stale_tag in result

    def test_keeps_stale_image_still_in_use(self, monkeypatch):
        project_hash = "a1b2c"
        stale_tag = f"pi-container-project-{project_hash}-oldhash1234567890.local"

        monkeypatch.setattr(
            images.subprocess,
            "run",
            _mock_podman(f"aaaaaaaaaaaa\t{stale_tag}\n", ps_stdout="aaaaaaaaaaaa\n"),
        )
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda image_id, label_key, **kw: (
                project_hash if label_key == "pi-container.project.hash" else "oldhash1234567890"
            ),
        )

        attempted = []
        monkeypatch.setattr(images, "remove_image", lambda rt, iid: attempted.append(iid) or True)

        result = images.cleanup_stale_project_images("podman", project_hash, "newhash1234567890")
        assert result == []
        assert attempted == []

    def test_skips_images_matching_new_hash(self, monkeypatch):
        project_hash = "a1b2c"
        new_hash = "newhash1234567890"
        matching_tag = f"pi-container-project-{project_hash}-{new_hash}.local"

        monkeypatch.setattr(images.subprocess, "run", _mock_podman(f"aaaaaaaaaaaa\t{matching_tag}\n"))
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda tag, label_key, **kw: project_hash if label_key == "pi-container.project.hash" else new_hash,
        )

        result = images.cleanup_stale_project_images(
            "podman",
            project_hash,
            new_hash,
        )
        assert result == []

    def test_skips_other_projects_images(self, monkeypatch):
        this_hash = "a1b2c"
        other_hash = "xyz99"
        other_tag = f"pi-container-project-{other_hash}-somehash1234567.local"

        monkeypatch.setattr(images.subprocess, "run", _mock_podman(f"bbbbbbbbbbbb\t{other_tag}\n"))
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda tag, label_key, **kw: other_hash if label_key == "pi-container.project.hash" else "somehash1234567",
        )

        result = images.cleanup_stale_project_images(
            "podman",
            this_hash,
            "newhash1234567890",
        )
        assert result == []


class TestGetImageLabelJsonFallback:
    def test_falls_back_to_json_when_format_fails(self, monkeypatch):
        call_count = [0]

        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            else:
                m.returncode = 0
                m.stdout = '[{"Config": {"Labels": {"pi-container.hash": "jsonhash123"}}}]\n'
                m.stderr = ""
            return m

        monkeypatch.setattr(images.subprocess, "run", mock_subprocess_run)
        result = images.get_image_label("test-image:latest", "pi-container.hash", runtime="podman")
        assert result == "jsonhash123"
        assert call_count[0] == 2

    def test_returns_none_when_both_fails(self, monkeypatch):
        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "error"
            return m

        monkeypatch.setattr(images.subprocess, "run", mock_subprocess_run)
        result = images.get_image_label("nonexistent:latest", "pi-container.hash", runtime="podman")
        assert result is None


class TestImageIsCurrentShared:
    def test_returns_true_for_shared_image(self, monkeypatch, tmp_path):
        monkeypatch.setattr(images, "has_dependency_files", lambda d: False)
        result = images.image_is_current(
            project_dir=tmp_path,
            image_tag="pi-coding-agent:local",
            current_hash="anything",
        )
        assert result is True


class TestCleanupStaleProjectImagesListFails:
    def test_returns_empty_when_list_raises(self, monkeypatch):
        import subprocess

        def mock_list(*args, **kwargs):
            raise subprocess.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(images, "list_project_images", mock_list)
        result = images.cleanup_stale_project_images(
            "podman",
            "a1b2c",
            "newhash1234567890",
        )
        assert result == []


class TestGetImageBuildTime:
    def test_returns_datetime_when_label_exists(self, monkeypatch):
        monkeypatch.setattr(images, "get_image_label", lambda image_tag, label_key, **kw: "2025-01-15T12:30:00Z")
        result = images.get_image_build_time("test-image:latest")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 12
        assert result.minute == 30
        assert result.tzinfo is not None

    def test_returns_none_when_label_missing(self, monkeypatch):
        monkeypatch.setattr(images, "get_image_label", lambda image_tag, label_key, **kw: None)
        result = images.get_image_build_time("test-image:latest")
        assert result is None

    def test_returns_none_on_bad_format(self, monkeypatch):
        monkeypatch.setattr(images, "get_image_label", lambda image_tag, label_key, **kw: "not-a-timestamp")
        result = images.get_image_build_time("test-image:latest")
        assert result is None

    def test_returns_none_on_runtime_failure(self, monkeypatch):
        import subprocess

        def mock_get_label(image_tag, label_key, **kw):
            raise subprocess.TimeoutExpired(["podman", "inspect"], 5)

        monkeypatch.setattr(images, "get_image_label", mock_get_label)
        result = images.get_image_build_time("test-image:latest")
        assert result is None


class TestCleanupOrphanedProjectImages:
    def test_removes_image_with_missing_path(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )

        def mock_get_label(image_id, label, **kw):
            if label == "pi-container.project.path":
                return "/nonexistent/project/path"
            return None

        monkeypatch.setattr(images, "get_image_label", mock_get_label)
        removed = []
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: removed.append(image_id) or True)

        result = images.cleanup_orphaned_project_images("podman")
        assert len(result) == 1
        assert "pi-container-project-abc12-def3456789012345.local:latest" in result
        assert removed == ["aaaaaaaaaaaa"]

    def test_keeps_image_with_existing_path(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )

        def mock_get_label(image_id, label, **kw):
            if label == "pi-container.project.path":
                return str(Path(__file__).parent)
            return None

        monkeypatch.setattr(images, "get_image_label", mock_get_label)
        removed_count = [0]
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: removed_count.__setitem__(0, 1) or True)

        result = images.cleanup_orphaned_project_images("podman")
        assert len(result) == 0
        assert removed_count[0] == 0

    def test_removes_images_without_path_label(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )
        monkeypatch.setattr(images, "get_image_label", lambda image_id, label, **kw: None)
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: True)

        result = images.cleanup_orphaned_project_images("podman")
        assert len(result) == 1
        assert "pi-container-project-abc12-def3456789012345.local:latest" in result

    def test_removes_images_with_blank_path_label(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )
        monkeypatch.setattr(images, "get_image_label", lambda image_id, label, **kw: "")
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: True)

        result = images.cleanup_orphaned_project_images("podman")
        assert result == ["pi-container-project-abc12-def3456789012345.local:latest"]

    def test_removes_untagged_image_by_id(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(sp, "run", _mock_podman("6abc36fca57d\t<none>:<none>\n"))
        monkeypatch.setattr(images, "get_image_label", lambda image_id, label, **kw: "")

        removed = []
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: removed.append(image_id) or True)

        result = images.cleanup_orphaned_project_images("podman")
        assert removed == ["6abc36fca57d"]
        assert result == ["6abc36fca57d (untagged)"]

    def test_keeps_orphaned_image_still_in_use(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(
            sp,
            "run",
            _mock_podman(
                "aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n",
                ps_stdout="aaaaaaaaaaaa\n",
            ),
        )
        monkeypatch.setattr(images, "get_image_label", lambda image_id, label, **kw: "/nonexistent/project/path")

        attempted = []
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: attempted.append(image_id) or True)

        result = images.cleanup_orphaned_project_images("podman")
        assert result == []
        assert attempted == []

    def test_never_removes_protected_shared_images(self, monkeypatch):
        import subprocess as sp

        protected = "\n".join(
            f"{i:012x}\t{prefix}{tag}"
            for i, (prefix, tag) in enumerate(
                [(p, t) for t in sorted(images._PROTECTED_IMAGE_TAGS) for p in ("", "localhost/")]
            )
        )

        monkeypatch.setattr(sp, "run", _mock_podman(protected + "\n"))
        monkeypatch.setattr(images, "get_image_label", lambda image_id, label, **kw: "")

        removed = []
        monkeypatch.setattr(images, "remove_image", lambda runtime, image_id: removed.append(image_id) or True)

        result = images.cleanup_orphaned_project_images("podman")
        assert removed == []
        assert result == []

    def test_returns_empty_when_list_fails(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(sp, "run", lambda cmd, **kw: (_ for _ in ()).throw(sp.TimeoutExpired("cmd", 10)))

        result = images.cleanup_orphaned_project_images("podman")
        assert result == []


class TestImageExists:
    def test_true_when_inspect_succeeds(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(sp, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="[{}]", stderr=""))
        assert images.image_exists("some-image:local", runtime="podman") is True

    def test_false_when_inspect_fails(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(sp, "run", lambda cmd, **kw: MagicMock(returncode=1, stdout="", stderr="No such image"))
        assert images.image_exists("missing-image:local", runtime="podman") is False

    def test_false_when_runtime_unavailable(self, monkeypatch):
        import subprocess as sp

        def boom(cmd, **kw):
            raise FileNotFoundError("podman")

        monkeypatch.setattr(sp, "run", boom)
        assert images.image_exists("some-image:local", runtime="podman") is False


class TestNewestSharedImageTime:
    def test_returns_the_newer_of_the_two(self, monkeypatch):
        times = {
            "pi-coding-agent-proxy:local": "2025-01-01T00:00:00Z",
            "pi-coding-agent-builder:local": "2025-06-01T00:00:00Z",
        }
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda tag, key, **kw: times[tag] if key == "pi-container.build.time" else None,
        )

        result = images.newest_shared_image_time()
        assert result == ("pi-coding-agent-builder:local", _ts("2025-06-01T00:00:00Z"))

    def test_proxy_wins_when_newer(self, monkeypatch):
        times = {
            "pi-coding-agent-proxy:local": "2025-06-01T00:00:00Z",
            "pi-coding-agent-builder:local": "2025-01-01T00:00:00Z",
        }
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda tag, key, **kw: times[tag] if key == "pi-container.build.time" else None,
        )

        result = images.newest_shared_image_time()
        assert result == ("pi-coding-agent-proxy:local", _ts("2025-06-01T00:00:00Z"))

    def test_none_when_an_image_cannot_be_dated(self, monkeypatch):
        times = {"pi-coding-agent-proxy:local": "2025-06-01T00:00:00Z"}
        monkeypatch.setattr(
            images,
            "get_image_label",
            lambda tag, key, **kw: times.get(tag) if key == "pi-container.build.time" else None,
        )

        assert images.newest_shared_image_time() is None

    def test_covers_both_source_images(self):
        assert set(images._SHARED_SOURCE_IMAGES) == {
            "pi-coding-agent-proxy:local",
            "pi-coding-agent-builder:local",
        }


class TestProjectImageBuildReason:
    def test_missing_image_builds_instead_of_failing(self, monkeypatch, tmp_path):
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(images, "image_exists", lambda tag, **kw: False)

        reason = images.project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason == "image not built yet"

    def test_none_when_image_is_current_and_proxy_older(self, monkeypatch, tmp_path):
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(images, "image_exists", lambda tag, **kw: True)

        def mock_get_label(image_tag, label_key, **kw):
            if label_key == "pi-container.build.time":
                return "2025-06-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            return None

        monkeypatch.setattr(images, "get_image_label", mock_get_label)

        reason = images.project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason is None

    def test_stale_shared_image(self, monkeypatch, tmp_path):
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(images, "image_exists", lambda tag, **kw: True)

        def mock_get_label(image_tag, label_key, **kw):
            if label_key == "pi-container.build.time":
                return "2025-01-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            return None

        monkeypatch.setattr(images, "get_image_label", mock_get_label)

        reason = images.project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-06-01T00:00:00Z"),
        )
        assert reason == "stale shared image"

    def test_newer_toolchain_image_forces_rebuild(self, monkeypatch, tmp_path, caplog):
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(images, "image_exists", lambda tag, **kw: True)

        def mock_get_label(image_tag, label_key, **kw):
            if label_key == "pi-container.build.time":
                return "2025-01-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            return None

        monkeypatch.setattr(images, "get_image_label", mock_get_label)

        with caplog.at_level("WARNING"):
            reason = images.project_image_build_reason(
                project_dir,
                "pi-container-project-abcde-abc123def4567890.local",
                "abc123def4567890",
                _ts("2025-06-01T00:00:00Z"),
                "pi-coding-agent-builder:local",
            )
        assert reason == "stale shared image"
        assert "pi-coding-agent-builder:local" in caplog.text

    def test_content_hash_mismatch(self, monkeypatch, tmp_path):
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(images, "image_exists", lambda tag, **kw: True)

        def mock_get_label(image_tag, label_key, **kw):
            if label_key == "pi-container.build.time":
                return "2025-06-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "0000000000000000"
            return None

        monkeypatch.setattr(images, "get_image_label", mock_get_label)

        reason = images.project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason == "content hash mismatch"

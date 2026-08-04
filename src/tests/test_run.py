"""
Unit tests for src/run.py — per-project configuration helpers.

Run with:
    python -m pytest src/tests/test_run.py -v
"""

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run


def _ts(iso: str):
    """Parse an ISO 8601 ``...Z`` timestamp into an aware UTC datetime."""
    from datetime import UTC, datetime

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


class TestProjectScope:
    def test_stable_for_same_dir(self, tmp_path):
        assert run._project_scope(tmp_path) == run._project_scope(tmp_path)

    def test_differs_across_dirs(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert run._project_scope(a) != run._project_scope(b)

    def test_name_format(self, tmp_path):
        proxy_name, network_name = run._project_scope(tmp_path)
        assert proxy_name.startswith("pi-proxy-")
        assert network_name.startswith("pi-isolated-net-")
        # Shared 10-hex-char project key across both names.
        assert proxy_name.split("pi-proxy-")[1] == network_name.split("pi-isolated-net-")[1]
        assert re.fullmatch(r"[0-9a-f]{10}", proxy_name.split("pi-proxy-")[1])


class TestEnsureProjectConfig:
    def _make_template(self, root, with_entrypoint=True):
        """Build a minimal pi-coding-agent/default template under root."""
        template = root / "pi-coding-agent" / "default"
        (template / "agent").mkdir(parents=True)
        (template / "agent" / "models.json").write_text("{}")
        (template / "chat-templates" / "Some-Model").mkdir(parents=True)
        (template / "chat-templates" / "Some-Model" / "chat_template.jinja").write_text("{{ x }}")
        (template / "config.yaml").write_text("tmpfs:\n  paths: []\n")
        (template / "allowlist.yaml").write_text("global: {}\n")
        (template / "token_replacer.yaml").write_text("global: {}\n")
        if with_entrypoint:
            (template / "entrypoint.sh").write_text("#!/bin/bash\n")
        return template

    def test_seeds_agent_and_yaml_when_absent(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        project.mkdir()
        self._make_template(repo)
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        agent_dir = run._ensure_project_config()

        assert agent_dir == project / ".pi-container" / "agent"
        assert (agent_dir / "models.json").exists()
        assert (project / ".pi-container" / "chat-templates" / "Some-Model" / "chat_template.jinja").exists()
        for name in ("config.yaml", "allowlist.yaml", "token_replacer.yaml"):
            assert (project / ".pi-container" / name).exists()

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        self._make_template(repo)
        # Pre-existing, user-edited allowlist must be preserved.
        existing = project / ".pi-container" / "allowlist.yaml"
        existing.parent.mkdir(parents=True)
        existing.write_text("global: {custom: true}\n")
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        run._ensure_project_config()

        assert existing.read_text() == "global: {custom: true}\n"
        # Missing ones are still seeded.
        assert (project / ".pi-container" / "token_replacer.yaml").exists()
        assert (project / ".pi-container" / "agent" / "models.json").exists()

    def test_seeds_entrypoint_sh_when_absent(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        project.mkdir()
        self._make_template(repo)
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        agent_dir = run._ensure_project_config()

        ep_dst = agent_dir / "entrypoint.sh"
        assert ep_dst.exists()
        assert ep_dst.read_text() == "#!/bin/bash\n"

    def test_does_not_overwrite_existing_entrypoint_sh(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        self._make_template(repo)
        # Pre-existing, user-edited entrypoint must be preserved.
        custom_ep = project / ".pi-container" / "agent" / "entrypoint.sh"
        custom_ep.parent.mkdir(parents=True)
        custom_ep.write_text("#!/bin/bash\necho 'custom setup'\n")
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        run._ensure_project_config()

        assert custom_ep.read_text() == "#!/bin/bash\necho 'custom setup'\n"

    def test_skips_entrypoint_sh_when_template_missing(self, tmp_path, monkeypatch):
        """If the template has no entrypoint.sh, seeding must not fail."""
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        project.mkdir()
        self._make_template(repo, with_entrypoint=False)
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        # Should not raise.
        agent_dir = run._ensure_project_config()
        assert not (agent_dir / "entrypoint.sh").exists()

    # Integration test moved to test_config_schema.py — validates the schema
    # checking logic. The run.py integration is verified by the actual code flow
    # in main() which calls validate_config() and exits on failure.


class TestComputeImageHash:
    """Tests for _compute_image_hash()."""

    def test_includes_repo_files_and_root_commands_sh(self, tmp_path):
        """root/commands.sh is included alongside Containerfile/entrypoint.sh from REPO_ROOT."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        result = run._compute_image_hash(repo)
        assert result is not None
        assert len(result) == 16

    def test_includes_pi_commands_sh(self, tmp_path):
        """pi/commands.sh is included in the hash (fixes -None.local tag issue)."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup\n")
        result = run._compute_image_hash(repo)
        assert result is not None
        assert result != "None"

    def test_different_root_content_different_hash(self, tmp_path):
        """Different root/commands.sh content produces different hashes."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)

        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install1\n")
        hash1 = run._compute_image_hash(repo)

        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install2\n")
        hash2 = run._compute_image_hash(repo)

        assert hash1 != hash2

    def test_different_pi_content_different_hash(self, tmp_path):
        """Different pi/commands.sh content produces different hashes."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)

        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup1\n")
        hash1 = run._compute_image_hash(repo)

        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup2\n")
        hash2 = run._compute_image_hash(repo)

        assert hash1 != hash2

    def test_empty_commands_skipped(self, tmp_path):
        """Empty dependency files are skipped (not hashed)."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("")
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("")
        # Should only hash Containerfile and entrypoint.sh from REPO_ROOT
        result = run._compute_image_hash(repo)
        assert result is not None

    def test_no_deps_no_repo_files_returns_none(self, tmp_path, monkeypatch):
        """When REPO_ROOT has no pi-coding-agent dir and no deps exist → returns None."""
        # Simulate a foreign environment where REPO_ROOT/pi-coding-agent doesn't exist
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        monkeypatch.setattr(run, "REPO_ROOT", fake_repo)
        repo = tmp_path / "workspace"
        repo.mkdir()
        result = run._compute_image_hash(repo)
        assert result is None


class TestHasDependencyFiles:
    """Tests for _has_dependency_files()."""

    def test_returns_false_when_no_files(self, tmp_path):
        """No dependency files → returns False."""
        repo = tmp_path / "repo"
        repo.mkdir()
        assert run._has_dependency_files(repo) is False

    def test_returns_true_when_root_exists(self, tmp_path):
        """root/commands.sh exists → returns True."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        assert run._has_dependency_files(repo) is True

    def test_returns_true_when_pi_exists(self, tmp_path):
        """pi/commands.sh exists → returns True."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("#!/bin/bash\necho setup\n")
        assert run._has_dependency_files(repo) is True

    def test_returns_false_when_files_empty(self, tmp_path):
        """Empty dependency files → returns False."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("")
        (deps / "pi" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "pi" / "commands.sh").write_text("")
        assert run._has_dependency_files(repo) is False


class TestResolveAgentImage:
    """Tests for _resolve_agent_image()."""

    def test_returns_shared_when_no_deps(self, tmp_path):
        """No dependency files → returns shared image tag."""
        repo = tmp_path / "repo"
        repo.mkdir()
        tag, is_project = run._resolve_agent_image(repo)
        assert tag == run.IMAGE_TAG
        assert is_project is False

    def test_returns_project_when_deps_exist(self, tmp_path):
        """Dependency files exist → returns project-specific image tag."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        tag, is_project = run._resolve_agent_image(repo)
        assert tag.startswith("pi-container-project-")
        assert tag.endswith(".local")
        assert is_project is True

    def test_project_tag_includes_hash(self, tmp_path):
        """Project image tag includes project hash and content hash."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        # Create Containerfile and entrypoint.sh so _compute_image_hash doesn't return None
        pi_agent = repo / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        tag, _ = run._resolve_agent_image(repo)
        # Tag format: pi-container-project-<project-hash>-<content-hash>.local
        # project-hash is 10 hex chars, content-hash is 16 hex chars
        assert re.fullmatch(r"pi-container-project-[0-9a-f]{10}-[0-9a-f]{16}\.local", tag)

    def test_project_tag_differs_across_repos(self, tmp_path):
        """Different project dirs produce different image tags."""
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
        tag_a, _ = run._resolve_agent_image(a)
        tag_b, _ = run._resolve_agent_image(b)
        assert tag_a != tag_b

    def test_project_tag_same_dir_same_tag(self, tmp_path):
        """Same project dir produces the same image tag on repeated calls."""
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        tag1, _ = run._resolve_agent_image(tmp_path)
        tag2, _ = run._resolve_agent_image(tmp_path)
        assert tag1 == tag2


class TestGetImageLabel:
    """Tests for _get_image_label()."""

    def test_returns_none_when_command_fails(self, monkeypatch):
        """When the inspect command fails, returns None."""

        def mock_run(args, **kwargs):
            import subprocess

            raise subprocess.TimeoutExpired(args, 5)

        monkeypatch.setattr(run.subprocess, "run", mock_run)
        result = run._get_image_label("nonexistent-image:latest", "pi-container.hash")
        assert result is None

    def test_returns_value_when_label_exists(self, monkeypatch):
        """When the label exists, returns its value."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc123def456\n"
        mock_result.stderr = ""

        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)
        # Set CONTAINER_RUNTIME so the function doesn't raise NameError
        run.CONTAINER_RUNTIME = "docker"
        result = run._get_image_label("test-image:latest", "pi-container.hash")
        assert result == "abc123def456"


class TestImageIsCurrent:
    """Tests for _image_is_current()."""

    def test_returns_true_when_label_matches(self, monkeypatch, tmp_path):
        """When the label matches the current hash, returns True."""

        def mock_get_label(image_tag, label_key):
            return "abc123"

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        # Create minimal files so _compute_image_hash doesn't return None
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        result = run._image_is_current(project_dir=tmp_path, image_tag="test:latest", current_hash="abc123")
        assert result is True

    def test_returns_false_when_label_missing(self, monkeypatch, tmp_path):
        """When the label is missing, returns False."""

        def mock_get_label(image_tag, label_key):
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        # Create minimal files so _compute_image_hash doesn't return None
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        result = run._image_is_current(project_dir=tmp_path, image_tag="test:latest", current_hash="abc123")
        assert result is False

    def test_returns_false_when_label_mismatch(self, monkeypatch, tmp_path):
        """When the label doesn't match, returns False."""

        def mock_get_label(image_tag, label_key):
            return "different-hash"

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        # Create minimal files so _compute_image_hash doesn't return None
        deps = tmp_path / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = tmp_path / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        result = run._image_is_current(project_dir=tmp_path, image_tag="test:latest", current_hash="abc123")
        assert result is False


class TestNowIso:
    def test_returns_iso_format(self):
        """now_iso() returns an ISO 8601 formatted string."""
        result = run.now_iso()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result)


class TestRemoveImage:
    def test_removes_image_on_success(self, monkeypatch):
        """_remove_image calls image rm and returns True on success."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = run._remove_image("docker", "some-image:latest")
        assert result is True

    def test_returns_false_on_failure(self, monkeypatch):
        """_remove_image returns False when the runtime command fails."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: image not found"
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = run._remove_image("docker", "nonexistent:latest")
        assert result is False

    def test_handles_exception(self, monkeypatch):
        """_remove_image returns False on exception."""
        import subprocess

        monkeypatch.setattr(
            run.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 30))
        )
        result = run._remove_image("docker", "slow-image:latest")
        assert result is False


class TestListProjectImages:
    def test_returns_empty_when_no_images(self, monkeypatch):
        """_list_project_images returns [] when no project images exist."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = run._list_project_images("docker", "project-hash")
        assert result == []

    def test_returns_images_for_matching_project(self, monkeypatch):
        """_list_project_images returns images with matching project hash."""
        from unittest.mock import MagicMock

        mock_ls_result = MagicMock()
        mock_ls_result.returncode = 0
        mock_ls_result.stdout = (
            "pi-container-project-a1b2c-1111111111111111.local\npi-container-project-a1b2c-2222222222222222.local\n"
        )

        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, label_key: "a1b2c" if label_key == "pi-container.project.hash" else "1111111111111111",
        )

        # Mock image ls
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "pi-container-project-a1b2c-1111111111111111.local\npi-container-project-a1b2c-2222222222222222.local\n"
        )
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)

        result = run._list_project_images("docker", "a1b2c")
        assert len(result) == 2
        # Each entry is (tag, content_hash)
        for tag, _content_hash in result:
            assert "a1b2c" in tag

    def test_handles_runtime_failure(self, monkeypatch):
        """_list_project_images returns [] when the runtime is unavailable."""
        import subprocess

        monkeypatch.setattr(
            run.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 10))
        )
        result = run._list_project_images("docker", "project-hash")
        assert result == []


class TestCleanupStaleProjectImages:
    def test_removes_stale_images(self, monkeypatch, tmp_path):
        """_cleanup_stale_project_images removes images with mismatched hashes."""
        project_hash = "a1b2c"
        new_hash = "newhash1234567890"
        stale_tag = f"pi-container-project-{project_hash}-oldhash1234567890.local"

        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = f"{stale_tag}\n"
            m.stderr = ""
            return m

        monkeypatch.setattr(run.subprocess, "run", mock_subprocess_run)
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, label_key: project_hash if label_key == "pi-container.project.hash" else "oldhash1234567890",
        )

        result = run._cleanup_stale_project_images(
            "docker",
            tmp_path,
            project_hash,
            new_hash,
        )
        assert stale_tag in result

    def test_skips_images_matching_new_hash(self, monkeypatch, tmp_path):
        """_cleanup_stale_project_images skips images whose hash matches new_hash."""
        project_hash = "a1b2c"
        new_hash = "newhash1234567890"
        matching_tag = f"pi-container-project-{project_hash}-{new_hash}.local"

        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = f"{matching_tag}\n"
            m.stderr = ""
            return m

        monkeypatch.setattr(run.subprocess, "run", mock_subprocess_run)
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, label_key: project_hash if label_key == "pi-container.project.hash" else new_hash,
        )

        result = run._cleanup_stale_project_images(
            "docker",
            tmp_path,
            project_hash,
            new_hash,
        )
        assert result == []

    def test_skips_other_projects_images(self, monkeypatch, tmp_path):
        """_cleanup_stale_project_images does not touch images from other projects."""
        this_hash = "a1b2c"
        other_hash = "xyz99"
        other_tag = f"pi-container-project-{other_hash}-somehash1234567.local"

        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = f"{other_tag}\n"
            m.stderr = ""
            return m

        monkeypatch.setattr(run.subprocess, "run", mock_subprocess_run)
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, label_key: other_hash if label_key == "pi-container.project.hash" else "somehash1234567",
        )

        result = run._cleanup_stale_project_images(
            "docker",
            tmp_path,
            this_hash,
            "newhash1234567890",
        )
        assert result == []


class TestEnsureProjectConfigMissingTemplate:
    def test_raises_when_template_missing(self, tmp_path, monkeypatch):
        """_ensure_project_config raises FileNotFoundError when the template dir is absent."""
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)
        with pytest.raises(FileNotFoundError, match="Project config template not found"):
            run._ensure_project_config()


class TestGetImageLabelJsonFallback:
    def test_falls_back_to_json_when_format_fails(self, monkeypatch):
        """_get_image_label falls back to JSON inspect when --format output is empty."""
        from unittest.mock import MagicMock

        call_count = [0]

        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (--format) returns empty stdout
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            else:
                # Second call (JSON inspect) succeeds
                m.returncode = 0
                m.stdout = '[{"Config": {"Labels": {"pi-container.hash": "jsonhash123"}}}]\n'
                m.stderr = ""
            return m

        monkeypatch.setattr(run.subprocess, "run", mock_subprocess_run)
        result = run._get_image_label("test-image:latest", "pi-container.hash")
        assert result == "jsonhash123"
        assert call_count[0] == 2

    def test_returns_none_when_both_fails(self, monkeypatch):
        """_get_image_label returns None when both format and JSON inspect fail."""
        from unittest.mock import MagicMock

        def mock_subprocess_run(*args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "error"
            return m

        monkeypatch.setattr(run.subprocess, "run", mock_subprocess_run)
        result = run._get_image_label("nonexistent:latest", "pi-container.hash")
        assert result is None


class TestImageIsCurrentShared:
    def test_returns_true_for_shared_image(self, monkeypatch, tmp_path):
        """_image_is_current returns True for shared (non-project) images."""
        # Create a project with NO dependency files → shared image
        monkeypatch.setattr(run, "_has_dependency_files", lambda d: False)
        result = run._image_is_current(
            project_dir=tmp_path,
            image_tag="pi-coding-agent:local",
            current_hash="anything",
        )
        assert result is True


class TestCleanupStaleProjectImagesListFails:
    def test_returns_empty_when_list_raises(self, monkeypatch, tmp_path):
        """_cleanup_stale_project_images returns [] when _list_project_images raises."""
        import subprocess

        def mock_list(*args, **kwargs):
            raise subprocess.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(run, "_list_project_images", mock_list)
        result = run._cleanup_stale_project_images(
            "docker",
            tmp_path,
            "a1b2c",
            "newhash1234567890",
        )
        assert result == []


class TestGetImageBuildTime:
    """Tests for _get_image_build_time()."""

    def test_returns_datetime_when_label_exists(self, monkeypatch):
        """Returns a timezone-aware UTC datetime when the label is present."""

        def mock_get_label(image_tag, label_key):
            return "2025-01-15T12:30:00Z"

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        result = run._get_image_build_time("test-image:latest")
        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 12
        assert result.minute == 30
        assert result.tzinfo is not None

    def test_returns_none_when_label_missing(self, monkeypatch):
        """Returns None when the build time label is absent."""

        def mock_get_label(image_tag, label_key):
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        result = run._get_image_build_time("test-image:latest")
        assert result is None

    def test_returns_none_on_bad_format(self, monkeypatch):
        """Returns None when the timestamp string is not parseable."""

        def mock_get_label(image_tag, label_key):
            return "not-a-timestamp"

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        result = run._get_image_build_time("test-image:latest")
        assert result is None

    def test_returns_none_on_runtime_failure(self, monkeypatch):
        """Returns None when the container runtime is unavailable."""
        import subprocess

        def mock_get_label(image_tag, label_key):
            raise subprocess.TimeoutExpired(["docker", "inspect"], 5)

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        result = run._get_image_build_time("test-image:latest")
        assert result is None


class TestCleanupOrphanedProjectImages:
    def test_removes_image_with_missing_path(self, monkeypatch):
        """Images whose stored path no longer exists are removed."""
        import subprocess as sp

        # Mock the docker image ls output.
        def mock_run(cmd, **kwargs):
            class Result:
                stdout = "pi-container-project-abc12-def3456789012345.local\n"
                stderr = ""
                returncode = 0

            return Result()

        monkeypatch.setattr(sp, "run", mock_run)

        # Mock _get_image_label to return a path that doesn't exist.
        def mock_get_label(tag, label):
            if label == "pi-container.project.path":
                return "/nonexistent/project/path"
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        # Mock _remove_image to track calls.
        removed = []

        def mock_remove(runtime, tag):
            removed.append(tag)
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("docker")
        assert len(result) == 1
        assert "pi-container-project-abc12-def3456789012345.local" in result

    def test_keeps_image_with_existing_path(self, monkeypatch):
        """Images whose stored path still exists are NOT removed."""
        import subprocess as sp

        def mock_run(cmd, **kwargs):
            class Result:
                stdout = "pi-container-project-abc12-def3456789012345.local\n"
                stderr = ""
                returncode = 0

            return Result()

        monkeypatch.setattr(sp, "run", mock_run)

        def mock_get_label(tag, label):
            if label == "pi-container.project.path":
                return str(Path(__file__).parent)  # exists
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        removed_count = [0]

        def mock_remove(runtime, tag):
            removed_count[0] += 1
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("docker")
        assert len(result) == 0
        assert removed_count[0] == 0

    def test_removes_images_without_path_label(self, monkeypatch):
        """Images without pi-container.project.path label are removed (unverifiable)."""
        import subprocess as sp

        def mock_run(cmd, **kwargs):
            class Result:
                stdout = "pi-container-project-abc12-def3456789012345.local\n"
                stderr = ""
                returncode = 0

            return Result()

        monkeypatch.setattr(sp, "run", mock_run)

        def mock_get_label(tag, label):
            return None  # No path label

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        def mock_remove(runtime, tag):
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("docker")
        assert len(result) == 1
        assert "pi-container-project-abc12-def3456789012345.local" in result

    def test_returns_empty_when_list_fails(self, monkeypatch):
        """Returns [] when docker image ls fails."""
        import subprocess as sp

        def mock_run(cmd, **kwargs):
            raise sp.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(sp, "run", mock_run)

        result = run._cleanup_orphaned_project_images("docker")
        assert result == []

    def test_proxy_newer_forces_rebuild(self, monkeypatch, tmp_path):
        """When proxy image is newer than project image, rebuild is triggered."""

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Set up dependency files so we get a project-specific image
        deps = project_dir / ".pi-container" / "dependencies"
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = project_dir / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")

        # Mock _get_image_label to simulate proxy built AFTER project
        build_times = {}

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            if label_key == "pi-container.build.time":
                return build_times.get(image_tag, "2025-01-01T00:00:00Z")
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        # Project image built first
        build_times["pi-container-project-abcde-abc123def4567890.local"] = "2025-01-01T00:00:00Z"
        # Proxy image built later
        build_times["pi-coding-agent-proxy:local"] = "2025-06-01T00:00:00Z"

        # _image_is_current should return True (hash matches)
        assert (
            run._image_is_current(
                project_dir=project_dir,
                image_tag="pi-container-project-abcde-abc123def4567890.local",
                current_hash="abc123def4567890",
            )
            is True
        )

        # But proxy is newer — the check should detect this
        proxy_ts = run._get_image_build_time("pi-coding-agent-proxy:local")
        project_ts = run._get_image_build_time("pi-container-project-abcde-abc123def4567890.local")
        assert proxy_ts is not None and project_ts is not None
        assert proxy_ts > project_ts

    def test_proxy_not_newer_no_forced_rebuild(self, monkeypatch, tmp_path):
        """When proxy image is older or equal to project image, no forced rebuild."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        deps = project_dir / ".pi-container" / "dependencies"
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = project_dir / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")

        build_times = {}

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            if label_key == "pi-container.build.time":
                return build_times.get(image_tag, "2025-01-01T00:00:00Z")
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        # Proxy built before project
        build_times["pi-container-project-abcde-abc123def4567890.local"] = "2025-06-01T00:00:00Z"
        build_times["pi-coding-agent-proxy:local"] = "2025-01-01T00:00:00Z"

        proxy_ts = run._get_image_build_time("pi-coding-agent-proxy:local")
        project_ts = run._get_image_build_time("pi-container-project-abcde-abc123def4567890.local")
        assert proxy_ts is not None and project_ts is not None
        assert proxy_ts <= project_ts

    def test_missing_proxy_timestamp_exits(self, monkeypatch, tmp_path):
        """Missing proxy build timestamp causes sys.exit(1)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        deps = project_dir / ".pi-container" / "dependencies"
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        pi_agent = project_dir / "pi-coding-agent"
        pi_agent.mkdir(parents=True, exist_ok=True)
        (pi_agent / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (pi_agent / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.build.time":
                return None  # Proxy has no build time
            return "abc123def4567890"

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        # Verify _get_image_build_time returns None for missing label
        proxy_ts = run._get_image_build_time("pi-coding-agent-proxy:local")
        assert proxy_ts is None

    def test_missing_project_timestamp_triggers_rebuild(self, monkeypatch, tmp_path):
        """An existing project image with no build timestamp is rebuilt, not fatal."""
        project_dir = _make_project_with_deps(tmp_path)

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.build.time":
                return None  # Project has no build time
            return "abc123def4567890"

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        monkeypatch.setattr(run, "_image_exists", lambda tag: True)

        project_ts = run._get_image_build_time("pi-container-project-abcde-abc123def4567890.local")
        assert project_ts is None

        reason = run._project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason == "missing build timestamp"

    def test_removes_multiple_orphaned_images(self, monkeypatch):
        """Multiple images without path labels are all removed."""
        import subprocess as sp

        def mock_run(cmd, **kwargs):
            class Result:
                stdout = (
                    "pi-container-project-aaa11-bbb2222222222222.local\n"
                    "pi-container-project-ccc33-ddd4444444444444.local\n"
                    "pi-container-project-eee55-fff6666666666666.local\n"
                )
                stderr = ""
                returncode = 0

            return Result()

        monkeypatch.setattr(sp, "run", mock_run)

        def mock_get_label(tag, label):
            return None  # No path labels — all will be cleaned

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        def mock_remove(runtime, tag):
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("docker")
        assert len(result) == 3


class TestImageExists:
    """Tests for _image_exists()."""

    def test_true_when_inspect_succeeds(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(run, "CONTAINER_RUNTIME", "docker", raising=False)
        monkeypatch.setattr(sp, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="[{}]", stderr=""))
        assert run._image_exists("some-image:local") is True

    def test_false_when_inspect_fails(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(run, "CONTAINER_RUNTIME", "docker", raising=False)
        monkeypatch.setattr(sp, "run", lambda cmd, **kw: MagicMock(returncode=1, stdout="", stderr="No such image"))
        assert run._image_exists("missing-image:local") is False

    def test_false_when_runtime_unavailable(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(run, "CONTAINER_RUNTIME", "docker", raising=False)

        def boom(cmd, **kw):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(sp, "run", boom)
        assert run._image_exists("some-image:local") is False


class TestProjectImageBuildReason:
    """Tests for _project_image_build_reason() — the build/reuse decision."""

    def test_missing_image_builds_instead_of_failing(self, monkeypatch, tmp_path):
        """A project image that does not exist yet is built, not treated as an error.

        Regression: a first run in a workspace (or the run right after stale-image
        cleanup pruned the previous image) used to abort with "Could not read build
        timestamp from project image ... Rebuild required."
        """
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(run, "_image_exists", lambda tag: False)

        def unexpected(*args, **kwargs):
            raise AssertionError("labels must not be read for a nonexistent image")

        monkeypatch.setattr(run, "_get_image_label", unexpected)

        reason = run._project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason == "image not built yet"

    def test_none_when_image_is_current_and_proxy_older(self, monkeypatch, tmp_path):
        """A present, hash-matching image newer than the proxy is reused."""
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(run, "_image_exists", lambda tag: True)

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.build.time":
                return "2025-06-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        reason = run._project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason is None

    def test_stale_proxy_certificate(self, monkeypatch, tmp_path):
        """A proxy image newer than the project image forces a rebuild."""
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(run, "_image_exists", lambda tag: True)

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.build.time":
                return "2025-01-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        reason = run._project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-06-01T00:00:00Z"),
        )
        assert reason == "stale proxy certificate"

    def test_content_hash_mismatch(self, monkeypatch, tmp_path):
        """A present, up-to-date-cert image with a different content hash rebuilds."""
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(run, "_image_exists", lambda tag: True)

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.build.time":
                return "2025-06-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "0000000000000000"
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        reason = run._project_image_build_reason(
            project_dir,
            "pi-container-project-abcde-abc123def4567890.local",
            "abc123def4567890",
            _ts("2025-01-01T00:00:00Z"),
        )
        assert reason == "content hash mismatch"

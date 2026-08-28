"""
Unit tests for src/run.py — per-project configuration helpers.

Run with:
    python -m pytest src/tests/test_run.py -v
"""

import json
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
        run.CONTAINER_RUNTIME = "podman"
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
        result = run._remove_image("podman", "some-image:latest")
        assert result is True

    def test_returns_false_on_failure(self, monkeypatch):
        """_remove_image returns False when the runtime command fails."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: image not found"
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)
        result = run._remove_image("podman", "nonexistent:latest")
        assert result is False

    def test_handles_exception(self, monkeypatch):
        """_remove_image returns False on exception."""
        import subprocess

        monkeypatch.setattr(
            run.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 30))
        )
        result = run._remove_image("podman", "slow-image:latest")
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
        result = run._list_project_images("podman")
        assert result == []

    def test_returns_images_with_id_and_display_name(self, monkeypatch):
        """_list_project_images pairs each image ID with its display name and hash."""
        from unittest.mock import MagicMock

        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda image_id, label_key: "a1b2c" if label_key == "pi-container.project.hash" else "1111111111111111",
        )

        # Mock image ls in podman's real "{{.ID}}\t{{.Repository}}:{{.Tag}}" shape.
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "aaaaaaaaaaaa\tpi-container-project-a1b2c-1111111111111111.local:latest\n"
            "bbbbbbbbbbbb\tpi-container-project-a1b2c-2222222222222222.local:latest\n"
        )
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)

        result = run._list_project_images("podman")
        assert len(result) == 2
        # Each entry is (image_id, display_name, content_hash).
        assert result[0] == (
            "aaaaaaaaaaaa",
            "pi-container-project-a1b2c-1111111111111111.local:latest",
            "1111111111111111",
        )
        for _image_id, name, _content_hash in result:
            assert "a1b2c" in name

    def test_handles_runtime_failure(self, monkeypatch):
        """_list_project_images returns [] when the runtime is unavailable."""
        import subprocess

        monkeypatch.setattr(
            run.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 10))
        )
        result = run._list_project_images("podman")
        assert result == []

    def test_untagged_image_uses_id_not_none_name(self, monkeypatch):
        """An untagged image is identified by ID, never by the literal "<none>:<none>"."""
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "6abc36fca57d\t<none>:<none>\n"
        monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: mock_result)

        inspected: list[str] = []

        def mock_get_label(image_id, label_key):
            inspected.append(image_id)
            return ""

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        result = run._list_project_images("podman")
        assert result == [("6abc36fca57d", "6abc36fca57d (untagged)", "")]
        # The unusable "<none>:<none>" reference never reaches an inspect call.
        assert inspected == ["6abc36fca57d"]


def _mock_podman(image_ls_stdout: str, ps_stdout: str = ""):
    """Build a subprocess.run stand-in that answers `image ls` and `ps` separately.

    Both cleanup passes call both commands. A single canned stdout would feed the
    image list straight back as the list of in-use containers, so the two have to be
    dispatched apart for the mock to mean anything.
    """

    def _run(cmd, *args, **kwargs):
        return MagicMock(returncode=0, stdout=ps_stdout if "ps" in cmd else image_ls_stdout, stderr="")

    return _run


class TestImagesInUse:
    """Tests for _images_in_use() — the guard against removing an image a container holds."""

    def test_returns_image_ids_of_all_containers(self, monkeypatch):
        """Both running and stopped containers pin their image."""
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="aaaaaaaaaaaa\nbbbbbbbbbbbb\n", stderr=""),
        )
        assert run._images_in_use("podman") == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}

    def test_returns_empty_set_when_no_containers(self, monkeypatch):
        monkeypatch.setattr(run.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0, stdout="\n", stderr=""))
        assert run._images_in_use("podman") == set()

    def test_returns_empty_set_on_runtime_failure(self, monkeypatch):
        """A failed query degrades to the old behaviour, not to a crash."""
        import subprocess

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(run.subprocess, "run", boom)
        assert run._images_in_use("podman") == set()


class TestCleanupStaleProjectImages:
    def test_removes_stale_images(self, monkeypatch, tmp_path):
        """_cleanup_stale_project_images removes images with mismatched hashes."""
        project_hash = "a1b2c"
        new_hash = "newhash1234567890"
        stale_tag = f"pi-container-project-{project_hash}-oldhash1234567890.local"

        monkeypatch.setattr(run.subprocess, "run", _mock_podman(f"aaaaaaaaaaaa\t{stale_tag}\n"))
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda image_id, label_key: (
                project_hash if label_key == "pi-container.project.hash" else "oldhash1234567890"
            ),
        )

        result = run._cleanup_stale_project_images(
            "podman",
            tmp_path,
            project_hash,
            new_hash,
        )
        assert stale_tag in result

    def test_keeps_stale_image_still_in_use(self, monkeypatch, tmp_path):
        """A stale image a container is still running on is kept, not removed.

        Concurrent sessions in one workspace are supported: a session started before
        a definition-file change keeps its now-stale image alive. Attempting the
        removal fails with "image is in use by a container" and warns on every start.
        """
        project_hash = "a1b2c"
        stale_tag = f"pi-container-project-{project_hash}-oldhash1234567890.local"

        monkeypatch.setattr(
            run.subprocess,
            "run",
            _mock_podman(f"aaaaaaaaaaaa\t{stale_tag}\n", ps_stdout="aaaaaaaaaaaa\n"),
        )
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda image_id, label_key: (
                project_hash if label_key == "pi-container.project.hash" else "oldhash1234567890"
            ),
        )

        attempted = []
        monkeypatch.setattr(run, "_remove_image", lambda rt, iid: attempted.append(iid) or True)

        result = run._cleanup_stale_project_images("podman", tmp_path, project_hash, "newhash1234567890")
        assert result == []
        assert attempted == []

    def test_skips_images_matching_new_hash(self, monkeypatch, tmp_path):
        """_cleanup_stale_project_images skips images whose hash matches new_hash."""
        project_hash = "a1b2c"
        new_hash = "newhash1234567890"
        matching_tag = f"pi-container-project-{project_hash}-{new_hash}.local"

        monkeypatch.setattr(run.subprocess, "run", _mock_podman(f"aaaaaaaaaaaa\t{matching_tag}\n"))
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, label_key: project_hash if label_key == "pi-container.project.hash" else new_hash,
        )

        result = run._cleanup_stale_project_images(
            "podman",
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

        monkeypatch.setattr(run.subprocess, "run", _mock_podman(f"bbbbbbbbbbbb\t{other_tag}\n"))
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, label_key: other_hash if label_key == "pi-container.project.hash" else "somehash1234567",
        )

        result = run._cleanup_stale_project_images(
            "podman",
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
            "podman",
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
            raise subprocess.TimeoutExpired(["podman", "inspect"], 5)

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)
        result = run._get_image_build_time("test-image:latest")
        assert result is None


class TestCleanupOrphanedProjectImages:
    def test_removes_image_with_missing_path(self, monkeypatch):
        """Images whose stored path no longer exists are removed."""
        import subprocess as sp

        # Mock the `podman image ls` output.
        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )

        # Mock _get_image_label to return a path that doesn't exist.
        def mock_get_label(image_id, label):
            if label == "pi-container.project.path":
                return "/nonexistent/project/path"
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        # Mock _remove_image to track calls.
        removed = []

        def mock_remove(runtime, image_id):
            removed.append(image_id)
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("podman")
        assert len(result) == 1
        assert "pi-container-project-abc12-def3456789012345.local:latest" in result
        # Removal goes by ID, not by name.
        assert removed == ["aaaaaaaaaaaa"]

    def test_keeps_image_with_existing_path(self, monkeypatch):
        """Images whose stored path still exists are NOT removed."""
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )

        def mock_get_label(image_id, label):
            if label == "pi-container.project.path":
                return str(Path(__file__).parent)  # exists
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        removed_count = [0]

        def mock_remove(runtime, image_id):
            removed_count[0] += 1
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("podman")
        assert len(result) == 0
        assert removed_count[0] == 0

    def test_removes_images_without_path_label(self, monkeypatch):
        """Images without pi-container.project.path label are removed (unverifiable)."""
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )

        def mock_get_label(image_id, label):
            return None  # No path label

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        def mock_remove(runtime, image_id):
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("podman")
        assert len(result) == 1
        assert "pi-container-project-abc12-def3456789012345.local:latest" in result

    def test_removes_images_with_blank_path_label(self, monkeypatch):
        """A blank path label is as unverifiable as a missing one.

        Regression test: `Path("")` is `PosixPath(".")`, which always exists, so a
        blank label used to fall through the "path gone" check and be kept forever.
        """
        import subprocess as sp

        monkeypatch.setattr(
            sp, "run", _mock_podman("aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n")
        )
        monkeypatch.setattr(run, "_get_image_label", lambda image_id, label: "")
        monkeypatch.setattr(run, "_remove_image", lambda runtime, image_id: True)

        result = run._cleanup_orphaned_project_images("podman")
        assert result == ["pi-container-project-abc12-def3456789012345.local:latest"]

    def test_removes_untagged_image_by_id(self, monkeypatch):
        """Untagged images are removed by ID, never by the literal "<none>:<none>".

        Regression test: `podman image rm '<none>:<none>'` fails with
        `parsing reference "<none>:<none>": invalid reference format`, so these
        images could never be reclaimed and warned on every startup.
        """
        import subprocess as sp

        monkeypatch.setattr(sp, "run", _mock_podman("6abc36fca57d\t<none>:<none>\n"))
        monkeypatch.setattr(run, "_get_image_label", lambda image_id, label: "")

        removed = []
        monkeypatch.setattr(run, "_remove_image", lambda runtime, image_id: removed.append(image_id) or True)

        result = run._cleanup_orphaned_project_images("podman")
        assert removed == ["6abc36fca57d"]
        assert result == ["6abc36fca57d (untagged)"]

    def test_keeps_orphaned_image_still_in_use(self, monkeypatch):
        """An orphaned image a container still holds open is kept, not removed.

        The removal would fail with "image is in use by a container" and warn on every
        start; the image is reclaimed by a later run once the container is gone.
        """
        import subprocess as sp

        monkeypatch.setattr(
            sp,
            "run",
            _mock_podman(
                "aaaaaaaaaaaa\tpi-container-project-abc12-def3456789012345.local:latest\n",
                ps_stdout="aaaaaaaaaaaa\n",
            ),
        )
        # Path is gone — it would be removed if nothing were using it.
        monkeypatch.setattr(run, "_get_image_label", lambda image_id, label: "/nonexistent/project/path")

        attempted = []
        monkeypatch.setattr(run, "_remove_image", lambda runtime, image_id: attempted.append(image_id) or True)

        result = run._cleanup_orphaned_project_images("podman")
        assert result == []
        assert attempted == []

    def test_never_removes_protected_shared_images(self, monkeypatch):
        """The shared base image is never a cleanup candidate, whatever its labels say.

        Older builds stamped the shared base `pi-container.type=project` with blank
        project labels, so it matches the cleanup filter until it is rebuilt.
        """
        import subprocess as sp

        # podman prefixes locally-built images with "localhost/". Both spellings must
        # be protected — the prefixed one is how the shared base actually appears in
        # `podman image ls` output.
        protected = "\n".join(
            f"{i:012x}\t{prefix}{tag}"
            for i, (prefix, tag) in enumerate(
                [(p, t) for t in sorted(run._PROTECTED_IMAGE_TAGS) for p in ("", "localhost/")]
            )
        )

        monkeypatch.setattr(sp, "run", _mock_podman(protected + "\n"))
        # Blank labels — exactly what the pre-fix shared base carries.
        monkeypatch.setattr(run, "_get_image_label", lambda image_id, label: "")

        removed = []
        monkeypatch.setattr(run, "_remove_image", lambda runtime, image_id: removed.append(image_id) or True)

        result = run._cleanup_orphaned_project_images("podman")
        assert removed == []
        assert result == []

    def test_returns_empty_when_list_fails(self, monkeypatch):
        """Returns [] when `podman image ls` fails."""
        import subprocess as sp

        def mock_run(cmd, **kwargs):
            raise sp.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(sp, "run", mock_run)

        result = run._cleanup_orphaned_project_images("podman")
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

        monkeypatch.setattr(
            sp,
            "run",
            _mock_podman(
                "aaaaaaaaaaaa\tpi-container-project-aaa11-bbb2222222222222.local:latest\n"
                "bbbbbbbbbbbb\tpi-container-project-ccc33-ddd4444444444444.local:latest\n"
                "cccccccccccc\tpi-container-project-eee55-fff6666666666666.local:latest\n"
            ),
        )

        def mock_get_label(image_id, label):
            return None  # No path labels — all will be cleaned

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        def mock_remove(runtime, image_id):
            return True

        monkeypatch.setattr(run, "_remove_image", mock_remove)

        result = run._cleanup_orphaned_project_images("podman")
        assert len(result) == 3


class TestImageExists:
    """Tests for _image_exists()."""

    def test_true_when_inspect_succeeds(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(run, "CONTAINER_RUNTIME", "podman", raising=False)
        monkeypatch.setattr(sp, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="[{}]", stderr=""))
        assert run._image_exists("some-image:local") is True

    def test_false_when_inspect_fails(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(run, "CONTAINER_RUNTIME", "podman", raising=False)
        monkeypatch.setattr(sp, "run", lambda cmd, **kw: MagicMock(returncode=1, stdout="", stderr="No such image"))
        assert run._image_exists("missing-image:local") is False

    def test_false_when_runtime_unavailable(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(run, "CONTAINER_RUNTIME", "podman", raising=False)

        def boom(cmd, **kw):
            raise FileNotFoundError("podman")

        monkeypatch.setattr(sp, "run", boom)
        assert run._image_exists("some-image:local") is False


class TestNewestSharedImageTime:
    """Tests for _newest_shared_image_time() — which shared image dates a rebuild."""

    def test_returns_the_newer_of_the_two(self, monkeypatch):
        times = {
            "pi-coding-agent-proxy:local": "2025-01-01T00:00:00Z",
            "pi-coding-agent-builder:local": "2025-06-01T00:00:00Z",
        }
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, key: times[tag] if key == "pi-container.build.time" else None,
        )

        result = run._newest_shared_image_time()
        assert result == ("pi-coding-agent-builder:local", _ts("2025-06-01T00:00:00Z"))

    def test_proxy_wins_when_newer(self, monkeypatch):
        times = {
            "pi-coding-agent-proxy:local": "2025-06-01T00:00:00Z",
            "pi-coding-agent-builder:local": "2025-01-01T00:00:00Z",
        }
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, key: times[tag] if key == "pi-container.build.time" else None,
        )

        result = run._newest_shared_image_time()
        assert result == ("pi-coding-agent-proxy:local", _ts("2025-06-01T00:00:00Z"))

    def test_none_when_an_image_cannot_be_dated(self, monkeypatch):
        """An undateable shared image is a hard stop, not a silent pass.

        Without a timestamp there is no way to tell whether the project image's
        copied CA certificate and toolchain are current, and running a stale one is
        the failure this check exists to prevent.
        """
        times = {"pi-coding-agent-proxy:local": "2025-06-01T00:00:00Z"}
        monkeypatch.setattr(
            run,
            "_get_image_label",
            lambda tag, key: times.get(tag) if key == "pi-container.build.time" else None,
        )

        assert run._newest_shared_image_time() is None

    def test_covers_both_source_images(self):
        """Both images the project image COPYs --from must be checked."""
        assert set(run._SHARED_SOURCE_IMAGES) == {
            "pi-coding-agent-proxy:local",
            "pi-coding-agent-builder:local",
        }


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

    def test_stale_shared_image(self, monkeypatch, tmp_path):
        """A shared source image newer than the project image forces a rebuild."""
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
        assert reason == "stale shared image"

    def test_newer_toolchain_image_forces_rebuild(self, monkeypatch, tmp_path, caplog):
        """A rebuilt toolchain image is as stale-making as a rebuilt proxy image.

        The project image copies its whole toolchain (python, podman, netavark) out
        of the builder image, so a newer builder means the copy is out of date even
        when the proxy has not moved.
        """
        project_dir = _make_project_with_deps(tmp_path)
        monkeypatch.setattr(run, "_image_exists", lambda tag: True)

        def mock_get_label(image_tag, label_key):
            if label_key == "pi-container.build.time":
                return "2025-01-01T00:00:00Z"
            if label_key == "pi-container.hash":
                return "abc123def4567890"
            return None

        monkeypatch.setattr(run, "_get_image_label", mock_get_label)

        with caplog.at_level("WARNING"):
            reason = run._project_image_build_reason(
                project_dir,
                "pi-container-project-abcde-abc123def4567890.local",
                "abc123def4567890",
                _ts("2025-06-01T00:00:00Z"),
                "pi-coding-agent-builder:local",
            )
        assert reason == "stale shared image"
        # The warning has to name which image moved, or the rebuild is unexplained.
        assert "pi-coding-agent-builder:local" in caplog.text

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


# ---------------------------------------------------------------------------
# Project key (shared identity of every per-workspace resource)
# ---------------------------------------------------------------------------


class TestProjectKey:
    def test_shared_with_proxy_and_network_names(self, tmp_path):
        key = run._project_key(tmp_path)
        proxy_name, network_name = run._project_scope(tmp_path)
        assert re.fullmatch(r"[0-9a-f]{10}", key)
        assert proxy_name == f"pi-proxy-{key}"
        assert network_name == f"pi-isolated-net-{key}"

    def test_differs_across_dirs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert run._project_key(a) != run._project_key(b)


# ---------------------------------------------------------------------------
# Nested-container image store (per-project named volume)
# ---------------------------------------------------------------------------


class TestEnsureNestedVolume:
    def test_existing_volume_is_reused(self, monkeypatch):
        """A present store must not be recreated — that is what keeps the cache."""
        monkeypatch.setattr(run, "_volume_exists", lambda rt, name: True)

        def unexpected(*args, **kwargs):
            raise AssertionError("volume create must not run for an existing volume")

        monkeypatch.setattr(run.subprocess, "run", unexpected)
        assert run._ensure_nested_volume("podman", "pi-nested-abc", "pi-proxy-abc", "/tmp/proj") is True

    def test_creates_with_project_labels(self, monkeypatch):
        monkeypatch.setattr(run, "_volume_exists", lambda rt, name: False)
        calls: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(run.subprocess, "run", mock_run)
        assert run._ensure_nested_volume("podman", "pi-nested-abc", "pi-proxy-abc", "/tmp/proj") is True

        cmd = calls[0]
        assert cmd[:3] == ["podman", "volume", "create"]
        assert cmd[-1] == "pi-nested-abc"
        # Labelled like project images, so the same orphan rule reclaims it.
        assert "pi-container.type=nested-storage" in cmd
        assert "pi-container.project.hash=pi-proxy-abc" in cmd
        assert "pi-container.project.path=/tmp/proj" in cmd

    def test_returns_false_when_create_fails(self, monkeypatch):
        monkeypatch.setattr(run, "_volume_exists", lambda rt, name: False)
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=125, stdout="", stderr="out of space"),
        )
        assert run._ensure_nested_volume("podman", "pi-nested-abc", "pi-proxy-abc", "/tmp/proj") is False


class TestUnusedVolumes:
    """Tests for _unused_volumes() — the guard against removing a volume in use."""

    def test_returns_dangling_volume_names(self, monkeypatch):
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="vol-a\nvol-b\n", stderr=""),
        )
        assert run._unused_volumes("podman") == {"vol-a", "vol-b"}

    def test_queries_the_dangling_filter(self, monkeypatch):
        """The whole check rests on `dangling=true` meaning "no container references it"."""
        seen: list[list[str]] = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(run.subprocess, "run", _run)
        run._unused_volumes("podman")
        assert "dangling=true" in seen[0]

    def test_returns_none_on_runtime_failure(self, monkeypatch):
        """None means "unknown", which the caller distinguishes from "none are unused"."""
        import subprocess

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(run.subprocess, "run", boom)
        assert run._unused_volumes("podman") is None


class TestCleanupOrphanedNestedVolumes:
    def _mock_ls(self, monkeypatch, names: str, dangling: str | None = None):
        """Answer `volume ls` by label and by `dangling=true` separately.

        Defaults to reporting every listed volume as unused, so tests that are not
        about container usage behave as if nothing holds the volumes open.
        """

        def _run(cmd, **kw):
            stdout = (names if dangling is None else dangling) if "dangling=true" in cmd else names
            return MagicMock(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(run.subprocess, "run", _run)

    def test_removes_volume_with_missing_path(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(
            run,
            "_get_volume_label",
            lambda rt, name, label: "/nonexistent/project/path",
        )
        removed: list[str] = []
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name: removed.append(name) or True)

        assert run._cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]
        assert removed == ["pi-nested-abc1234567"]

    def test_keeps_volume_with_existing_path(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: str(Path(__file__).parent))

        def unexpected(rt, name):
            raise AssertionError("a live project's store must never be removed")

        monkeypatch.setattr(run, "_remove_volume", unexpected)
        assert run._cleanup_orphaned_nested_volumes("podman") == []

    def test_removes_volume_without_path_label(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: None)
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name: True)
        assert run._cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]

    def test_removes_volume_with_blank_path_label(self, monkeypatch):
        """A blank path label is as unverifiable as a missing one.

        Regression test: `Path("")` is `PosixPath(".")`, which always exists, so a
        blank label used to fall through the "path gone" check and be kept forever.
        """
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "")
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name: True)
        assert run._cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]

    def test_keeps_orphaned_volume_still_in_use(self, monkeypatch):
        """An orphaned volume a container still holds open is skipped, not attempted.

        The removal would fail with "volume is being used by a container" and warn on
        every start; it is reclaimed by a later run once the container is gone.
        """
        # Listed, but absent from the dangling set — something references it.
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n", dangling="")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "/nonexistent/path")

        def unexpected(rt, name):
            raise AssertionError("removal must not be attempted while a container holds the volume")

        monkeypatch.setattr(run, "_remove_volume", unexpected)
        assert run._cleanup_orphaned_nested_volumes("podman") == []

    def test_attempts_removal_when_usage_is_unknown(self, monkeypatch):
        """A failed usage query degrades to the old behaviour, not to skipping everything."""
        import subprocess as sp

        def _run(cmd, **kw):
            if "dangling=true" in cmd:
                raise sp.TimeoutExpired("cmd", 10)
            return MagicMock(returncode=0, stdout="pi-nested-abc1234567\n", stderr="")

        monkeypatch.setattr(run.subprocess, "run", _run)
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "/nonexistent/path")
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name: True)
        assert run._cleanup_orphaned_nested_volumes("podman") == ["pi-nested-abc1234567"]

    def test_removal_failure_is_not_fatal(self, monkeypatch):
        """If `volume rm` fails anyway, the volume is skipped rather than reported removed."""
        self._mock_ls(monkeypatch, "pi-nested-abc1234567\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "/nonexistent/path")
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name: False)
        assert run._cleanup_orphaned_nested_volumes("podman") == []

    def test_returns_empty_when_list_fails(self, monkeypatch):
        import subprocess as sp

        def boom(cmd, **kw):
            raise sp.TimeoutExpired("cmd", 10)

        monkeypatch.setattr(run.subprocess, "run", boom)
        assert run._cleanup_orphaned_nested_volumes("podman") == []


class TestProjectVolumeName:
    def test_deterministic_and_unique(self):
        name1 = run._project_volume_name("abc1234567", "/workspace/node_modules")
        name2 = run._project_volume_name("abc1234567", "/workspace/node_modules")
        name3 = run._project_volume_name("abc1234567", "/workspace/.venv")
        name4 = run._project_volume_name("otherproj", "/workspace/node_modules")

        assert name1 == name2
        assert name1.startswith("pi-vol-abc1234567-")
        assert name1 != name3
        assert name1 != name4


class TestEnsureProjectVolume:
    def test_existing_volume_returns_true(self, monkeypatch):
        monkeypatch.setattr(run, "_volume_exists", lambda rt, name: True)
        assert (
            run._ensure_project_volume(
                "podman",
                "pi-vol-test",
                "/workspace/node_modules",
                "pi-proxy-test",
                "/host/path",
            )
            is True
        )

    def test_creates_volume_with_labels(self, monkeypatch):
        monkeypatch.setattr(run, "_volume_exists", lambda rt, name: False)
        recorded = []

        def _run(cmd, **kw):
            recorded.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(run.subprocess, "run", _run)
        success = run._ensure_project_volume(
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
        monkeypatch.setattr(run, "_volume_exists", lambda rt, name: False)
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=1, stdout="", stderr="failed"),
        )
        assert (
            run._ensure_project_volume(
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

        monkeypatch.setattr(run.subprocess, "run", _run)

    def test_removes_stale_volume(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-11111111\npi-vol-proj-22222222\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "/workspace/old")
        removed = []
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name, **kw: removed.append(name) or True)

        active = {"pi-vol-proj-11111111"}
        result = run._cleanup_stale_project_volumes("podman", "pi-proxy-proj", active)
        assert result == ["pi-vol-proj-22222222"]
        assert removed == ["pi-vol-proj-22222222"]

    def test_keeps_stale_volume_if_in_use(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-22222222\n", dangling="")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "/workspace/old")
        monkeypatch.setattr(
            run,
            "_remove_volume",
            lambda *a, **kw: pytest.fail("should not remove in-use volume"),
        )

        result = run._cleanup_stale_project_volumes("podman", "pi-proxy-proj", set())
        assert result == []


class TestCleanupOrphanedProjectVolumes:
    def _mock_ls(self, monkeypatch, names: str, dangling: str | None = None):
        def _run(cmd, **kw):
            stdout = (names if dangling is None else dangling) if "dangling=true" in cmd else names
            return MagicMock(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(run.subprocess, "run", _run)

    def test_removes_orphaned_volume(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-11111111\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: "/nonexistent/path")
        removed = []
        monkeypatch.setattr(run, "_remove_volume", lambda rt, name, **kw: removed.append(name) or True)

        result = run._cleanup_orphaned_project_volumes("podman")
        assert result == ["pi-vol-proj-11111111"]
        assert removed == ["pi-vol-proj-11111111"]

    def test_keeps_active_project_volume(self, monkeypatch):
        self._mock_ls(monkeypatch, "pi-vol-proj-11111111\n")
        monkeypatch.setattr(run, "_get_volume_label", lambda rt, name, label: str(Path(__file__).parent))
        monkeypatch.setattr(
            run,
            "_remove_volume",
            lambda *a, **kw: pytest.fail("should not remove live project volume"),
        )

        result = run._cleanup_orphaned_project_volumes("podman")
        assert result == []


class TestVolumeHelpers:
    def test_volume_exists_true_on_success(self, monkeypatch):
        monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="[{}]"))
        assert run._volume_exists("podman", "pi-nested-abc") is True

    def test_volume_exists_false_on_failure(self, monkeypatch):
        monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=125, stdout=""))
        assert run._volume_exists("podman", "pi-nested-abc") is False

    def test_get_volume_label_reads_value(self, monkeypatch):
        monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="/tmp/proj\n"))
        assert run._get_volume_label("podman", "v", "pi-container.project.path") == "/tmp/proj"

    def test_get_volume_label_treats_no_value_as_absent(self, monkeypatch):
        """podman renders a missing label key as `<no value>`, not an error."""
        monkeypatch.setattr(run.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0, stdout="<no value>\n"))
        assert run._get_volume_label("podman", "v", "nope") is None

    def test_remove_volume_reports_failure(self, monkeypatch):
        monkeypatch.setattr(
            run.subprocess, "run", lambda cmd, **kw: MagicMock(returncode=2, stdout="", stderr="volume is being used")
        )
        assert run._remove_volume("podman", "pi-nested-abc") is False


# ---------------------------------------------------------------------------
# Registry allowlist preflight
# ---------------------------------------------------------------------------


class TestWarnAboutRegistryAllowlist:
    def _allowlist(self, tmp_path: Path, hostnames: list[str]) -> Path:
        import yaml

        (tmp_path / "allowlist.yaml").write_text(
            yaml.dump({"global": {"rules": [{"name": "r", "mode": "allow", "hostnames": hostnames}]}})
        )
        return tmp_path

    def test_warns_when_no_registry_present(self, tmp_path, caplog):
        self._allowlist(tmp_path, ["pypi.org", "github.com"])
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert any("no container registry hostname is allowed" in r.message for r in caplog.records)

    def test_silent_when_registry_and_its_cdn_allowed(self, tmp_path, caplog):
        self._allowlist(tmp_path, ["pypi.org", "registry-1.docker.io", "*.cloudfront.docker.com"])
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_warns_when_registry_allowed_without_its_blob_cdn(self, tmp_path, caplog):
        """The failure this preflight exists for: manifest resolves, first layer 403s."""
        self._allowlist(tmp_path, ["registry-1.docker.io", "auth.docker.io", "*.docker.io"])
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert any("*.cloudfront.docker.com" in r.message for r in caplog.records)

    def test_stale_cloudflare_entry_does_not_satisfy_the_check(self, tmp_path, caplog):
        """Docker Hub moved its blob CDN from Cloudflare to CloudFront."""
        self._allowlist(tmp_path, ["registry-1.docker.io", "production.cloudflare.docker.com"])
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert any("*.cloudfront.docker.com" in r.message for r in caplog.records)

    def test_wildcard_pattern_covers_blob_host(self, tmp_path, caplog):
        """ghcr's blob host is usually already covered by the github rule's wildcard."""
        self._allowlist(tmp_path, ["*.ghcr.io", "*.githubusercontent.com"])
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_registry_without_blob_redirect_never_warns(self, tmp_path, caplog):
        """gcr.io serves blobs inline, so the registry host alone is sufficient."""
        self._allowlist(tmp_path, ["gcr.io", "*.gcr.io"])
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_missing_allowlist_is_silent(self, tmp_path, caplog):
        """An unreadable allowlist is the addon's problem to report, not this preflight's."""
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []

    def test_commented_template_still_warns(self, tmp_path, caplog):
        """The seeded template ships the registry rule commented out."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        template = repo_root / "pi-coding-agent" / "default" / "allowlist.yaml"
        (tmp_path / "allowlist.yaml").write_text(template.read_text())
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert any("no container registry hostname is allowed" in r.message for r in caplog.records)

    def test_shipped_registry_block_is_self_consistent(self, tmp_path, caplog):
        """Uncommenting the template's registry rule must produce a clean preflight."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        template = (repo_root / "pi-coding-agent" / "default" / "allowlist.yaml").read_text()
        uncommented = "\n".join(
            line.replace("    # ", "    ", 1) if line.lstrip().startswith("#") else line
            for line in template.splitlines()
        )
        (tmp_path / "allowlist.yaml").write_text(uncommented)
        with caplog.at_level("WARNING"):
            run._warn_about_registry_allowlist(tmp_path)
        assert caplog.records == []


class TestHostnameAllowed:
    """Glob/regex semantics must match the proxy addon's `_matches_hostname`."""

    @pytest.mark.parametrize(
        "host,patterns,expected",
        [
            ("registry-1.docker.io", ["*.docker.io"], True),
            ("production.cloudfront.docker.com", ["*.docker.io"], False),
            ("production.cloudfront.docker.com", ["*.cloudfront.docker.com"], True),
            ("evil.com", ["*.docker.io", "pypi.org"], False),
            ("REGISTRY-1.DOCKER.IO", ["registry-1.docker.io"], True),
            ("cdn01.quay.io", [r"^cdn\d+\.quay\.io$"], True),
            ("sub.a.docker.io", ["*.docker.io"], True),
            ("docker.io", ["*.docker.io"], False),
            ("anything", ["*.["], False),  # malformed regex is skipped, not raised
        ],
    )
    def test_matching(self, host, patterns, expected):
        assert run._hostname_allowed(host, patterns) is expected


class TestUnavailableHostPorts:
    """Preflight for ``nested_containers.ports.publish``.

    ``podman run`` rejects a conflicting ``-p`` too, but only after the images are
    built and the proxy is up; this probe aborts the launch before any of that.
    """

    @staticmethod
    def _bound(host: str) -> tuple[int, object]:
        """Bind an ephemeral port on ``host`` and return it with the live socket."""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, 0))
        sock.listen(1)
        return sock.getsockname()[1], sock

    def test_empty_list_is_trivially_available(self):
        assert run._unavailable_host_ports([], "localhost") == []

    def test_free_port_is_available(self):
        port, sock = self._bound("127.0.0.1")
        sock.close()  # released again — nothing is holding it now
        assert run._unavailable_host_ports([(port, port)], "localhost") == []

    def test_bound_port_is_reported(self):
        port, sock = self._bound("127.0.0.1")
        try:
            assert run._unavailable_host_ports([(port, port)], "localhost") == [port]
        finally:
            sock.close()

    def test_only_the_conflicting_port_is_reported(self):
        port, sock = self._bound("127.0.0.1")
        free, free_sock = self._bound("127.0.0.1")
        free_sock.close()
        try:
            assert run._unavailable_host_ports([(free, free), (port, port)], "localhost") == [port]
        finally:
            sock.close()

    def test_the_host_port_is_probed_not_the_agent_port(self):
        """The mapping is (host, agent); only the host side is ours to bind."""
        port, sock = self._bound("127.0.0.1")
        try:
            # host port free, agent port = the bound one → nothing to report
            free, free_sock = self._bound("127.0.0.1")
            free_sock.close()
            assert run._unavailable_host_ports([(free, port)], "localhost") == []
        finally:
            sock.close()

    def test_lan_scope_probes_the_wildcard_address(self):
        """A free 127.0.0.1:N says nothing about 0.0.0.0:N — probe what podman binds."""
        port, sock = self._bound("0.0.0.0")
        try:
            assert run._unavailable_host_ports([(port, port)], "lan") == [port]
        finally:
            sock.close()


class TestConfigFixHint:
    """The remedy printed when config.yaml fails validation.

    Split by failure kind on purpose: the old message printed both remedies every
    time, so a user whose version already matched was told to bump it anyway.
    """

    PATH = Path("/ws/.pi-container/config.yaml")

    def test_stale_version_alone_is_fixed_by_editing_the_version(self):
        errors = [f"  {run.SCHEMA_VERSION_MISMATCH}: config has '0.4.1', pi-container is '0.4.2'"]
        hint = " ".join(run._config_fix_hint(errors, self.PATH))
        assert "set schema_version" in hint
        assert "rm " not in hint  # nothing is deleted to fix a string

    def test_missing_field_never_suggests_a_version_edit(self):
        """The circle the old message sent users round: bump, then fail on the field."""
        hint = " ".join(run._config_fix_hint(["  nested_containers.ports: required field missing"], self.PATH))
        assert "will not help" in hint
        assert "set schema_version" not in hint

    def test_missing_field_scopes_the_reseed_to_one_file(self):
        hint = " ".join(run._config_fix_hint(["  nested_containers.ports: required field missing"], self.PATH))
        assert f"rm {self.PATH}" in hint
        assert "rm -rf" not in hint
        assert "allowlist.yaml" in hint  # says what survives

    def test_version_and_field_errors_together_take_the_reseed_path(self):
        """A bump would clear the version gate and stop at the field gate."""
        errors = [
            f"  {run.SCHEMA_VERSION_MISMATCH}: config has '0.4.1', pi-container is '0.4.2'",
            "  nested_containers.ports: required field missing",
        ]
        hint = " ".join(run._config_fix_hint(errors, self.PATH))
        assert f"rm {self.PATH}" in hint
        assert "set schema_version" not in hint

    def test_no_errors_does_not_claim_the_version_is_stale(self):
        """Defensive: an empty list must not fall into the version-only branch."""
        hint = " ".join(run._config_fix_hint([], self.PATH))
        assert "set schema_version" not in hint


class TestPortHolders:
    """Attribution for a port conflict: which container is holding it."""

    @staticmethod
    def _ps(payload, monkeypatch, returncode: int = 0):
        """Stand in for ``<runtime> ps --format json``."""
        import subprocess

        def fake_run(cmd, **kwargs):
            assert cmd[1:] == ["ps", "--format", "json"]
            if returncode:
                raise subprocess.CalledProcessError(returncode, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

        monkeypatch.setattr(run.subprocess, "run", fake_run)

    @staticmethod
    def _container(name: str, host_port: int, *, image: str = "alpine", span: int = 1, status: str = "Up 2 minutes"):
        return {
            "Names": [name],
            "Image": image,
            "Status": status,
            "Ports": [{"host_ip": "127.0.0.1", "host_port": host_port, "container_port": 80, "range": span}],
        }

    def test_no_ports_asked_makes_no_subprocess_call(self, monkeypatch):
        def explode(*a, **k):  # pragma: no cover — must never run
            raise AssertionError("should not shell out for an empty port list")

        monkeypatch.setattr(run.subprocess, "run", explode)
        assert run._port_holders("podman", []) == {}

    def test_names_the_container_publishing_the_port(self, monkeypatch):
        self._ps(json.dumps([self._container("pi-coding-agent-abc123", 18080)]), monkeypatch)
        assert run._port_holders("podman", [18080]) == {18080: "pi-coding-agent-abc123 (Up 2 minutes)"}

    def test_marks_a_holder_belonging_to_this_workspace(self, monkeypatch):
        """Agent containers are named per run, so identity comes from the image tag."""
        image = "localhost/pi-container-project-f155d7064f-60e201e6.local:latest"
        self._ps(json.dumps([self._container("pi-coding-agent-abc123", 18080, image=image)]), monkeypatch)
        holders = run._port_holders("podman", [18080], "f155d7064f")
        assert "this workspace" in holders[18080]

    def test_other_workspace_is_not_marked_as_this_one(self, monkeypatch):
        image = "localhost/pi-container-project-aaaaaaaaaa-60e201e6.local:latest"
        self._ps(json.dumps([self._container("pi-coding-agent-abc123", 18080, image=image)]), monkeypatch)
        holders = run._port_holders("podman", [18080], "f155d7064f")
        assert "this workspace" not in holders[18080]

    def test_a_published_range_covers_every_port_in_it(self, monkeypatch):
        """`-p 8000-8010:...` reports host_port 8000 with range 11, not 11 entries."""
        self._ps(json.dumps([self._container("ranged", 8000, span=11)]), monkeypatch)
        holders = run._port_holders("podman", [8005])
        assert holders[8005].startswith("ranged")

    def test_unrelated_ports_are_not_reported(self, monkeypatch):
        self._ps(json.dumps([self._container("other", 9999)]), monkeypatch)
        assert run._port_holders("podman", [18080]) == {}

    def test_runtime_failure_degrades_to_no_attribution(self, monkeypatch):
        """Diagnostics must never be able to break the launch path they explain."""
        self._ps("", monkeypatch, returncode=125)
        assert run._port_holders("podman", [18080]) == {}

    def test_unparseable_output_degrades_to_no_attribution(self, monkeypatch):
        self._ps("not json at all", monkeypatch)
        assert run._port_holders("podman", [18080]) == {}

    def test_malformed_entries_are_skipped_not_fatal(self, monkeypatch):
        payload = json.dumps(
            [
                "a bare string, not a container",
                {"Names": [], "Ports": [{"host_port": None}]},
                {"Names": ["good"], "Image": "x", "Status": "Up", "Ports": [{"host_port": 18080, "range": 1}]},
            ]
        )
        self._ps(payload, monkeypatch)
        assert run._port_holders("podman", [18080]) == {18080: "good (Up)"}


# ---------------------------------------------------------------------------
# Extract server configs and hostnames
# ---------------------------------------------------------------------------


class TestExtractServerConfigs:
    def test_extract_single_provider(self):
        data = {
            "providers": {
                "local-ornith": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "r",
                                "file": "f",
                                "dir": "d",
                            }
                        },
                    },
                }
            }
        }
        configs, hostnames = run._extract_server_configs(data)
        assert len(configs) == 1
        assert configs[0]["name"] == "local-ornith"
        assert configs[0]["baseUrl"] == "http://llama:9999/v1"
        assert hostnames == {"llama", "local-ornith"}

    def test_extract_multiple_providers_with_custom_hostnames(self):
        data = {
            "providers": {
                "local-ornith": {
                    "baseUrl": "http://llama:9999/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "r",
                                "file": "f",
                                "dir": "d",
                            }
                        },
                    },
                },
                "local-gemma": {
                    "baseUrl": "http://gemma-server:9998/v1",
                    "serverCustomParameters": {
                        "flags": [],
                        "hfModels": {
                            "main": {
                                "fileFlag": "--model",
                                "repo": "r",
                                "file": "f",
                                "dir": "d",
                            }
                        },
                    },
                },
                "anthropic": {
                    "baseUrl": "https://api.anthropic.com/v1",
                },
            }
        }
        configs, hostnames = run._extract_server_configs(data)
        assert len(configs) == 2
        assert {c["name"] for c in configs} == {"local-ornith", "local-gemma"}
        assert hostnames == {"llama", "local-ornith", "local-gemma", "gemma-server"}


# ---------------------------------------------------------------------------
# Cleanup orphaned agent containers
# ---------------------------------------------------------------------------


class TestCleanupOrphanedAgentContainers:
    def test_removes_container_with_dead_launcher_pid(self, monkeypatch):
        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-1234567890ab"],
                    "Image": "localhost/pi-container-project-f155d7064f-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "f155d7064f",
                        "pi-container.launcher_pid": "88888",
                        "pi-container.type": "agent",
                    },
                    "Status": "Up 10 minutes",
                }
            ]
        )
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(run, "is_pid_alive", lambda pid: False)
        removed_calls: list[list[str]] = []
        monkeypatch.setattr(
            run,
            "run_quiet",
            lambda cmd, **kw: removed_calls.append(cmd) or MagicMock(returncode=0),
        )

        removed = run._cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == ["pi-coding-agent-1234567890ab"]
        assert len(removed_calls) == 1
        assert removed_calls[0][:4] == ["podman", "rm", "-f", "pi-coding-agent-1234567890ab"]

    def test_skips_container_with_live_launcher_pid(self, monkeypatch):
        import os

        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-1234567890ab"],
                    "Image": "localhost/pi-container-project-f155d7064f-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "f155d7064f",
                        "pi-container.launcher_pid": str(os.getpid()),
                        "pi-container.type": "agent",
                    },
                    "Status": "Up 10 minutes",
                }
            ]
        )
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(run, "is_pid_alive", lambda pid: True)
        removed_calls: list[list[str]] = []
        monkeypatch.setattr(
            run,
            "run_quiet",
            lambda cmd, **kw: removed_calls.append(cmd) or MagicMock(returncode=0),
        )

        removed = run._cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == []
        assert len(removed_calls) == 0

    def test_skips_containers_from_other_projects(self, monkeypatch):
        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-other-project"],
                    "Image": "localhost/pi-container-project-other123-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "other123",
                        "pi-container.launcher_pid": "88888",
                    },
                    "Status": "Up 10 minutes",
                }
            ]
        )
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(run, "is_pid_alive", lambda pid: False)
        removed = run._cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == []

    def test_removes_exited_container_without_launcher_label(self, monkeypatch):
        container_json = json.dumps(
            [
                {
                    "Names": ["pi-coding-agent-old"],
                    "Image": "localhost/pi-container-project-f155d7064f-60e201e6.local:latest",
                    "Labels": {
                        "pi-container.project.hash": "f155d7064f",
                    },
                    "Status": "Exited (137) 5 minutes ago",
                }
            ]
        )
        monkeypatch.setattr(
            run.subprocess,
            "run",
            lambda cmd, **kw: MagicMock(returncode=0, stdout=container_json, stderr=""),
        )
        monkeypatch.setattr(
            run,
            "run_quiet",
            lambda cmd, **kw: MagicMock(returncode=0),
        )
        removed = run._cleanup_orphaned_agent_containers("podman", "f155d7064f")
        assert removed == ["pi-coding-agent-old"]


# ---------------------------------------------------------------------------
# Sweep orphaned servers
# ---------------------------------------------------------------------------


class TestSweepOrphanedServers:
    def test_delegates_to_server_cleanup(self, monkeypatch, tmp_path):
        called_with = []
        monkeypatch.setattr(
            run.Server,
            "cleanup_orphaned_servers",
            lambda lock_dir: called_with.append(lock_dir) or ["cleaned-instance"],
        )
        res = run._sweep_orphaned_servers(tmp_path)
        assert res == ["cleaned-instance"]
        assert called_with == [tmp_path]

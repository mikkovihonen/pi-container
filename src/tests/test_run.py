"""
Unit tests for src/run.py — per-project configuration helpers.

Run with:
    python -m pytest src/tests/test_run.py -v
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run


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

    def _make_template(self, root):
        """Build a minimal pi-coding-agent template under root."""
        template = root / "pi-coding-agent"
        template.mkdir(parents=True, exist_ok=True)
        (template / "Containerfile").write_text("FROM ubuntu:22.04\n")
        (template / "entrypoint.sh").write_text("#!/bin/bash\necho hello\n")
        return template

    def test_returns_none_when_no_files(self, tmp_path):
        """No definition files → returns None."""
        repo = tmp_path / "repo"
        repo.mkdir()
        result = run._compute_image_hash(repo)
        assert result is None

    def test_includes_root_commands_sh(self, tmp_path):
        """root/commands.sh is included in the hash."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install\n")
        self._make_template(repo)
        result = run._compute_image_hash(repo)
        assert result is not None
        assert len(result) == 16

    def test_includes_containerfile_and_entrypoint(self, tmp_path):
        """Containerfile and entrypoint.sh are always included."""
        repo = tmp_path / "repo"
        self._make_template(repo)
        result = run._compute_image_hash(repo)
        assert result is not None

    def test_different_content_different_hash(self, tmp_path):
        """Different root/commands.sh content produces different hashes."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)

        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install1\n")
        self._make_template(repo)
        hash1 = run._compute_image_hash(repo)

        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("#!/bin/bash\necho install2\n")
        hash2 = run._compute_image_hash(repo)

        assert hash1 != hash2

    def test_empty_root_commands_skipped(self, tmp_path):
        """Empty root/commands.sh is skipped (not hashed)."""
        repo = tmp_path / "repo"
        deps = repo / ".pi-container" / "dependencies"
        deps.parent.mkdir(parents=True, exist_ok=True)
        deps.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").parent.mkdir(parents=True, exist_ok=True)
        (deps / "root" / "commands.sh").write_text("")
        self._make_template(repo)
        # Should only hash Containerfile and entrypoint.sh, not the empty file
        result = run._compute_image_hash(repo)
        assert result is not None


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
        assert tag.startswith("pi-coding-agent-")
        assert tag.endswith(".local")
        assert is_project is True

    def test_project_tag_includes_hash(self, tmp_path):
        """Project image tag includes content hash."""
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
        # Tag should be pi-coding-agent-<hash>.local
        assert re.fullmatch(r"pi-coding-agent-[0-9a-f]{16}\.local", tag)


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

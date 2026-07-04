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

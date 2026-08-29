import re
import sys
from pathlib import Path

import pytest

import project
from config_schema import SCHEMA_VERSION_MISMATCH

sys.dont_write_bytecode = True


class TestProjectScope:
    def test_stable_for_same_dir(self, tmp_path):
        assert project.project_scope(tmp_path) == project.project_scope(tmp_path)

    def test_differs_across_dirs(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert project.project_scope(a) != project.project_scope(b)

    def test_name_format(self, tmp_path):
        proxy_name, network_name = project.project_scope(tmp_path)
        assert proxy_name.startswith("pi-proxy-")
        assert network_name.startswith("pi-isolated-net-")
        # Shared 10-hex-char project key across both names.
        assert proxy_name.split("pi-proxy-")[1] == network_name.split("pi-isolated-net-")[1]
        assert re.fullmatch(r"[0-9a-f]{10}", proxy_name.split("pi-proxy-")[1])


class TestProjectKey:
    def test_shared_with_proxy_and_network_names(self, tmp_path):
        key = project.project_key(tmp_path)
        proxy_name, network_name = project.project_scope(tmp_path)
        assert re.fullmatch(r"[0-9a-f]{10}", key)
        assert proxy_name == f"pi-proxy-{key}"
        assert network_name == f"pi-isolated-net-{key}"

    def test_differs_across_dirs(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert project.project_key(a) != project.project_key(b)


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
        proj = tmp_path / "project"
        proj.mkdir()
        self._make_template(repo)
        monkeypatch.setattr(project, "REPO_ROOT", repo)
        monkeypatch.setattr(project, "PROJECT_DIR", proj)

        agent_dir = project.ensure_project_config()

        assert agent_dir == proj / ".pi-container" / "agent"
        assert (agent_dir / "models.json").exists()
        assert (proj / ".pi-container" / "chat-templates" / "Some-Model" / "chat_template.jinja").exists()
        for name in ("config.yaml", "allowlist.yaml", "token_replacer.yaml"):
            assert (proj / ".pi-container" / name).exists()

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        proj = tmp_path / "project"
        self._make_template(repo)
        # Pre-existing, user-edited allowlist must be preserved.
        existing = proj / ".pi-container" / "allowlist.yaml"
        existing.parent.mkdir(parents=True)
        existing.write_text("global: {custom: true}\n")
        monkeypatch.setattr(project, "REPO_ROOT", repo)
        monkeypatch.setattr(project, "PROJECT_DIR", proj)

        project.ensure_project_config()

        assert existing.read_text() == "global: {custom: true}\n"
        # Missing ones are still seeded.
        assert (proj / ".pi-container" / "token_replacer.yaml").exists()
        assert (proj / ".pi-container" / "agent" / "models.json").exists()

    def test_seeds_entrypoint_sh_when_absent(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        proj = tmp_path / "project"
        proj.mkdir()
        self._make_template(repo)
        monkeypatch.setattr(project, "REPO_ROOT", repo)
        monkeypatch.setattr(project, "PROJECT_DIR", proj)

        agent_dir = project.ensure_project_config()

        ep_dst = agent_dir / "entrypoint.sh"
        assert ep_dst.exists()
        assert ep_dst.read_text() == "#!/bin/bash\n"

    def test_does_not_overwrite_existing_entrypoint_sh(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        proj = tmp_path / "project"
        self._make_template(repo)
        # Pre-existing, user-edited entrypoint must be preserved.
        custom_ep = proj / ".pi-container" / "agent" / "entrypoint.sh"
        custom_ep.parent.mkdir(parents=True)
        custom_ep.write_text("#!/bin/bash\necho 'custom setup'\n")
        monkeypatch.setattr(project, "REPO_ROOT", repo)
        monkeypatch.setattr(project, "PROJECT_DIR", proj)

        project.ensure_project_config()

        assert custom_ep.read_text() == "#!/bin/bash\necho 'custom setup'\n"

    def test_skips_entrypoint_sh_when_template_missing(self, tmp_path, monkeypatch):
        """If the template has no entrypoint.sh, seeding must not fail."""
        repo = tmp_path / "repo"
        proj = tmp_path / "project"
        proj.mkdir()
        self._make_template(repo, with_entrypoint=False)
        monkeypatch.setattr(project, "REPO_ROOT", repo)
        monkeypatch.setattr(project, "PROJECT_DIR", proj)

        agent_dir = project.ensure_project_config()
        assert not (agent_dir / "entrypoint.sh").exists()


class TestEnsureProjectConfigMissingTemplate:
    def test_raises_when_template_missing(self, tmp_path, monkeypatch):
        """ensure_project_config raises FileNotFoundError when the template dir is absent."""
        repo = tmp_path / "repo"
        proj = tmp_path / "project"
        monkeypatch.setattr(project, "REPO_ROOT", repo)
        monkeypatch.setattr(project, "PROJECT_DIR", proj)
        with pytest.raises(FileNotFoundError, match="Project config template not found"):
            project.ensure_project_config()


class TestConfigFixHint:
    """The remedy printed when config.yaml fails validation."""

    PATH = Path("/ws/.pi-container/config.yaml")

    def test_stale_version_alone_is_fixed_by_editing_the_version(self):
        errors = [f"  {SCHEMA_VERSION_MISMATCH}: config has '0.4.1', pi-container is '0.4.2'"]
        hint = " ".join(project.config_fix_hint(errors, self.PATH))
        assert "set schema_version" in hint
        assert "rm " not in hint

    def test_missing_field_never_suggests_a_version_edit(self):
        hint = " ".join(project.config_fix_hint(["  nested_containers.ports: required field missing"], self.PATH))
        assert "will not help" in hint
        assert "set schema_version" not in hint

    def test_missing_field_scopes_the_reseed_to_one_file(self):
        hint = " ".join(project.config_fix_hint(["  nested_containers.ports: required field missing"], self.PATH))
        assert f"rm {self.PATH}" in hint
        assert "rm -rf" not in hint
        assert "allowlist.yaml" in hint

    def test_version_and_field_errors_together_take_the_reseed_path(self):
        errors = [
            f"  {SCHEMA_VERSION_MISMATCH}: config has '0.4.1', pi-container is '0.4.2'",
            "  nested_containers.ports: required field missing",
        ]
        hint = " ".join(project.config_fix_hint(errors, self.PATH))
        assert f"rm {self.PATH}" in hint
        assert "set schema_version" not in hint

    def test_no_errors_does_not_claim_the_version_is_stale(self):
        hint = " ".join(project.config_fix_hint([], self.PATH))
        assert "set schema_version" not in hint

"""Unit tests for src/run.py — entrypoint validation and initialization."""

import sys
from unittest.mock import MagicMock

import run

sys.dont_write_bytecode = True


class TestInitRuntime:
    def test_exits_on_default_admin_password(self, monkeypatch):
        monkeypatch.setattr(run, "ADMIN_PASSWORD", "CHANGEME")
        exited = []
        monkeypatch.setattr(run.sys, "exit", lambda code: exited.append(code))
        run._init_runtime()
        assert exited == [1]

    def test_exits_on_empty_admin_password(self, monkeypatch):
        monkeypatch.setattr(run, "ADMIN_PASSWORD", "")
        exited = []
        monkeypatch.setattr(run.sys, "exit", lambda code: exited.append(code))
        run._init_runtime()
        assert exited == [1]

    def test_exits_on_environment_error(self, monkeypatch):
        monkeypatch.setattr(run, "ADMIN_PASSWORD", "securepassword123")
        exited = []
        monkeypatch.setattr(run.sys, "exit", lambda code: exited.append(code))

        def mock_validate(bin_path):
            raise run.EnvironmentError("Podman not found")

        monkeypatch.setattr(run, "validate_environment", mock_validate)
        run._init_runtime()
        assert exited == [1]

    def test_initializes_runtime_on_valid_environment(self, monkeypatch):
        monkeypatch.setattr(run, "ADMIN_PASSWORD", "securepassword123")
        monkeypatch.setattr(run, "validate_environment", lambda bin_path: "podman")

        mock_rt = MagicMock()
        mock_rt.bridge_interface = "cni-podman0"
        mock_rt.upstream_network = "podman"
        monkeypatch.setattr(run.ContainerRuntime, "create", lambda *a, **kw: mock_rt)

        run._init_runtime()
        assert run.CONTAINER_RUNTIME == "podman"
        assert run.BRIDGE_INTERFACE == "cni-podman0"
        assert run.PROXY_UPSTREAM_NETWORK == "podman"


class TestAgentLaunchReadOnlyPiContainerMount:
    def test_read_only_pi_container_mount_flag_included_when_enabled(self):
        pi_container_dir = run.PROJECT_DIR / ".pi-container"
        read_only_pi_container = True
        flags = ["--volume", f"{pi_container_dir}:/workspace/.pi-container:ro"] if read_only_pi_container else []
        assert flags == ["--volume", f"{pi_container_dir}:/workspace/.pi-container:ro"]

    def test_read_only_pi_container_mount_flag_omitted_when_disabled(self):
        pi_container_dir = run.PROJECT_DIR / ".pi-container"
        read_only_pi_container = False
        flags = ["--volume", f"{pi_container_dir}:/workspace/.pi-container:ro"] if read_only_pi_container else []
        assert flags == []


class TestAgentLaunchReadOnlyGitHooksMount:
    def test_read_only_git_hooks_flag_included(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        hooks_dir = git_dir / "hooks"
        read_only_git_hooks = True
        flags = []
        if read_only_git_hooks and git_dir.is_dir():
            hooks_dir.mkdir(parents=True, exist_ok=True)
            flags = ["--volume", f"{hooks_dir}:/workspace/.git/hooks:ro"]
        assert flags == ["--volume", f"{hooks_dir}:/workspace/.git/hooks:ro"]
        assert hooks_dir.is_dir()


class TestAgentLaunchSecurityOpts:
    def test_no_new_privileges_included_when_nested_disabled(self):
        nested_enabled = False
        opts = ["--security-opt", "no-new-privileges"] if not nested_enabled else []
        assert opts == ["--security-opt", "no-new-privileges"]

    def test_no_new_privileges_omitted_when_nested_enabled(self):
        nested_enabled = True
        opts = ["--security-opt", "no-new-privileges"] if not nested_enabled else []
        assert opts == []


class TestVolumeMountOptions:
    def test_shadow_volumes_have_nodev_nosuid(self):
        vol_name = "pi-vol-12345-venv"
        dest_path = "/workspace/.venv"
        arg = ("--volume", f"{vol_name}:{dest_path}:nodev,nosuid")
        assert arg == ("--volume", "pi-vol-12345-venv:/workspace/.venv:nodev,nosuid")

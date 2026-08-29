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

"""Unit tests for src/run.py — entrypoint validation and initialization."""

import os
import sys
from unittest.mock import MagicMock, patch

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


class TestAgentLaunchGitFlags:
    def test_git_safe_directory_and_rewrite_flags_enabled(self):
        git_cfg = {"safe_directory": True, "rewrite_ssh_to_https": True}
        flags = [
            "--env",
            f"GIT_SAFE_DIRECTORY={'true' if git_cfg['safe_directory'] else 'false'}",
            "--env",
            f"GIT_REWRITE_SSH={'true' if git_cfg['rewrite_ssh_to_https'] else 'false'}",
        ]
        assert flags == [
            "--env",
            "GIT_SAFE_DIRECTORY=true",
            "--env",
            "GIT_REWRITE_SSH=true",
        ]

    def test_git_safe_directory_and_rewrite_flags_disabled(self):
        git_cfg = {"safe_directory": False, "rewrite_ssh_to_https": False}
        flags = [
            "--env",
            f"GIT_SAFE_DIRECTORY={'true' if git_cfg['safe_directory'] else 'false'}",
            "--env",
            f"GIT_REWRITE_SSH={'true' if git_cfg['rewrite_ssh_to_https'] else 'false'}",
        ]
        assert flags == [
            "--env",
            "GIT_SAFE_DIRECTORY=false",
            "--env",
            "GIT_REWRITE_SSH=false",
        ]


class TestAgentLaunchSourceControlFlags:
    def test_setup_git_credentials_flag(self):
        sc_cfg = {"setup_git_credentials": True}
        flag = f"SETUP_GIT_CREDENTIALS={'true' if sc_cfg['setup_git_credentials'] else 'false'}"
        assert flag == "SETUP_GIT_CREDENTIALS=true"

    def test_token_env_forwarding_and_override(self):
        sc_tokens = {"GH_TOKEN": "gho_auto", "GITHUB_TOKEN": "gho_auto"}
        agent_env = {"GH_TOKEN": "gho_user_override", "OTHER_VAR": "val"}
        merged = {**sc_tokens, **agent_env}
        assert merged["GH_TOKEN"] == "gho_user_override"
        assert merged["GITHUB_TOKEN"] == "gho_auto"
        assert merged["OTHER_VAR"] == "val"


class TestVolumeMountOptions:
    def test_shadow_volumes_have_nodev_nosuid(self):
        vol_name = "pi-vol-12345-venv"
        dest_path = "/workspace/.venv"
        arg = ("--volume", f"{vol_name}:{dest_path}:nodev,nosuid")
        assert arg == ("--volume", "pi-vol-12345-venv:/workspace/.venv:nodev,nosuid")


class TestProcessGatewayConfigs:
    def test_api_key_missing_exits(self, tmp_path, monkeypatch):
        from registry import GatewayAuth, GatewayDef

        gw_def = GatewayDef(
            id="opper",
            base_url="https://api.opper.ai/v3/compat",
            auth=GatewayAuth(type="api-key", env_var="MISSING_OPPER_KEY"),
        )
        gateways = [{"name": "opper", "gatewayDef": gw_def, "baseUrl": gw_def.base_url}]

        exited = []
        monkeypatch.setattr(run.sys, "exit", lambda code: exited.append(code))
        with monkeypatch.context() as m:
            m.delenv("MISSING_OPPER_KEY", raising=False)
            with patch("keyring.get_password", return_value=None):
                run.process_gateway_configs(gateways, tmp_path)

        assert exited == [1]

    def test_api_key_found_sets_env(self, tmp_path, monkeypatch):
        from registry import GatewayAuth, GatewayDef

        gw_def = GatewayDef(
            id="opper",
            base_url="https://api.opper.ai/v3/compat",
            auth=GatewayAuth(type="api-key", env_var="TEST_OPPER_KEY"),
        )
        gateways = [{"name": "opper", "gatewayDef": gw_def, "baseUrl": gw_def.base_url}]

        monkeypatch.delenv("TEST_OPPER_KEY", raising=False)

        with patch("keyring.get_password", return_value="opp_secret_abc"):
            run.process_gateway_configs(gateways, tmp_path)

        assert run.os.environ.get("TEST_OPPER_KEY") == "opp_secret_abc"

    def test_skips_when_no_auth(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        gw_def = SimpleNamespace(auth=None)
        monkeypatch.setattr(run.containers, "warn_about_gateway_allowlist", lambda *a, **k: None)
        run.process_gateway_configs([{"name": "test-gw", "gatewayDef": gw_def}], tmp_path)

    def test_openid_auth_sets_env(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        auth = SimpleNamespace(type="openid", env_var=None)
        gw_def = SimpleNamespace(auth=auth)
        monkeypatch.setattr("auth.ensure_gateway_auth", lambda name, a: "mock-token-123")
        monkeypatch.setattr(run.containers, "warn_about_gateway_allowlist", lambda *a, **k: None)

        run.process_gateway_configs([{"name": "test-gw", "gatewayDef": gw_def}], tmp_path)
        assert os.environ.get("TEST_GW_TOKEN") == "mock-token-123"
        os.environ.pop("TEST_GW_TOKEN", None)


class TestEnsureProxyImage:
    def test_rebuilds_when_not_current(self, monkeypatch):
        built = []
        monkeypatch.setattr(run.images, "proxy_image_is_current", lambda **kw: False)
        monkeypatch.setattr(run, "build_proxy", lambda rt: built.append(rt))

        result = run.ensure_proxy_image("podman")
        assert result is True
        assert built == ["podman"]

    def test_skips_when_current(self, monkeypatch):
        built = []
        monkeypatch.setattr(run.images, "proxy_image_is_current", lambda **kw: True)
        monkeypatch.setattr(run, "build_proxy", lambda rt: built.append(rt))

        result = run.ensure_proxy_image("podman")
        assert result is False
        assert built == []


class TestT3LaunchConfiguration:
    def test_t3_flag_enables_mode_and_default_port(self):
        argv = ["run.py", "--t3"]
        cli_t3 = "--t3" in argv
        container_argv = [arg for arg in argv[1:] if arg != "--t3"]
        assert cli_t3 is True
        assert container_argv == []

        agent_ports = {"expose": "localhost", "publish": []}
        claimed_agent_ports = {agent for _, agent in agent_ports["publish"]}
        if 3773 not in claimed_agent_ports:
            agent_ports["publish"].append((3773, 3773))
        assert agent_ports["publish"] == [(3773, 3773)]

    def test_t3_preserves_custom_port(self):
        agent_ports = {"expose": "localhost", "publish": [(13773, 3773)]}
        claimed_agent_ports = {agent for _, agent in agent_ports["publish"]}
        if 3773 not in claimed_agent_ports:
            agent_ports["publish"].append((3773, 3773))
        assert agent_ports["publish"] == [(13773, 3773)]

    def test_t3_advertise_host_cli_parsing(self):
        # Two-arg form
        raw_args = ["--t3", "--t3-advertise-host", "192.168.1.50", "extra_arg"]
        cli_t3 = False
        cli_t3_advertise_host = None
        container_argv = []
        i = 0
        while i < len(raw_args):
            arg = raw_args[i]
            if arg == "--t3":
                cli_t3 = True
                i += 1
            elif arg == "--t3-advertise-host" and i + 1 < len(raw_args):
                cli_t3_advertise_host = raw_args[i + 1]
                i += 2
            else:
                container_argv.append(arg)
                i += 1

        assert cli_t3 is True
        assert cli_t3_advertise_host == "192.168.1.50"
        assert container_argv == ["extra_arg"]

        # Equals form
        raw_args_eq = ["--t3-advertise-host=custom.domain"]
        cli_t3_advertise_host_eq = None
        container_argv_eq = []
        i = 0
        while i < len(raw_args_eq):
            arg = raw_args_eq[i]
            if arg.startswith("--t3-advertise-host="):
                cli_t3_advertise_host_eq = arg.split("=", 1)[1]
                i += 1
            else:
                container_argv_eq.append(arg)
                i += 1

        assert cli_t3_advertise_host_eq == "custom.domain"
        assert container_argv_eq == []

    def test_t3_policy_content(self):
        import json

        policy_file = run.REPO_ROOT / "pi-coding-agent" / "t3-policy.json"
        assert policy_file.is_file()
        data = json.loads(policy_file.read_text())
        assert data.get("schemaVersion") == 1
        assert data.get("limits", {}).get("providers", {}).get("allowedDrivers") == ["pi"]

    def test_t3_settings_seeding_structure(self):
        settings = {}
        settings["providerInstances"] = settings.get("providerInstances", {})
        settings["providerInstances"]["pi"] = {"driver": "pi", "enabled": True}
        settings["providerInstances"]["codex"] = {"driver": "codex", "enabled": False}
        settings["providerInstances"]["claudeAgent"] = {"driver": "claudeAgent", "enabled": False}
        settings["textGenerationModelSelection"] = {"instanceId": "pi", "model": "pi"}

        assert settings["providerInstances"]["pi"]["enabled"] is True
        assert settings["providerInstances"]["codex"]["enabled"] is False
        assert settings["providerInstances"]["claudeAgent"]["enabled"] is False
        assert settings["textGenerationModelSelection"]["instanceId"] == "pi"

    def test_t3_seed_settings_file(self):
        script_file = run.REPO_ROOT / "pi-coding-agent" / "t3-seed-settings.cjs"
        assert script_file.is_file()
        content = script_file.read_text()
        assert "providerInstances.pi" in content
        assert 'driver: "pi", enabled: true' in content

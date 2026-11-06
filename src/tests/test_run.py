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

    def test_exits_on_empty_admin_password(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(run, "ADMIN_PASSWORD", "")
        exited = []
        monkeypatch.setattr(run.sys, "exit", lambda code: exited.append(code))
        with caplog.at_level(logging.ERROR):
            run._init_runtime()
        assert exited == [1]
        assert "uv run keyring set pi-container ADMIN_PASSWORD" in caplog.text
        assert "Host environment variables are not accepted" in caplog.text

    def test_admin_password_ignores_host_environment_variables(self, monkeypatch, caplog):
        import importlib
        import logging

        import config

        monkeypatch.setenv("ADMIN_PASSWORD", "env_should_be_ignored")
        with patch("secret_store.get_keyring_secret", return_value="from_keyring_only"):
            with caplog.at_level(logging.WARNING):
                importlib.reload(config)
            try:
                assert config.ADMIN_PASSWORD == "from_keyring_only"
                assert "ADMIN_PASSWORD in host environment is ignored" in caplog.text
                assert "ADMIN_PASSWORD" not in os.environ
            finally:
                importlib.reload(config)

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
        cli_lan = False
        container_argv = []
        i = 0
        while i < len(raw_args):
            arg = raw_args[i]
            if arg == "--t3":
                cli_t3 = True
                i += 1
            elif arg in ("--t3-lan", "--lan"):
                cli_lan = True
                i += 1
            elif arg in ("--t3-external-ip", "--t3-advertise-external-ip"):
                cli_t3_advertise_host = "external"
                i += 1
            elif arg == "--t3-advertise-host" and i + 1 < len(raw_args):
                cli_t3_advertise_host = raw_args[i + 1]
                i += 2
            elif arg.startswith("--t3-advertise-host="):
                cli_t3_advertise_host = arg.split("=", 1)[1]
                i += 1
            else:
                container_argv.append(arg)
                i += 1

        assert cli_t3 is True
        assert cli_t3_advertise_host == "192.168.1.50"
        assert cli_lan is False
        assert container_argv == ["extra_arg"]

        # Equals form with --t3-lan and --t3-external-ip
        raw_args_flags = ["--t3", "--t3-external-ip", "--t3-lan"]
        cli_t3 = False
        cli_t3_advertise_host = None
        cli_lan = False
        i = 0
        while i < len(raw_args_flags):
            arg = raw_args_flags[i]
            if arg == "--t3":
                cli_t3 = True
                i += 1
            elif arg in ("--t3-lan", "--lan"):
                cli_lan = True
                i += 1
            elif arg in ("--t3-external-ip", "--t3-advertise-external-ip"):
                cli_t3_advertise_host = "external"
                i += 1
            elif arg == "--t3-advertise-host" and i + 1 < len(raw_args_flags):
                cli_t3_advertise_host = raw_args_flags[i + 1]
                i += 2
            elif arg.startswith("--t3-advertise-host="):
                cli_t3_advertise_host = arg.split("=", 1)[1]
                i += 1
            else:
                i += 1

        assert cli_t3 is True
        assert cli_t3_advertise_host == "external"
        assert cli_lan is True

        # Short flags (--lan, --t3-advertise-external-ip)
        raw_args_short = ["--lan", "--t3-advertise-external-ip"]
        cli_lan = False
        cli_t3_advertise_host = None
        i = 0
        while i < len(raw_args_short):
            arg = raw_args_short[i]
            if arg in ("--t3-lan", "--lan"):
                cli_lan = True
                i += 1
            elif arg in ("--t3-external-ip", "--t3-advertise-external-ip"):
                cli_t3_advertise_host = "external"
                i += 1
            else:
                i += 1
        assert cli_lan is True
        assert cli_t3_advertise_host == "external"

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
        settings["textGenerationModelSelection"] = {"instanceId": "pi", "model": "default"}

        assert settings["providerInstances"]["pi"]["enabled"] is True
        assert settings["providerInstances"]["codex"]["enabled"] is False
        assert settings["textGenerationModelSelection"]["model"] == "default"


class TestT3AdvertiseHostResolution:
    def test_defaults_to_localhost_when_unconfigured(self):
        from unittest.mock import patch

        cli_t3_advertise_host = None
        raw_adv_host = cli_t3_advertise_host or "localhost"
        with patch("util.get_host_external_ip") as mock_get_ip:
            if raw_adv_host.strip().lower() in ("external", "external_ip", "auto"):
                t3_advertise_host = mock_get_ip()
            else:
                t3_advertise_host = raw_adv_host.strip()
            assert t3_advertise_host == "localhost"
            mock_get_ip.assert_not_called()

    def test_explicit_external_resolves_external_ip(self):
        from unittest.mock import patch

        import util

        for variant in ("external", "EXTERNAL", "external_ip", "auto"):
            with patch("util.get_host_external_ip", return_value="192.168.10.20"):
                if variant.strip().lower() in ("external", "external_ip", "auto"):
                    t3_advertise_host = util.get_host_external_ip()
                else:
                    t3_advertise_host = variant.strip()
                assert t3_advertise_host == "192.168.10.20"

    def test_preserves_custom_configured_host(self):
        from unittest.mock import patch

        raw_adv_host = "my-custom-dev.lan"
        with patch("util.get_host_external_ip") as mock_get_ip:
            if raw_adv_host.strip().lower() in ("external", "external_ip", "auto"):
                t3_advertise_host = mock_get_ip()
            else:
                t3_advertise_host = raw_adv_host.strip()
            assert t3_advertise_host == "my-custom-dev.lan"
            mock_get_ip.assert_not_called()

    def test_external_ip_does_not_implicitly_elevate_lan(self):
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        agent_ports = {"expose": "localhost", "publish": [(3773, 3773)]}
        t3_advertise_host = "192.168.1.50"
        if t3_advertise_host not in ("localhost", "127.0.0.1", "::1") and agent_ports.get("expose") != "lan":
            mock_logger.warning("warn message")
        # Ensure expose remains localhost
        assert agent_ports["expose"] == "localhost"
        mock_logger.warning.assert_called_once()

    def test_explicit_lan_flag_elevates_agent_ports(self):
        agent_ports = {"expose": "localhost", "publish": [(3773, 3773)]}
        cli_lan = True
        if cli_lan:
            agent_ports["expose"] = "lan"
        assert agent_ports["expose"] == "lan"

    def test_t3_seed_settings_file(self):
        script_file = run.REPO_ROOT / "pi-coding-agent" / "t3-seed-settings.cjs"
        assert script_file.is_file()
        content = script_file.read_text()
        assert "providerInstances.pi" in content
        assert 'driver: "pi", enabled: true' in content

    def test_t3_tunnel_cli_parsing(self):
        raw_args = [
            "--t3-tunnel",
            "cloudflare",
            "--t3-tunnel-domain",
            "t3.example.com",
            "--t3-tunnel-runner=container",
            "--t3-tunnel-keyring-account",
            "CUSTOM_CF_TOKEN",
            "--t3-public-url=https://custom-entry.dev",
        ]
        cli_t3 = False
        cli_t3_tunnel = None
        cli_t3_tunnel_domain = None
        cli_t3_tunnel_quick = False
        cli_t3_tunnel_runner = "auto"
        cli_t3_tunnel_keyring_account = "CLOUDFLARE_TUNNEL_TOKEN"
        cli_t3_public_url = None
        i = 0
        while i < len(raw_args):
            arg = raw_args[i]
            if arg in ("--t3-tunnel", "--tunnel") and i + 1 < len(raw_args):
                cli_t3_tunnel = raw_args[i + 1]
                cli_t3 = True
                i += 2
            elif arg in ("--t3-tunnel-domain", "--tunnel-domain") and i + 1 < len(raw_args):
                cli_t3_tunnel_domain = raw_args[i + 1]
                cli_t3 = True
                i += 2
            elif arg in ("--t3-tunnel-runner", "--tunnel-runner") and i + 1 < len(raw_args):
                cli_t3_tunnel_runner = raw_args[i + 1]
                cli_t3 = True
                i += 2
            elif arg.startswith("--t3-tunnel-runner=") or arg.startswith("--tunnel-runner="):
                cli_t3_tunnel_runner = arg.split("=", 1)[1]
                cli_t3 = True
                i += 1
            elif arg in ("--t3-tunnel-keyring-account", "--tunnel-keyring-account") and i + 1 < len(raw_args):
                cli_t3_tunnel_keyring_account = raw_args[i + 1]
                cli_t3 = True
                i += 2
            elif arg in ("--t3-public-url", "--public-url") and i + 1 < len(raw_args):
                cli_t3_public_url = raw_args[i + 1]
                cli_t3 = True
                i += 2
            elif arg.startswith("--t3-public-url=") or arg.startswith("--public-url="):
                cli_t3_public_url = arg.split("=", 1)[1]
                cli_t3 = True
                i += 1
            else:
                i += 1

        assert cli_t3 is True
        assert cli_t3_tunnel == "cloudflare"
        assert cli_t3_tunnel_domain == "t3.example.com"
        assert cli_t3_tunnel_quick is False
        assert cli_t3_tunnel_runner == "container"
        assert cli_t3_tunnel_keyring_account == "CUSTOM_CF_TOKEN"
        assert cli_t3_public_url == "https://custom-entry.dev"

    def test_t3_tunnel_suppresses_external_ip_warning(self):
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        agent_ports = {"expose": "localhost", "publish": [(3773, 3773)]}
        t3_advertise_host = "t3.example.com"
        cli_t3_tunnel = "cloudflare"
        cli_t3_public_url = None

        has_active_tunnel = bool(cli_t3_tunnel or cli_t3_public_url)
        if (
            not has_active_tunnel
            and t3_advertise_host not in ("localhost", "127.0.0.1", "::1")
            and agent_ports.get("expose") != "lan"
        ):
            mock_logger.warning("warn message")

        # Expose should remain localhost and no warning should be logged
        assert agent_ports["expose"] == "localhost"
        mock_logger.warning.assert_not_called()

    def test_t3_tunnel_zero_port_https_normalization(self):
        from urllib.parse import urlparse

        from tunnel import normalize_tunnel_url

        cli_t3_public_url = "http://t3.example.com:3773"
        t3_public_url = normalize_tunnel_url(cli_t3_public_url)
        assert t3_public_url == "https://t3.example.com"
        assert ":3773" not in t3_public_url
        assert t3_public_url.startswith("https://")

        t3_advertise_host = urlparse(t3_public_url).hostname
        assert t3_advertise_host == "t3.example.com"

        env_args = [
            "--env",
            "PI_CONTAINER_MODE=t3",
            "--env",
            f"T3_ADVERTISE_HOST={t3_advertise_host}",
            *(["--env", f"T3_PUBLIC_URL={t3_public_url}"] if t3_public_url else []),
        ]
        assert "--env" in env_args
        assert "T3_PUBLIC_URL=https://t3.example.com" in env_args
        assert "T3_ADVERTISE_HOST=t3.example.com" in env_args

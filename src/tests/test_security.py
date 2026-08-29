from pathlib import Path

import yaml

from security import (
    DEFAULT_SECURITY_CONFIG,
    is_ip_in_blocked_ranges,
    read_security_config,
    validate_mount_paths,
)


class TestReadSecurityConfig:
    def _write_config(self, tmp_path: Path, data: dict):
        (tmp_path / "config.yaml").write_text(yaml.dump(data))

    def test_defaults_when_empty_or_missing(self, tmp_path: Path):
        cfg = read_security_config(tmp_path)
        assert cfg["read_only_git_hooks"] is True
        assert cfg["blocked_mount_paths"] == DEFAULT_SECURITY_CONFIG["blocked_mount_paths"]
        assert cfg["blocked_ip_ranges"] == DEFAULT_SECURITY_CONFIG["blocked_ip_ranges"]
        assert cfg["git_config_allowlist"] == DEFAULT_SECURITY_CONFIG["git_config_allowlist"]

    def test_custom_values_override_defaults(self, tmp_path: Path):
        self._write_config(
            tmp_path,
            {
                "security": {
                    "read_only_git_hooks": False,
                    "blocked_mount_paths": ["~/.custom_secret"],
                    "blocked_ip_ranges": ["10.0.0.0/8"],
                    "git_config_allowlist": ["user.name"],
                }
            },
        )
        cfg = read_security_config(tmp_path)
        assert cfg["read_only_git_hooks"] is False
        assert cfg["blocked_mount_paths"] == ["~/.custom_secret"]
        assert cfg["blocked_ip_ranges"] == ["10.0.0.0/8"]
        assert cfg["git_config_allowlist"] == ["user.name"]


class TestValidateMountPaths:
    def test_safe_mounts_pass(self, tmp_path: Path):
        safe_dir = tmp_path / "safe_dir"
        safe_dir.mkdir()
        mounts = [
            f"{safe_dir}:/home/pi/safe:ro",
            f"{tmp_path}/cache:/home/pi/cache",
        ]
        errors = validate_mount_paths(mounts, DEFAULT_SECURITY_CONFIG["blocked_mount_paths"])
        assert errors == []

    def test_blocked_mount_paths_detected(self):
        ssh_dir = str(Path("~/.ssh").expanduser())
        mounts = [
            f"{ssh_dir}:/home/pi/.ssh",
            f"{ssh_dir}/id_rsa:/home/pi/.ssh/id_rsa:ro",
            "/var/run/docker.sock:/var/run/docker.sock",
            "/etc/shadow:/home/pi/shadow:ro",
        ]
        errors = validate_mount_paths(mounts, DEFAULT_SECURITY_CONFIG["blocked_mount_paths"])
        assert len(errors) == 4
        assert any("~/.ssh" in e for e in errors)
        assert any("/var/run/docker.sock" in e for e in errors)
        assert any("/etc/shadow" in e for e in errors)


class TestIsIpInBlockedRanges:
    def test_private_and_metadata_ips_are_blocked(self):
        ranges = DEFAULT_SECURITY_CONFIG["blocked_ip_ranges"]
        assert is_ip_in_blocked_ranges("127.0.0.1", ranges)
        assert is_ip_in_blocked_ranges("10.1.2.3", ranges)
        assert is_ip_in_blocked_ranges("172.16.5.5", ranges)
        assert is_ip_in_blocked_ranges("192.168.1.1", ranges)
        assert is_ip_in_blocked_ranges("169.254.169.254", ranges)
        assert is_ip_in_blocked_ranges("::1", ranges)
        assert is_ip_in_blocked_ranges("fe80::1", ranges)

    def test_public_ips_are_not_blocked(self):
        ranges = DEFAULT_SECURITY_CONFIG["blocked_ip_ranges"]
        assert not is_ip_in_blocked_ranges("8.8.8.8", ranges)
        assert not is_ip_in_blocked_ranges("1.1.1.1", ranges)
        assert not is_ip_in_blocked_ranges("93.184.216.34", ranges)
        assert not is_ip_in_blocked_ranges("invalid_ip", ranges)

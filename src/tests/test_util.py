"""
Unit tests for src/util.py

Run with:
    python -m pytest src/tests/test_util.py -v
"""

import errno
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from util import (
    EnvironmentError,
    get_free_port,
    get_sanitized_git_config_json,
    handle_signal,
    load_dotenv,
    run_quiet,
    stop_process_group,
    validate_environment,
)

# ---------------------------------------------------------------------------
# load_dotenv
# ---------------------------------------------------------------------------


class TestLoadDotenv:
    def test_loads_from_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY_ONE=value_one\nKEY_TWO=value_two\n")
        load_dotenv(env_file)
        assert os.environ["KEY_ONE"] == "value_one"
        assert os.environ["KEY_TWO"] == "value_two"
        os.environ.pop("KEY_ONE", None)
        os.environ.pop("KEY_TWO", None)

    def test_ignores_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nKEY=value\n")
        load_dotenv(env_file)
        assert os.environ.get("KEY") == "value"
        os.environ.pop("KEY", None)

    def test_ignores_empty_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("\n\nKEY=value\n\n")
        load_dotenv(env_file)
        assert os.environ.get("KEY") == "value"
        os.environ.pop("KEY", None)

    def test_splits_on_first_equals_only(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value=with=equals\n")
        load_dotenv(env_file)
        assert os.environ["KEY"] == "value=with=equals"
        os.environ.pop("KEY", None)

    def test_strips_whitespace(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("  KEY  =  value  \n")
        load_dotenv(env_file)
        assert os.environ["KEY"] == "value"
        os.environ.pop("KEY", None)

    def test_nonexistent_file_no_op(self, tmp_path):
        nonexistent = tmp_path / "nonexistent.env"
        load_dotenv(nonexistent)  # should not raise

    def test_skips_lines_without_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("NOT_A_KEY_VALUE\nVALID_KEY=valid\n")
        load_dotenv(env_file)
        assert os.environ.get("VALID_KEY") == "valid"
        assert "NOT_A_KEY_VALUE" not in os.environ
        os.environ.pop("VALID_KEY", None)


# ---------------------------------------------------------------------------
# validate_environment
# ---------------------------------------------------------------------------


class TestValidateEnvironment:
    def test_all_dependencies_present(self):
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            # Mock llama_bin path exists
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            # Mock shutil.which
            mock_shutil.which.side_effect = lambda cmd: {
                "hf": "/usr/bin/hf",
                "socat": "/usr/bin/socat",
                "podman": "/usr/bin/podman",
            }.get(cmd)

            result = validate_environment("/usr/bin/llama-server")
            assert result == "podman"

    def test_llama_bin_not_found_raises(self):
        with patch("util.Path") as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            mock_path.return_value = mock_path_instance

            with pytest.raises(EnvironmentError, match="llama-server not found"):
                validate_environment("/nonexistent/llama-server")

    def test_llama_bin_none_raises(self):
        with pytest.raises(EnvironmentError, match="llama-server not found"):
            validate_environment(None)

    def test_hf_not_found_raises(self):
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            mock_shutil.which.side_effect = lambda cmd: {
                "socat": "/usr/bin/socat",
                "podman": "/usr/bin/podman",
            }.get(cmd)

            with pytest.raises(EnvironmentError, match="hf not found"):
                validate_environment("/usr/bin/llama-server")

    def test_socat_not_found_does_not_raise(self):
        """socat is no longer required (removed when Apple container support was dropped)."""
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            mock_shutil.which.side_effect = lambda cmd: {
                "hf": "/usr/bin/hf",
                "podman": "/usr/bin/podman",
            }.get(cmd)

            # Should succeed - socat is no longer required
            result = validate_environment("/usr/bin/llama-server")
            assert result == "podman"

    def test_no_container_runtime_raises(self):
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            mock_shutil.which.side_effect = lambda cmd: {
                "hf": "/usr/bin/hf",
                "socat": "/usr/bin/socat",
            }.get(cmd)

            with pytest.raises(EnvironmentError, match="podman not found"):
                validate_environment("/usr/bin/llama-server")

    def test_docker_alone_is_not_enough(self):
        """Docker support was removed: a host with only docker must fail loudly."""
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            mock_shutil.which.side_effect = lambda cmd: {
                "hf": "/usr/bin/hf",
                "docker": "/usr/bin/docker",
            }.get(cmd)

            with pytest.raises(EnvironmentError, match="podman not found"):
                validate_environment("/usr/bin/llama-server")

    def test_returns_podman_when_only_podman(self):
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            mock_shutil.which.side_effect = lambda cmd: {
                "hf": "/usr/bin/hf",
                "socat": "/usr/bin/socat",
                "podman": "/usr/bin/podman",
            }.get(cmd)

            assert validate_environment("/usr/bin/llama-server") == "podman"

    def test_podman_auto_detected(self):
        """Podman is auto-detected when no env var is set."""
        with patch("util.Path") as mock_path, patch("util.shutil") as mock_shutil:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path.return_value = mock_path_instance

            mock_shutil.which.side_effect = lambda cmd: {
                "hf": "/usr/bin/hf",
                "podman": "/usr/bin/podman",
            }.get(cmd)

            assert validate_environment("/usr/bin/llama-server") == "podman"


# ---------------------------------------------------------------------------
# get_free_port
# ---------------------------------------------------------------------------


class TestGetFreePort:
    def test_returns_integer(self):
        port = get_free_port()
        assert isinstance(port, int)

    def test_returns_valid_port_range(self):
        port = get_free_port()
        assert 1024 <= port <= 65535

    def test_returns_different_ports(self):
        port1 = get_free_port()
        port2 = get_free_port()
        assert port1 != port2


# ---------------------------------------------------------------------------
# handle_signal
# ---------------------------------------------------------------------------


class TestHandleSignal:
    def test_raises_system_exit(self):
        mock_logger = MagicMock()
        with pytest.raises(SystemExit) as excinfo:
            handle_signal(signal.SIGINT, mock_logger)
        assert excinfo.value.code == 128 + signal.SIGINT

    def test_log_contains_signal_name(self):
        mock_logger = MagicMock()
        with pytest.raises(SystemExit):
            handle_signal(signal.SIGTERM, mock_logger)
        mock_logger.info.assert_called_once()
        assert "SIGTERM" in str(mock_logger.info.call_args)

    def test_log_contains_clean_shutdown(self):
        mock_logger = MagicMock()
        with pytest.raises(SystemExit):
            handle_signal(signal.SIGINT, mock_logger)
        assert "clean shutdown" in str(mock_logger.info.call_args)

    def test_handles_signal_with_frame_object(self):
        """When Python dispatches a signal, it passes (signum, frame)."""
        fake_frame = object()  # not a logger
        with patch("util._LOG") as mock_log:
            with pytest.raises(SystemExit) as excinfo:
                handle_signal(signal.SIGINT, fake_frame)
            assert excinfo.value.code == 128 + signal.SIGINT
            mock_log.info.assert_called_once()
            assert "SIGINT" in str(mock_log.info.call_args)


# ---------------------------------------------------------------------------
# is_pid_alive and Client Manifest helpers
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    def test_negative_or_zero_pid(self):
        from util import is_pid_alive

        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    def test_live_pid_returns_true(self):
        from util import is_pid_alive

        assert is_pid_alive(os.getpid()) is True

    def test_dead_pid_returns_false(self):
        from util import is_pid_alive

        with patch("os.kill", side_effect=OSError(errno.ESRCH, "No such process")):
            assert is_pid_alive(999999) is False

    def test_eperm_returns_true(self):
        """EPERM means process exists but we lack permission to signal it."""
        from util import is_pid_alive

        with patch("os.kill", side_effect=OSError(errno.EPERM, "Operation not permitted")):
            assert is_pid_alive(1) is True


class TestClientManifestHelpers:
    def test_read_write_clients(self, tmp_path):
        from util import read_live_clients, write_clients

        clients_file = tmp_path / "clients.json"
        assert read_live_clients(clients_file) == []

        client = {"pid": os.getpid(), "run_id": "test1"}
        write_clients(clients_file, [client])

        live = read_live_clients(clients_file)
        assert len(live) == 1
        assert live[0]["pid"] == os.getpid()

    def test_filters_dead_clients(self, tmp_path):
        from util import read_live_clients, write_clients

        clients_file = tmp_path / "clients.json"
        dead_client = {"pid": 999999, "run_id": "dead"}
        live_client = {"pid": os.getpid(), "run_id": "live"}
        write_clients(clients_file, [dead_client, live_client])

        with patch("util.is_pid_alive", side_effect=lambda pid: pid == os.getpid()):
            live = read_live_clients(clients_file)
            assert len(live) == 1
            assert live[0]["run_id"] == "live"

    def test_register_and_deregister_client(self, tmp_path):
        from util import deregister_client, register_client

        clients_file = tmp_path / "clients.json"
        c1 = {"pid": os.getpid(), "run_id": "run1"}
        register_client(clients_file, c1)

        live = register_client(clients_file, c1)  # duplicate should not duplicate
        assert len(live) == 1

        remaining = deregister_client(clients_file, os.getpid(), "run1")
        assert remaining == []
        assert not clients_file.exists()


class TestGetRefCount:
    def test_get_ref_count_from_live_clients(self, tmp_path):
        from util import get_ref_count, write_clients

        clients_file = tmp_path / "clients.json"
        ref_count_file = tmp_path / "ref_count"
        write_clients(clients_file, [{"pid": os.getpid(), "run_id": "r1"}])
        ref_count_file.write_text("5")

        # Live clients take priority
        assert get_ref_count(clients_file, ref_count_file) == 1

    def test_get_ref_count_fallback_to_file(self, tmp_path):
        from util import get_ref_count

        clients_file = tmp_path / "clients.json"
        ref_count_file = tmp_path / "ref_count"
        ref_count_file.write_text("3")

        assert get_ref_count(clients_file, ref_count_file) == 3

    def test_get_ref_count_corrupted_or_missing_file(self, tmp_path):
        from util import get_ref_count

        clients_file = tmp_path / "clients.json"
        ref_count_file = tmp_path / "ref_count"
        assert get_ref_count(clients_file, ref_count_file) == 0

        ref_count_file.write_text("not_a_number")
        assert get_ref_count(clients_file, ref_count_file) == 0


# ---------------------------------------------------------------------------
# stop_process_group
# ---------------------------------------------------------------------------


class TestStopProcessGroup:
    def test_sends_sigterm(self):
        mock_logger = MagicMock()
        with patch("util.os.getpgid") as mock_getpgid, patch("util.os.killpg") as mock_killpg, patch("util.time.sleep"):
            mock_getpgid.return_value = 1234

            # Make killpg with sig=0 raise ESRCH so process "dies" immediately
            call_log = []

            def killpg_side_effect(pgid, sig):
                call_log.append((pgid, sig))
                if sig == 0:
                    raise OSError(errno.ESRCH, "No such process")

            mock_killpg.side_effect = killpg_side_effect

            stop_process_group(1234, "test_process", mock_logger)

            mock_getpgid.assert_called_once_with(1234)
            # Should have called killpg with SIGTERM first, then with 0 (which raised ESRCH)
            assert (1234, signal.SIGTERM) in call_log

    def test_waits_then_kills(self):
        """If process doesn't die after 10 polls, SIGKILL should be sent."""
        mock_logger = MagicMock()
        with patch("util.os.getpgid") as mock_getpgid, patch("util.os.killpg") as mock_killpg, patch("util.time.sleep"):
            mock_getpgid.return_value = 1234

            # killpg with 0 (check) always succeeds → process "never" dies
            def killpg_side_effect(pgid, sig):
                if sig == 0:
                    return  # keep "alive"

            mock_killpg.side_effect = killpg_side_effect

            stop_process_group(1234, "test_process", mock_logger)

            # Should have called killpg with SIGTERM first, then SIGKILL after loop
            calls = mock_killpg.call_args_list
            assert any(c.args[1] == signal.SIGTERM for c in calls)
            assert any(c.args[1] == signal.SIGKILL for c in calls)

    def test_breaks_on_esrch(self):
        """Should break out of the loop if process is gone."""
        mock_logger = MagicMock()
        with patch("util.os.getpgid") as mock_getpgid, patch("util.os.killpg") as mock_killpg, patch("util.time.sleep"):
            mock_getpgid.return_value = 1234

            call_count = [0]

            def killpg_side_effect(pgid, sig):
                if sig == 0:
                    call_count[0] += 1
                    if call_count[0] == 1:
                        raise OSError(errno.ESRCH, "No such process")

            mock_killpg.side_effect = killpg_side_effect

            stop_process_group(1234, "test_process", mock_logger)

    def test_handles_process_lookup_error(self):
        """getpgid raising ProcessLookupError should not raise."""
        mock_logger = MagicMock()
        with patch("util.os.getpgid", side_effect=ProcessLookupError()):
            stop_process_group(9999, "test_process", mock_logger)  # should not raise

    def test_handles_os_error_esrch_in_check(self):
        """OS errors with ESRCH in the killpg poll loop should break."""
        mock_logger = MagicMock()
        with patch("util.os.getpgid") as mock_getpgid, patch("util.os.killpg") as mock_killpg, patch("util.time.sleep"):
            mock_getpgid.return_value = 1234

            def killpg_side_effect(pgid, sig):
                if sig == 0:
                    raise OSError(errno.ESRCH, "No such process")

            mock_killpg.side_effect = killpg_side_effect

            stop_process_group(1234, "test_process", mock_logger)

    def test_logs_error_for_other_os_errors(self):
        """Non-ESRCH OS errors during stop should be logged."""
        mock_logger = MagicMock()
        with patch("util.os.getpgid") as mock_getpgid, patch("util.os.killpg") as mock_killpg, patch("util.time.sleep"):
            mock_getpgid.return_value = 1234

            # killpg with sig=0 raises EIO (not ESRCH/EPERM) to trigger error logging
            def killpg_side_effect(pgid, sig):
                if sig == 0:
                    raise OSError(errno.EIO, "I/O error")

            mock_killpg.side_effect = killpg_side_effect

            stop_process_group(1234, "test_process", mock_logger)
            mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# get_sanitized_git_config_json
# ---------------------------------------------------------------------------


class TestGetSanitizedGitConfigJson:
    def test_sanitizes_url_credentials(self):
        mock_logger = MagicMock()
        # Use global .gitconfig origin so the line is not skipped
        output = "file:/home/user/.gitconfig\tremote.origin.url=https://user:pass@github.com/org/repo.git\n"
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert "user" not in result["remote.origin.url"]
        assert "pass" not in result["remote.origin.url"]
        assert result["remote.origin.url"] == "https://github.com/org/repo.git"

    def test_strips_credential_from_url(self):
        mock_logger = MagicMock()
        output = "credential.helper\tstore\n"
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert "credential.helper" not in result

    def test_skips_file_origin(self):
        mock_logger = MagicMock()
        output = (
            "file:.git/config\tuser.name=Test User\n"
            "file:/home/user/project/.git/config\tuser.email=local@example.com\n"
            "file:/home/user/.gitconfig\tuser.signingkey=GLOBALKEY\n"
        )
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert "user.name" not in result
        assert "user.email" not in result
        assert result.get("user.signingkey") == "GLOBALKEY"

    def test_skips_credential_dot_prefix(self):
        mock_logger = MagicMock()
        output = "credential.helper\tstore\ncredential.https://github.com.username\tmyuser\n"
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert "credential.helper" not in result
        assert "credential.https://github.com.username" not in result

    def test_returns_empty_dict_on_git_error(self):
        mock_logger = MagicMock()
        with patch("util.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "git")):
            result = get_sanitized_git_config_json(mock_logger)
        assert result == "{}"

    def test_returns_empty_dict_on_missing_git(self):
        mock_logger = MagicMock()
        with patch("util.subprocess.check_output", side_effect=FileNotFoundError):
            result = get_sanitized_git_config_json(mock_logger)
        assert result == "{}"

    def test_normal_config_works(self):
        mock_logger = MagicMock()
        output = (
            "file:/home/user/.gitconfig\tremote.origin.url=https://github.com/org/repo.git\n"
            "file:/home/user/.gitconfig\tuser.email=test@example.com\n"
        )
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert result["remote.origin.url"] == "https://github.com/org/repo.git"
        assert result["user.email"] == "test@example.com"

    def test_strips_http_credentials(self):
        mock_logger = MagicMock()
        output = "file:/home/user/.gitconfig\tremote.origin.url=http://admin:secret@github.com/org/repo.git\n"
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert result["remote.origin.url"] == "http://github.com/org/repo.git"

    def test_strips_https_credentials(self):
        mock_logger = MagicMock()
        output = "file:/home/user/.gitconfig\tremote.origin.url=https://admin:secret@github.com/org/repo.git\n"
        with patch("util.subprocess.check_output", return_value=output):
            result = json.loads(get_sanitized_git_config_json(mock_logger))
        assert result["remote.origin.url"] == "https://github.com/org/repo.git"


# ---------------------------------------------------------------------------
# run_quiet
# ---------------------------------------------------------------------------


class TestRunQuiet:
    def test_success_returns_completed_process(self):
        """A zero-exit command returns its CompletedProcess and logs nothing."""
        mock_logger = MagicMock()
        result = run_quiet(["sh", "-c", "exit 0"], logger=mock_logger)
        assert result.returncode == 0
        mock_logger.error.assert_not_called()
        mock_logger.warning.assert_not_called()

    def test_captures_output_on_success(self):
        """stdout is captured (not passed through) and available on the result."""
        result = run_quiet(["sh", "-c", "echo hello"], logger=MagicMock())
        assert result.stdout == "hello\n"

    def test_failure_raises_and_logs_error(self):
        """check=True (default): non-zero exit logs an error and raises."""
        mock_logger = MagicMock()
        with pytest.raises(subprocess.CalledProcessError):
            run_quiet(["sh", "-c", "exit 3"], label="do a thing", logger=mock_logger)
        mock_logger.error.assert_called_once()
        assert "do a thing" in str(mock_logger.error.call_args)

    def test_failure_message_includes_stderr(self):
        """The captured stderr is surfaced in the logged failure message."""
        mock_logger = MagicMock()
        with pytest.raises(subprocess.CalledProcessError):
            run_quiet(["sh", "-c", "echo boom 1>&2; exit 1"], label="do a thing", logger=mock_logger)
        assert "boom" in str(mock_logger.error.call_args)

    def test_exception_does_not_leak_argv(self):
        """A secret passed on the command line must not appear in the raised
        exception string — the label is used as the exception's command."""
        mock_logger = MagicMock()
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            run_quiet(
                ["sh", "-c", "exit 1", "SECRETPW123"],
                label="start proxy container proxy",
                logger=mock_logger,
            )
        assert "SECRETPW123" not in str(excinfo.value)
        assert excinfo.value.cmd == "start proxy container proxy"

    def test_check_false_warns_and_returns(self):
        """check=False: non-zero exit logs a warning and returns instead of raising."""
        mock_logger = MagicMock()
        result = run_quiet(["sh", "-c", "exit 1"], check=False, label="cleanup", logger=mock_logger)
        assert result.returncode == 1
        mock_logger.warning.assert_called_once()
        mock_logger.error.assert_not_called()

    def test_label_defaults_to_executable(self):
        """Without an explicit label, the executable name identifies the command."""
        mock_logger = MagicMock()
        with pytest.raises(subprocess.CalledProcessError) as excinfo:
            run_quiet(["false"], logger=mock_logger)
        assert excinfo.value.cmd == "false"

    def test_kwargs_passthrough_to_subprocess_run(self):
        """Extra kwargs (e.g. timeout) are forwarded to subprocess.run."""
        with patch("util.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")
            run_quiet(["x"], timeout=5, logger=MagicMock())
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 5
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True


# ---------------------------------------------------------------------------
# check_repo_gitignore and warn_missing_gitignore
# ---------------------------------------------------------------------------


class TestCheckRepoGitignore:
    def test_non_git_repo_returns_empty(self, tmp_path):
        from util import check_repo_gitignore

        missing = check_repo_gitignore(tmp_path)
        assert missing == []

    def test_git_repo_with_all_entries(self, tmp_path):
        from util import check_repo_gitignore

        (tmp_path / ".git").mkdir()
        # Initialize a basic .gitignore
        (tmp_path / ".gitignore").write_text(
            "\n".join(
                [
                    ".pi-container/agent/bin",
                    ".pi-container/agent/sessions",
                    ".pi-container/agent/trust.json",
                    ".pi-container/agent/models-store.json",
                    ".pi-container/exports/",
                ]
            )
        )
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                # rev-parse --show-toplevel
                subprocess.CompletedProcess(args=[], returncode=0, stdout=str(tmp_path) + "\n"),
            ]
            missing = check_repo_gitignore(tmp_path)
            assert missing == []

    def test_git_repo_missing_entries(self, tmp_path):
        from util import check_repo_gitignore

        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".pi-container/exports/\n")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=0, stdout=str(tmp_path) + "\n"),
                # check-ignore failures
                subprocess.CompletedProcess(args=[], returncode=1),
                subprocess.CompletedProcess(args=[], returncode=1),
                subprocess.CompletedProcess(args=[], returncode=1),
                subprocess.CompletedProcess(args=[], returncode=1),
            ]
            missing = check_repo_gitignore(tmp_path)
            assert ".pi-container/agent/bin" in missing
            assert ".pi-container/agent/sessions" in missing
            assert ".pi-container/agent/trust.json" in missing
            assert ".pi-container/agent/models-store.json" in missing
            assert ".pi-container/exports/" not in missing

    def test_warn_missing_gitignore_logs_warning(self, tmp_path):
        from util import warn_missing_gitignore

        mock_logger = MagicMock()
        with patch("util.check_repo_gitignore", return_value=[".pi-container/exports/"]):
            warn_missing_gitignore(tmp_path, mock_logger)
            mock_logger.warning.assert_called_once()
            assert ".pi-container/exports/" in str(mock_logger.warning.call_args)

    def test_warn_missing_gitignore_logs_nothing_when_clean(self, tmp_path):
        from util import warn_missing_gitignore

        mock_logger = MagicMock()
        with patch("util.check_repo_gitignore", return_value=[]):
            warn_missing_gitignore(tmp_path, mock_logger)
            mock_logger.warning.assert_not_called()

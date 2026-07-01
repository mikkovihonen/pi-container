import sys
sys.dont_write_bytecode = True

"""llama-server process lifecycle (start, share via refcount, socat bridge)."""

import contextlib
import fcntl
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from config import MAX_STARTUP_ATTEMPTS
from models import Model, ServerConfig
from util import get_free_port, stop_process_group

logger = logging.getLogger(__name__)


class Server:
    def __init__(self, config: ServerConfig, models_dir: Path, llama_bin: Optional[str], bridge_interface: str, lock_dir: Path, repo_root: Path, server_id: str, container_port: Optional[int] = None, use_host_socat: bool = True) -> None:
        self.config: ServerConfig = config
        self.server_id: str = server_id
        self.models_dir: Path = models_dir
        self.llama_bin: str = llama_bin or ""
        self.bridge_interface: str = bridge_interface
        # Only runtimes that share an L2 bridge with the host (Apple container)
        # need llama-server re-exposed on the bridge via socat. podman/docker
        # reach the host loopback through host.containers.internal instead.
        self.use_host_socat: bool = use_host_socat
        self.lock_dir: Path = lock_dir
        self.repo_root: Path = repo_root
        self.port: Optional[int] = None
        self.container_port: Optional[int] = container_port
        self.server_pid: Optional[int] = None
        self.socat_process: Optional[subprocess.Popen] = None
        self.models: Dict[str, Model] = {}

        server_lock_dir: Path = self.lock_dir / self.server_id
        self.paths: Dict[str, Path] = {
            "lock_dir": server_lock_dir,
            "ref_count_lock": server_lock_dir / ".llama_server_refcount.lock",
            "ref_count_file": server_lock_dir / ".llama_server_refcount",
            "pid_file": server_lock_dir / ".llama_server.pid",
            "log_file": self.repo_root / "llama-server" / "logs" / self.server_id / "llama-server.log"
        }

        for label, model_config in self.config.hf_models.items():
            self.models[label] = Model(
                label=label,
                config=model_config,
                models_dir=self.models_dir
            )

    def __enter__(self) -> 'Server':
        self.start()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[Any]) -> None:
        self.stop()

    def _ensure_models_downloaded(self) -> None:
        for model in self.models.values():
            model.download()

    def _get_server_flags(self) -> List[str]:
        if self.models.get("main") is None:
            raise ValueError(f"[{self.server_id}] No main model defined in config.")

        flags: List[str] = [str(flag) for flag in self.config.flags]
        flags.extend(["--alias", self.server_id])
        for model in self.models.values():
            flags.extend([str(model.config.file_flag), str(model.path)])
            flags.extend([str(flag) for flag in model.config.additional_server_flags])
        return flags

    def _get_bridge_ip(self) -> Optional[str]:
        # Try 'ip addr' first (Linux)
        with contextlib.suppress(subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            result = subprocess.check_output(['ip', 'addr', 'show', self.bridge_interface], text=True, stderr=subprocess.DEVNULL, timeout=5)
            match = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+', result)
            if match:
                return match.group(1)

        # Fallback to 'ifconfig' (macOS / older Linux)
        with contextlib.suppress(subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            result = subprocess.check_output(['ifconfig', self.bridge_interface], text=True, stderr=subprocess.DEVNULL, timeout=5)
            match = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', result)
            if match:
                return match.group(1)

        return None

    def _cleanup(self, pid_to_kill: Optional[int] = None, full_cleanup: bool = False) -> None:
        """Stops processes and cleans up local files for this server instance."""
        try:
            target_pid = pid_to_kill or self.server_pid
            if target_pid:
                logger.info(f"[Server: {self.server_id}] Stopping server process group (pid {target_pid})...")
                stop_process_group(target_pid, f"llama-server {'attempt' if not full_cleanup else 'group'} {self.server_id}", logger=logger)
                if target_pid == self.server_pid:
                    self.server_pid = None

            # Stop socat robustly. Prefer this instance's live handle, but fall
            # back to the pid recorded in the pid file — this covers the
            # shared-server case (the process doing the final cleanup attached to
            # an existing server and never owned the socat) and stale socats left
            # behind by a crashed run.py.
            socat_pid: Optional[int] = None
            if self.socat_process and self.socat_process.poll() is None:
                socat_pid = self.socat_process.pid
            if socat_pid is None:
                socat_pid = self._read_socat_pid()
            if socat_pid:
                logger.info(f"[Server: {self.server_id}] Stopping socat process (pid {socat_pid})...")
                stop_process_group(socat_pid, f"socat {self.server_id}", logger=logger)
            self.socat_process = None

        finally:
            self.paths["pid_file"].unlink(missing_ok=True)
            self.paths["ref_count_file"].unlink(missing_ok=True)

            if full_cleanup:
                self.paths["ref_count_lock"].unlink(missing_ok=True)
                try:
                    self.paths["lock_dir"].rmdir()
                    if self.lock_dir.exists() and not any(self.lock_dir.iterdir()):
                        self.lock_dir.rmdir()
                except OSError:
                    pass

    def wait_for_server(self, timeout: int = 180) -> bool:
        logger.info(f"[Server: {self.server_id}] Waiting for llama-server on port {self.port}")
        elapsed: int = 0
        while elapsed < timeout:
            if self.server_pid:
                try:
                    os.kill(self.server_pid, 0)
                except OSError:
                    logger.error(f"[Server: {self.server_id}] Process died during startup.")
                    return False
            else:
                return False

            with contextlib.suppress(Exception):
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        if data.get("status") == "ok":
                            logger.info(f"[Server: {self.server_id}] [OK]")
                            return True

            time.sleep(2)
            elapsed += 2
            logger.info(f"[Server: {self.server_id}] Waiting... ({elapsed}s elapsed)")

        logger.error(f"[Server: {self.server_id}] Timed out waiting for llama-server")
        return False

    def start(self) -> int:
        self._ensure_models_downloaded()

        self.paths["lock_dir"].mkdir(parents=True, exist_ok=True)

        should_start_new = False
        pid_to_cleanup = None

        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            ref_count = self._get_current_ref_count()

            if ref_count > 0:
                healthy, pid, port = self._is_existing_server_healthy()
                if healthy and pid and port:
                    self.port = port
                    ref_count += 1
                    logger.info(f"[Server: {self.server_id}] Attaching to existing healthy server on port {port}")
                    self.paths["ref_count_file"].write_text(str(ref_count))
                else:
                    logger.warning(f"[Server: {self.server_id}] Existing server is not healthy or stale. Cleaning up and restarting...")
                    pid_to_cleanup = pid
                    should_start_new = True
                    self.paths["ref_count_file"].write_text("1")
            else:
                ref_count = 1
                should_start_new = True
                self.paths["ref_count_file"].write_text("1")

        if should_start_new:
            self._cleanup(pid_to_kill=pid_to_cleanup, full_cleanup=True)
            self._start_new_server_process()

        return self.port if self.port is not None else -1

    def _get_current_ref_count(self) -> int:
        if self.paths["ref_count_file"].exists():
            try:
                return int(self.paths["ref_count_file"].read_text().strip())
            except ValueError:
                return 0
        return 0

    def _read_socat_pid(self) -> Optional[int]:
        """Read the socat pid recorded on the 3rd line of the pid file, if any."""
        if not self.paths["pid_file"].exists():
            return None
        try:
            lines = self.paths["pid_file"].read_text().splitlines()
            if len(lines) >= 3 and lines[2].strip():
                return int(lines[2])
        except (ValueError, IndexError, OSError):
            return None
        return None

    def _is_existing_server_healthy(self) -> tuple[bool, Optional[int], Optional[int]]:
        if not self.paths["pid_file"].exists():
            return False, None, None

        try:
            lines = self.paths["pid_file"].read_text().splitlines()
            if len(lines) < 2:
                return False, None, None
            pid = int(lines[0])
            port = int(lines[1])
        except (ValueError, IndexError):
            return False, None, None

        try:
            os.kill(pid, 0)
            with contextlib.suppress(Exception):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        return True, pid, port
        except OSError:
            pass

        return False, pid, port

    def _start_new_server_process(self) -> None:
        last_exception = None
        for attempt in range(MAX_STARTUP_ATTEMPTS):
            port = get_free_port()
            self.port = port

            self.paths["lock_dir"].mkdir(parents=True, exist_ok=True)
            self.paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
            cmd: List[str] = [
                self.llama_bin,
                "--host", "127.0.0.1",
                "--port", str(port),
                "--log-file", str(self.paths["log_file"]),
                *self._get_server_flags()
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            self.server_pid = process.pid

            try:
                if process.poll() is not None:
                    raise Exception("llama-server died immediately")

                bridge_ip = self._get_bridge_ip() if self.use_host_socat else None
                if bridge_ip:
                    socat_cmd = [
                        "socat",
                        f"TCP-LISTEN:{port},fork,reuseaddr,bind={bridge_ip}",
                        f"TCP:127.0.0.1:{port}"
                    ]
                    try:
                        # start_new_session gives socat its own process group so
                        # it (and its per-connection forked children) can be
                        # reaped via killpg without touching run.py, and by a
                        # different run.py process reading the pid file.
                        self.socat_process = subprocess.Popen(
                            socat_cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True
                        )
                    except Exception as e:
                        raise Exception(f"Failed to start socat: {e}")

                # pid file: llama pid, port, and socat pid (blank if no socat).
                socat_pid = self.socat_process.pid if self.socat_process else ""
                self.paths["pid_file"].write_text(f"{process.pid}\n{port}\n{socat_pid}\n")

                if self.wait_for_server():
                    self.paths["ref_count_file"].write_text("1")
                    return  # Success!
                else:
                    raise Exception(f"Timed out waiting for llama-server on port {port}")

            except Exception as e:
                last_exception = e
                logger.warning(f"[Server: {self.server_id}] Attempt {attempt + 1}/{MAX_STARTUP_ATTEMPTS} failed: {e}")
                self._cleanup(full_cleanup=False)

        raise Exception(f"Failed to start server {self.server_id} after {MAX_STARTUP_ATTEMPTS} attempts. Last error: {last_exception}")

    def stop(self) -> None:
        should_full_cleanup = False
        if self.paths["ref_count_file"].exists():
            with self.paths["ref_count_lock"].open("a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)

                ref_count: int = 0
                if self.paths["ref_count_file"].exists():
                    try:
                        ref_count = int(self.paths["ref_count_file"].read_text().strip())
                    except ValueError:
                        ref_count = 0

                ref_count -= 1

                if ref_count <= 0:
                    should_full_cleanup = True
                else:
                    self.paths["ref_count_file"].write_text(str(ref_count))

        if should_full_cleanup:
            self._cleanup(full_cleanup=True)

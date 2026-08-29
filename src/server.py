import sys

sys.dont_write_bytecode = True

"""llama-server process lifecycle and multi-client reference counting."""

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models import Model, ServerConfig
from util import (
    deregister_client,
    get_free_port,
    get_ref_count,
    is_pid_alive,
    read_live_clients,
    register_client,
    stop_process_group,
    write_clients,
)

logger = logging.getLogger(__name__)


def _config_fingerprint(config: ServerConfig) -> str:
    """Generate a stable short sha256 hash of server flags and model configurations."""
    payload = {
        "flags": [str(f) for f in config.flags],
        "models": {
            label: {
                "file_flag": m.file_flag,
                "repo": m.repo,
                "file": m.file,
                "directory": str(m.directory),
                "additional_server_flags": [str(f) for f in m.additional_server_flags],
                "sha256": m.sha256,
            }
            for label, m in sorted(config.hf_models.items())
        },
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


class Server:
    def __init__(
        self,
        config: ServerConfig,
        models_dir: Path,
        llama_bin: str | None,
        lock_dir: Path,
        repo_root: Path,
        server_id: str,
        container_port: int | None = None,
        startup_timeout: int = 180,
        startup_attempts: int = 2,
    ) -> None:
        self.config: ServerConfig = config
        self.server_id: str = server_id
        self.models_dir: Path = models_dir
        self.llama_bin: str = llama_bin or ""
        self.lock_dir: Path = lock_dir
        self.repo_root: Path = repo_root
        # llama-server startup tuning (config.yaml llama.*). Large models load
        # slowly; ``startup_timeout`` is the /health wait per attempt and
        # ``startup_attempts`` the number of (re)launches before giving up.
        self.startup_timeout: int = startup_timeout
        self.startup_attempts: int = startup_attempts
        self.port: int | None = None
        self.container_port: int | None = container_port
        self.server_pid: int | None = None
        self.models: dict[str, Model] = {}

        # Sharing identity: provider name + a fingerprint of its config. Same
        # name + same config across projects → one shared llama-server; same name
        # + different config → separate servers (no silent wrong-model sharing).
        # The ``--alias`` the agent talks to stays ``server_id`` (see
        # ``_get_server_flags``) so model ids in requests are unaffected.
        self.instance_key: str = f"{self.server_id}-{_config_fingerprint(config)}"

        server_lock_dir: Path = self.lock_dir / self.instance_key
        self.paths: dict[str, Path] = {
            "lock_dir": server_lock_dir,
            "ref_count_lock": server_lock_dir / ".llama_server_refcount.lock",
            "ref_count_file": server_lock_dir / ".llama_server_refcount",
            "clients_file": server_lock_dir / ".llama_server_clients.json",
            "pid_file": server_lock_dir / ".llama_server.pid",
            "log_file": self.repo_root / "llama-server" / "logs" / self.instance_key / "llama-server.log",
        }

        for label, model_config in self.config.hf_models.items():
            self.models[label] = Model(label=label, config=model_config, models_dir=self.models_dir)

    def __enter__(self) -> Server:
        self.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any | None) -> None:
        self.stop()

    def _ensure_models_downloaded(self) -> None:
        for model in self.models.values():
            model.download()

    def _get_server_flags(self) -> list[str]:
        if self.models.get("main") is None:
            raise ValueError(f"[{self.server_id}] No main model defined in config.")

        raw_flags: list[str] = [str(flag) for flag in self.config.flags]
        flags: list[str] = []
        i = 0
        while i < len(raw_flags):
            flag = raw_flags[i]
            if flag == "--chat-template-file" and i + 1 < len(raw_flags):
                template_path = Path(raw_flags[i + 1])
                if not template_path.is_absolute():
                    template_path = (self.repo_root / template_path).resolve()
                flags.extend([flag, str(template_path)])
                i += 2
                continue
            flags.append(flag)
            i += 1

        flags.extend(["--alias", self.server_id])
        for model in self.models.values():
            flags.extend([str(model.config.file_flag), str(model.path)])
            flags.extend([str(flag) for flag in model.config.additional_server_flags])
        return flags

    def _cleanup(self, pid_to_kill: int | None = None, full_cleanup: bool = False) -> None:
        """Stops processes and cleans up local files for this server instance."""
        try:
            target_pid = pid_to_kill or self.server_pid
            if target_pid:
                logger.info(f"[Server: {self.server_id}] Stopping server process group (pid {target_pid})...")
                stop_process_group(
                    target_pid,
                    f"llama-server {'attempt' if not full_cleanup else 'group'} {self.server_id}",
                    logger=logger,
                )
                if target_pid == self.server_pid:
                    self.server_pid = None

        finally:
            self.paths["pid_file"].unlink(missing_ok=True)
            self.paths["ref_count_file"].unlink(missing_ok=True)
            self.paths["clients_file"].unlink(missing_ok=True)

            if full_cleanup:
                with contextlib.suppress(OSError):
                    self.paths["lock_dir"].rmdir()
                    if self.lock_dir.exists() and not any(self.lock_dir.iterdir()):
                        self.lock_dir.rmdir()

    def wait_for_server(self, timeout: int | None = None) -> bool:
        timeout = self.startup_timeout if timeout is None else timeout
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

            with (
                contextlib.suppress(Exception),
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as response,
            ):
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

        client_info = {
            "pid": os.getpid(),
            "server_id": self.server_id,
            "registered_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            live_clients = read_live_clients(self.paths["clients_file"])
            healthy, pid, port = self._is_existing_server_healthy()

            if live_clients:
                if healthy and pid and port:
                    self.port = port
                    register_client(self.paths["clients_file"], client_info)
                    logger.info(
                        f"[Server: {self.server_id}] Attaching to existing healthy server on port {port} "
                        f"({len(live_clients) + 1} live clients)"
                    )
                    self.paths["ref_count_file"].write_text(str(len(live_clients) + 1))
                    return self.port
                else:
                    logger.warning(
                        f"[Server: {self.server_id}] Existing server is not healthy or stale. Cleaning up and restarting..."
                    )
                    self._cleanup(pid_to_kill=pid, full_cleanup=False)
            else:
                # No live clients. Check if an existing server process was left running by a crashed run.
                if healthy and pid and port:
                    self.port = port
                    write_clients(self.paths["clients_file"], [client_info])
                    logger.info(f"[Server: {self.server_id}] Adopting existing healthy server on port {port}")
                    self.paths["ref_count_file"].write_text("1")
                    return self.port
                else:
                    self._cleanup(pid_to_kill=pid, full_cleanup=False)

            self._start_new_server_process()
            register_client(self.paths["clients_file"], client_info)
            self.paths["ref_count_file"].write_text("1")

        return self.port if self.port is not None else -1

    def _get_current_ref_count(self) -> int:
        return get_ref_count(self.paths["clients_file"], self.paths["ref_count_file"])

    def _is_existing_server_healthy(self) -> tuple[bool, int | None, int | None]:
        if not self.paths["pid_file"].exists():
            return False, None, None

        try:
            lines = self.paths["pid_file"].read_text().splitlines()
            if len(lines) < 2:
                return False, None, None
            pid = int(lines[0])
            port = int(lines[1])
        except ValueError, IndexError:
            return False, None, None

        try:
            os.kill(pid, 0)
            with (
                contextlib.suppress(Exception),
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp,
            ):
                if resp.status == 200:
                    return True, pid, port
        except OSError:
            pass

        return False, pid, port

    def _start_new_server_process(self) -> None:
        last_exception = None
        for attempt in range(self.startup_attempts):
            port = get_free_port()
            self.port = port

            self.paths["lock_dir"].mkdir(parents=True, exist_ok=True)
            self.paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
            cmd: list[str] = [
                self.llama_bin,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-file",
                str(self.paths["log_file"]),
                *self._get_server_flags(),
            ]

            process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            self.server_pid = process.pid

            try:
                if process.poll() is not None:
                    raise Exception("llama-server died immediately")

                # pid file: llama pid and port.
                self.paths["pid_file"].write_text(f"{process.pid}\n{port}\n")

                if self.wait_for_server():
                    self.paths["ref_count_file"].write_text("1")
                    return  # Success!
                else:
                    raise Exception(f"Timed out waiting for llama-server on port {port}")

            except Exception as e:
                last_exception = e
                logger.warning(f"[Server: {self.server_id}] Attempt {attempt + 1}/{self.startup_attempts} failed: {e}")
                self._cleanup(full_cleanup=False)

        raise Exception(
            f"Failed to start server {self.server_id} after {self.startup_attempts} attempts. Last error: {last_exception}"
        )

    def stop(self) -> None:
        should_full_cleanup = False
        if not self.paths["clients_file"].exists() and not self.paths["ref_count_file"].exists():
            return

        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            remaining = deregister_client(self.paths["clients_file"], os.getpid())
            if not remaining:
                should_full_cleanup = True
            else:
                self.paths["ref_count_file"].write_text(str(len(remaining)))

        if should_full_cleanup:
            self._cleanup(full_cleanup=True)

    @classmethod
    def cleanup_orphaned_servers(cls, lock_dir: Path) -> list[str]:
        """Scan lock_dir for server instances whose clients are all dead, and stop them."""
        if not lock_dir.exists():
            return []
        cleaned = []
        for instance_dir in lock_dir.iterdir():
            if not instance_dir.is_dir():
                continue
            lock_file_path = instance_dir / ".llama_server_refcount.lock"
            clients_file = instance_dir / ".llama_server_clients.json"
            pid_file = instance_dir / ".llama_server.pid"
            refcount_file = instance_dir / ".llama_server_refcount"
            if not lock_file_path.exists() and not pid_file.exists():
                continue
            try:
                with lock_file_path.open("a") as lf:
                    try:
                        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError, OSError:
                        continue
                    live_clients = read_live_clients(clients_file)
                    if not live_clients:
                        pid = None
                        if pid_file.exists():
                            try:
                                lines = pid_file.read_text().splitlines()
                                if lines:
                                    pid = int(lines[0])
                            except ValueError, IndexError:
                                pass
                        if pid and is_pid_alive(pid):
                            logger.info(f"Stopping orphaned llama-server instance {instance_dir.name} (pid {pid})...")
                            stop_process_group(pid, f"orphaned llama-server {instance_dir.name}", logger=logger)
                        pid_file.unlink(missing_ok=True)
                        clients_file.unlink(missing_ok=True)
                        refcount_file.unlink(missing_ok=True)
                        with contextlib.suppress(OSError):
                            instance_dir.rmdir()
                        cleaned.append(instance_dir.name)
            except Exception as e:
                logger.debug(f"Could not check/clean server lock dir {instance_dir}: {e}")
        return cleaned

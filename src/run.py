import sys
sys.dont_write_bytecode = True

import hashlib
import os
import subprocess
import time
import fcntl
import signal
import json
import shutil
import logging
import urllib.request
import re
from urllib.parse import urlparse
from pathlib import Path
from contextlib import ExitStack
from typing import Any, Dict, List, Optional, Type
import contextlib
from huggingface_hub import hf_hub_download
from dataclasses import dataclass, field
from util import (
    load_dotenv,
    validate_environment,
    get_free_port,
    handle_signal,
    stop_process_group,
    get_sanitized_git_config_json,
    EnvironmentError,
)

# ─── Module Loading ──────────────────────────────────────────────────────

SCRIPT_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = SCRIPT_DIR.parent
PROJECT_DIR: Path = Path(os.environ.get("PROJECT_DIR", Path.cwd()))
DOTENV_PATH: Path = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

# Configure logging
log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

IMAGE_TAG: str = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
LLAMA_BIN: Optional[str] = os.environ.get("LLAMA_BIN") or shutil.which("llama-server")
MAX_STARTUP_ATTEMPTS: int =  int(os.environ.get("MAX_STARTUP_ATTEMPTS", 2))
MODELS_DIR: Path = REPO_ROOT / "llama-server" / "models"
LLAMA_SERVER_LOCK_DIR: Path = REPO_ROOT / "llama-server" / ".locks"
BRIDGE_INTERFACE: str = os.environ.get("BRIDGE_INTERFACE", "bridge100")
ADMIN_PASSWORD: str = os.environ.get('ADMIN_PASSWORD', '')

# ─── Configuration Dataclasses ───────────────────────────────────────────

@dataclass(frozen=True)
class ModelConfig:
    file_flag: str
    repo: str
    file: str
    directory: Path
    additional_server_flags: List[Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ModelConfig':
        return cls(
            file_flag=data["fileFlag"],
            repo=data["repo"],
            file=data["file"],
            directory=Path(data["dir"]),
            additional_server_flags=data.get("additionalServerFlags", [])
        )

@dataclass(frozen=True)
class ServerConfig:
    hf_models: Dict[str, ModelConfig]
    flags: List[Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ServerConfig':
        hf_models = {
            label: ModelConfig.from_dict(m_info)
            for label, m_info in data.get("hfModels", {}).items()
        }
        return cls(
            hf_models=hf_models,
            flags=data.get("flags", [])
        )

# ─── Model Class ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Model:
    label: str
    config: ModelConfig
    models_dir: Path

    @property
    def path(self) -> Path:
        return self.models_dir / self.config.directory / self.config.file

    def download(self) -> None:
        if self.path.exists():
            logger.info(f"[Model: {self.label}] Found existing model: {self.path}")
            return

        # Create a shared lock directory for all models
        shared_lock_dir: Path = self.models_dir.parent / ".model_download_locks"
        shared_lock_dir.mkdir(exist_ok=True, parents=True)

        # Create a unique lock filename based on repo and file
        model_id = hashlib.sha256(f"{self.config.repo}:{self.config.file}".encode()).hexdigest()
        lock_file_path = shared_lock_dir / f"{model_id}.lock"

        logger.info(f"[Model: {self.label}] Acquiring lock for download...")
        with lock_file_path.open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            # Re-check if exists after acquiring lock
            if self.path.exists():
                logger.info(f"[Model: {self.label}] Found existing model after acquiring lock: {self.path}")
                return

            logger.info(f"[Model: {self.label}] Downloading {self.config.file} from {self.config.repo}...")
            self.path.parent.mkdir(exist_ok=True, parents=True)
            try:
                hf_hub_download(
                    repo_id=self.config.repo,
                    filename=self.config.file,
                    local_dir=str(self.models_dir / self.config.directory),
                    local_dir_use_symlinks=False
                )
                logger.info(f"[Model: {self.label}] Download complete.")
            except Exception:
                logger.exception(f"[Model: {self.label}] Download failed")
                raise


# ─── Container Network Manager ───────────────────────────────────────────

class ContainerNetworkManager:
    def __init__(self, container_runtime: str, network_name: str, proxy_image: str, proxy_name: str = "proxy") -> None:
        self.container_runtime: str = container_runtime
        self.network_name: str = network_name
        self.proxy_image: str = proxy_image
        self.proxy_name: str = proxy_name

        # Shared directory for synchronization across different run.py processes
        self.lock_dir: Path = REPO_ROOT / "pi-coding-agent-proxy" / ".locks"
        self.paths: Dict[str, Path] = {
            "lock_dir": self.lock_dir,
            "ref_count_lock": self.lock_dir / ".network_manager.lock",
            "ref_count_file": self.lock_dir / ".network_manager.refcount",
        }

    def __enter__(self) -> 'ContainerNetworkManager':
        self.start()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[Any]) -> None:
        self.stop()

    def start(self) -> None:
        self.paths["lock_dir"].mkdir(exist_ok=True, parents=True)

        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            ref_count = self._get_ref_count()
            if ref_count == 0:
                self._actually_start()

            ref_count += 1
            self.paths["ref_count_file"].write_text(str(ref_count))

    def stop(self) -> None:
        should_full_cleanup = False
        with self.paths["ref_count_lock"].open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            ref_count = self._get_ref_count()
            if ref_count <= 1:
                self._actually_stop()
                should_full_cleanup = True
            else:
                ref_count -= 1
                self.paths["ref_count_file"].write_text(str(ref_count))

        if should_full_cleanup:
            self.paths["ref_count_file"].unlink(missing_ok=True)
            try:
                self.paths["ref_count_lock"].unlink(missing_ok=True)
                if self.paths["lock_dir"].exists() and not any(self.paths["lock_dir"].iterdir()):
                    self.paths["lock_dir"].rmdir()
            except OSError:
                pass

    def _get_ref_count(self) -> int:
        if self.paths["ref_count_file"].exists():
            try:
                return int(self.paths["ref_count_file"].read_text().strip())
            except ValueError:
                return 0
        return 0

    def _actually_start(self) -> None:
        # Create the isolated internal network
        logger.info(f"Creating network {self.network_name}...")
        subprocess.run([
            self.container_runtime,
            "network",
            "create",
            "--internal",
            self.network_name
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Start proxy container
        logger.info(f"Starting proxy container {self.proxy_name} from {self.proxy_image}...")
        subprocess.run([
            self.container_runtime,
            "run", "-d", "--rm", "--name", self.proxy_name,
            "--network", "default",
            "--network", self.network_name,
            "--cap-add", "NET_ADMIN",
            "--dns", "1.1.1.1",
            "-p", "8081:8081",
            "--env", f"ADMIN_PASSWORD={ADMIN_PASSWORD}",
            self.proxy_image
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for proxy to be ready
        time.sleep(2)

    def _actually_stop(self) -> None:
        logger.info(f"Stopping proxy container {self.proxy_name}...")
        subprocess.run([self.container_runtime, "stop", self.proxy_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        logger.info(f"Removing network {self.network_name}...")
        subprocess.run([self.container_runtime, "network", "delete", self.network_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

class Server:
    def __init__(self, config: ServerConfig, models_dir: Path, llama_bin: Optional[str], bridge_interface: str, lock_dir: Path, repo_root: Path, server_id: str, container_port: Optional[int] = None) -> None:
        self.config: ServerConfig = config
        self.server_id: str = server_id
        self.models_dir: Path = models_dir
        self.llama_bin: str = llama_bin or ""
        self.bridge_interface: str = bridge_interface
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

            if self.socat_process and self.socat_process.poll() is None:
                logger.info(f"[Server: {self.server_id}] Stopping socat process (pid {self.socat_process.pid})...")
                self.socat_process.terminate()
                try:
                    self.socat_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.socat_process.kill()
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

                bridge_ip = self._get_bridge_ip()
                if bridge_ip:
                    socat_cmd = [
                        "socat",
                        f"TCP-LISTEN:{port},fork,reuseaddr,bind={bridge_ip}",
                        f"TCP:127.0.0.1:{port}"
                    ]
                    try:
                        self.socat_process = subprocess.Popen(
                            socat_cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                    except Exception as e:
                        raise Exception(f"Failed to start socat: {e}")

                self.paths["pid_file"].write_text(f"{process.pid}\n{port}\n")

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

# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        container_runtime = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)

    config_path: Path = REPO_ROOT / "pi-coding-agent" / "home" / ".pi" / "agent" / "models.json"
    if not config_path.exists():
         logger.error(f"Config file not found: {config_path}")
         sys.exit(1)

    with config_path.open('r') as file:
        data = json.load(file)
        server_configs = []
        for name, val in data["providers"].items():
            if isinstance(val, dict) and "serverCustomParameters" in val:
                server_config = ServerConfig.from_dict(val["serverCustomParameters"])
                server_configs.append({
                    "name": name,
                    "config": server_config,
                    "baseUrl": val.get("baseUrl")
                })

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        with ExitStack() as stack:
            servers: List[Server] = []
            for item in server_configs:
                base_url = item["baseUrl"]
                container_port = urlparse(base_url).port if base_url else None

                server = Server(
                    config=item["config"],
                    models_dir=MODELS_DIR,
                    llama_bin=LLAMA_BIN,
                    bridge_interface=BRIDGE_INTERFACE,
                    lock_dir=LLAMA_SERVER_LOCK_DIR,
                    repo_root=REPO_ROOT,
                    server_id=item["name"],
                    container_port=container_port
                )
                stack.enter_context(server)
                servers.append(server)

            portconfig = json.dumps(
                [{"cp": server.container_port, "hp": server.port} for server in servers]
            )

            with ContainerNetworkManager(
                container_runtime,
                "isolated-net",
                "pi-coding-agent-proxy:local"
            ) as _:
                eth1_ip = None
                gateway_ip = None
                try:
                    result_ip = subprocess.run(
                        [container_runtime, "exec", "proxy", "ip", "addr", "show", "eth1"],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5
                    )
                    match_ip = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+', result_ip.stdout)
                    if match_ip:
                        eth1_ip = match_ip.group(1)
                        logger.info(f"Found eth1 IP address: {eth1_ip}")

                    # Get default gateway
                    result_route = subprocess.run(
                        [container_runtime, "exec", "proxy", "ip", "route", "show", "default"],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5
                    )
                    match_route = re.search(r'default via (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', result_route.stdout)
                    if match_route:
                        gateway_ip = match_route.group(1)
                        logger.info(f"Found proxy default gateway: {gateway_ip}")

                except Exception as e:
                    logger.warning(f"Could not retrieve proxy network info: {e}")

                pi_container_cmd = [
                    container_runtime, "run",
                    "--rm",
                    "--interactive",
                    "--tty",
                    "--dns", f"{eth1_ip}",
                    "--cap-add", "NET_ADMIN",
                    "--network", "isolated-net",
                    "--tmpfs", "/home/pi/",
                    "--volume", f"{REPO_ROOT}/pi-coding-agent/home/.pi:/home/pi/.pi",
                    "--tmpfs", "/home/pi/.pi/agent/bin",
                    "--volume", f"{PROJECT_DIR}:/workspace",
                    "--workdir", "/workspace",
                ]

                if eth1_ip:
                    pi_container_cmd.extend(["--env", f"DEFAULT_ROUTE={eth1_ip}"])
                if gateway_ip:
                    pi_container_cmd.extend(["--env", f"GATEWAY_IP={gateway_ip}"])

                pi_container_cmd.extend([
                    "--env", f"LLAMA_PORTS={portconfig}",
                    "--env", f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                    IMAGE_TAG,
                    *sys.argv[1:]
                ])

                result = subprocess.run(pi_container_cmd)


            if result.returncode != 0:
                sys.exit(result.returncode)

    except SystemExit:
        sys.exit(0)
    except Exception:
        logger.exception("An error occurred")
        sys.exit(1)

if __name__ == "__main__":
    main()

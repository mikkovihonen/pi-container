import sys
sys.dont_write_bytecode = True

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
import importlib.util
from urllib.parse import urlparse
import traceback
from pathlib import Path
from contextlib import ExitStack
from typing import Any, Dict, List, Optional, Type
from huggingface_hub import hf_hub_download
from dataclasses import dataclass, field
from util import load_dotenv, validate_environment, get_free_port, handle_signal, stop_process_group, get_sanitized_git_config_json


# ─── Module Loading ──────────────────────────────────────────────────────

def _import_util_module(script_dir: Path) -> Optional[Any]:
    """Robustly imports util.py from the same directory without modifying sys.path."""
    util_path = script_dir / "util.py"
    if not util_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("util", str(util_path))
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None

SCRIPT_DIR: Path = Path(__file__).resolve().parent
util_module = _import_util_module(SCRIPT_DIR)
load_dotenv = getattr(util_module, "load_dotenv", lambda _: None)

# ─── Constants ─────────────────────────────────────────────────────────────

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

# ─── Configuration Dataclasses ───────────────────────────────────────────

@dataclass(frozen=True)
class ModelConfig:
    file_flag: str
    repo: str
    file: str
    directory: Path
    additional_server_flags: List[Any]

@dataclass(frozen=True)
class ServerConfig:
    hf_models: Dict[str, ModelConfig]
    flags: List[Any]

class EnvironmentError(Exception):
    """Raised when the environment does not meet requirements."""
    pass

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
        except Exception as e:
            logger.error(f"[Model: {self.label}] Download failed: {e}")
            raise

# ─── Server Class ──────────────────────────────────────────────────────────

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
            "download_lock": server_lock_dir / ".model_download.lock",
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
        self.paths["download_lock"].parent.mkdir(exist_ok=True, parents=True)
        with open(self.paths["download_lock"], "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            for model in self.models.values():
                model.download()

    def _get_server_flags(self) -> List[str]:
        flags: List[str] = list(map(str, self.config.flags))
        flags.extend(["--alias", self.server_id])
        if self.models.get("main") == None:
            raise ValueError(f"[{self.server_id}] No main model defined in config.")
        else:
            for model in self.models.values():
                flags.extend([str(model.config.file_flag), str(model.path)])
                flags.extend(
                    [str(flag) for flag in model.config.additional_server_flags]
                )
        return flags

    def _get_bridge_ip(self) -> Optional[str]:
        # Try 'ip addr' first (Linux)
        try:
            result = subprocess.check_output(['ip', 'addr', 'show', self.bridge_interface], text=True, stderr=subprocess.DEVNULL)
            match = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+', result)
            if match:
                return match.group(1)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Fallback to 'ifconfig' (macOS / older Linux)
        try:
            result = subprocess.check_output(['ifconfig', self.bridge_interface], text=True, stderr=subprocess.DEVNULL)
            match = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', result)
            if match:
                return match.group(1)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        return None

    def _stop_completely(self, pid_to_kill: Optional[int] = None) -> None:
        """Stops a process group and cleans up local files for this server instance."""
        target_pid = pid_to_kill or self.server_pid
        if target_pid:
            logger.info(f"[Server: {self.server_id}] Stopping server process group (pid {target_pid})...")
            stop_process_group(target_pid, f"llama-server group {self.server_id}", logger=logger)
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

        if self.paths["pid_file"].exists():
            self.paths["pid_file"].unlink(missing_ok=True)

        self.paths["ref_count_file"].unlink(missing_ok=True)
        self.paths["download_lock"].unlink(missing_ok=True)

        try:
            self.paths["lock_dir"].rmdir()
            if self.lock_dir.exists() and not any(self.lock_dir.iterdir()):
                logger.info(f"[Server: {self.server_id}] .locks directory is empty, deleting {self.lock_dir}")
                self.lock_dir.rmdir()
        except OSError:
            pass

    def _cleanup_attempt(self) -> None:
        """Cleans up only the current process attempt, used for retries."""
        if self.server_pid:
            stop_process_group(self.server_pid, f"llama-server attempt {self.server_id}", logger=logger)
            self.server_pid = None

        if self.socat_process and self.socat_process.poll() is None:
            self.socat_process.terminate()
            try:
                self.socat_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.socat_process.kill()
            self.socat_process = None

        if self.paths["pid_file"].exists():
            self.paths["pid_file"].unlink(missing_ok=True)

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

            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        if data.get("status") == "ok":
                            logger.info(f"[Server: {self.server_id}] [OK]")
                            return True
            except Exception:
                pass

            time.sleep(2)
            elapsed += 2
            logger.info(f"[Server: {self.server_id}] Waiting... ({elapsed}s elapsed)")

        logger.error(f"[Server: {self.server_id}] Timed out waiting for llama-server")
        return False

    def start(self) -> int:
        self._ensure_models_downloaded()

        with open(self.paths["ref_count_lock"], "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            ref_count = self._get_current_ref_count()

            if ref_count > 0:
                healthy, pid, port = self._is_existing_server_healthy()
                if healthy and pid and port:
                    self.port = port
                    ref_count += 1
                    logger.info(f"[Server: {self.server_id}] Attaching to existing healthy server on port {port}")
                else:
                    logger.warning(f"[Server: {self.server_id}] Existing server is not healthy or stale. Cleaning up and restarting...")
                    self._stop_completely(pid)
                    ref_count = 1
                    self._start_new_server_process()
            else:
                ref_count = 1
                self._start_new_server_process()

            self.paths["ref_count_file"].write_text(str(ref_count))

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
            with open(self.paths["pid_file"], "r") as pf:
                lines = pf.read().splitlines()
                if len(lines) < 2:
                    return False, None, None
                pid = int(lines[0])
                port = int(lines[1])
        except (ValueError, IndexError):
            return False, None, None

        try:
            os.kill(pid, 0)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        return True, pid, port
            except Exception:
                pass
        except OSError:
            pass

        return False, pid, port

    def _start_new_server_process(self) -> None:
        for attempt in range(MAX_STARTUP_ATTEMPTS):
            port = get_free_port()
            self.port = port

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
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            self.server_pid = process.pid

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
                    logger.info(f"[Server: {self.server_id}] socat started (pid {self.socat_process.pid}) listening on {bridge_ip}:{port}")
                except Exception as e:
                    logger.warning(f"[Server: {self.server_id}] Failed to start socat: {e}")

            with open(self.paths["pid_file"], "w") as pf:
                pf.write(f"{process.pid}\n{port}\n")

            if self.wait_for_server():
                return
            else:
                logger.warning(f"[Server: {self.server_id}] Attempt {attempt + 1}/{MAX_STARTUP_ATTEMPTS} failed to start on port {port}. Retrying...")
                self._cleanup_attempt()

        raise Exception(f"Failed to start server {self.server_id} after {MAX_STARTUP_ATTEMPTS} attempts.")

    def stop(self) -> None:
        if self.paths["ref_count_file"].exists():
            with open(self.paths["ref_count_lock"], "a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)

                ref_count: int = 0
                if self.paths["ref_count_file"].exists():
                    try:
                        ref_count = int(self.paths["ref_count_file"].read_text().strip())
                    except ValueError:
                        ref_count = 0

                ref_count -= 1

                if ref_count <= 0:
                    self._stop_completely()
                else:
                    self.paths["ref_count_file"].write_text(str(ref_count))

# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        container_runtime = validate_environment(LLAMA_BIN)
    except EnvironmentError as e:
        logger.error(f"Environment Error: {e}")
        sys.exit(1)

    config_path: Path = REPO_ROOT / "pi-home" / ".pi" / "agent" / "models.json"
    if not config_path.exists():
         logger.error(f"Config file not found: {config_path}")
         sys.exit(1)

    with open(config_path, 'r') as file:
        data = json.load(file)
        server_configs = []
        for name, val in data["providers"].items():
            if isinstance(val, dict) and "serverCustomParameters" in val:
                params = val["serverCustomParameters"]
                hf_models_dict = {}
                for label, m_info in params.get("hfModels", {}).items():
                    hf_models_dict[label] = ModelConfig(
                        file_flag=m_info["fileFlag"],
                        repo=m_info["repo"],
                        file=m_info["file"],
                        directory=Path(m_info["dir"]),
                        additional_server_flags=m_info["additionalServerFlags"]
                    )

                server_config = ServerConfig(
                    hf_models=hf_models_dict,
                    flags=params.get("flags", [])
                )
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

            container_cmd = [
                container_runtime, "run",
                "--rm",
                "--interactive",
                "--tty",
                "--tmpfs", "/home/pi/",
                "--volume", f"{REPO_ROOT}/pi-home/.pi:/home/pi/.pi",
                "--tmpfs", "/home/pi/.pi/agent/bin",
                "--volume", f"{PROJECT_DIR}:/workspace",
                "--workdir", "/workspace",
                "--env", f"LLAMA_PORTS={portconfig}",
                "--env", f"HOST_GIT_CONFIG={get_sanitized_git_config_json(logger=logger)}",
                IMAGE_TAG,
                *sys.argv[1:]
            ]

            result = subprocess.run(container_cmd)
            if result.returncode != 0:
                sys.exit(result.returncode)

    except SystemExit:
        sys.exit(0)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

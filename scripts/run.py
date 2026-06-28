import os
import sys
import subprocess
import socket
import time
import fcntl
import signal
import json
import errno
import shutil
import logging
import urllib.request
from urllib.parse import urlparse
import traceback
from pathlib import Path
from contextlib import ExitStack
from typing import Any, Dict, List, Optional, Type, Tuple
from huggingface_hub import hf_hub_download

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Add scripts directory to sys.path so we can import from util
sys.path.append(str(Path(__file__).resolve().parent))
try:
    from util import load_dotenv
except ImportError:
    def load_dotenv(path: Path):
        pass

# ─── Constants ─────────────────────────────────────────────────────────────

SCRIPT_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = SCRIPT_DIR.parent
PROJECT_DIR: Path = Path(os.environ.get("PROJECT_DIR", os.getcwd()))
DOTENV_PATH: Path = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

IMAGE_TAG: str = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
LLAMA_BIN: Optional[str] = os.environ.get("LLAMA_BIN") or shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
MODELS_DIR: Path = REPO_ROOT / "llama-server" / "models"
LLAMA_SERVER_LOCK_DIR: Path = REPO_ROOT / "llama-server" / ".locks"
BRIDGE_INTERFACE: str = os.environ.get("BRIDGE_INTERFACE", "bridge100")

# ─── Utility Functions ─────────────────────────────────────────────────────

class EnvironmentError(Exception):
    """Raised when the environment does not meet requirements."""
    pass

def validate_environment(llama_bin: Optional[str]) -> str:
    if llama_bin is None or not os.path.exists(llama_bin):
        raise EnvironmentError(f"llama-server not found at {llama_bin}. Install via: brew install llama.cpp")

    if shutil.which("hf") is None:
        raise EnvironmentError("hf not found. Install via: pip install huggingface_hub[cli]")

    if shutil.which("socat") is None:
        raise EnvironmentError("socat not found. Install via: brew install socat")

    runtime: Optional[str] = None
    if shutil.which("container") is not None:
        runtime = "container"
    elif shutil.which("docker") is not None:
        runtime = "docker"
    elif shutil.which("podman") is not None:
        runtime = "podman"

    if runtime is None:
        raise EnvironmentError("No supported container runtime found (container, docker or podman).")

    return runtime

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def handle_signal(signum: int, frame: Any) -> None:
    signame: str = signal.Signals(signum).name
    logger.info(f"Received {signame}. Initiating clean shutdown...")
    raise SystemExit


def stop_process_group(pid: int, name: str) -> None:
    """Stops a process group to ensure all child processes are killed."""
    logger.info(f"Stopping process group for {name} (pgid: {pid})...")
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)

        for _ in range(10):
            try:
                os.killpg(pgid, 0)
                time.sleep(0.5)
            except OSError as e:
                if e.errno in (errno.ESRCH, errno.EPERM):
                    break
                raise
        else:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as e:
        if e.errno != errno.ESRCH:
            logger.error(f"Error stopping process group for {name}: {e}")

# ─── Model Class ───────────────────────────────────────────────────────────

class Model:
    def __init__(self, label: str, repo: str, directory: Path, file: Path, models_dir: Path) -> None:
        self.label: str = label
        self.repo: str = repo
        self.directory: Path = directory
        self.file: Path = file
        self.models_dir: Path = models_dir
        self.path: Path = models_dir / directory / file

    def download(self) -> None:
        if self.path.exists():
            logger.info(f"[Model: {self.label}] Found existing model: {self.path}")
            return

        logger.info(f"[Model: {self.label}] Downloading {self.file} from {self.repo}...")
        self.path.parent.mkdir(exist_ok=True, parents=True)
        try:
            hf_hub_download(
                repo_id=self.repo,
                filename=str(self.file),
                local_dir=str(self.models_dir / self.directory),
                local_dir_use_symlinks=False
            )
            logger.info(f"[Model: {self.label}] Download complete.")
        except Exception as e:
            logger.error(f"[Model: {self.label}] Download failed: {e}")
            raise

# ─── Server Class ──────────────────────────────────────────────────────────

class Server:
    def __init__(self, config: Dict[str, Any], models_dir: Path, llama_bin: Optional[str], bridge_interface: str, lock_dir: Path, repo_root: Path, server_id: str, container_port: Optional[int] = None) -> None:
        self.config: Dict[str, Any] = config
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

        hf_models_conf: Dict[str, Any] = self.config.get("hf-models", {})
        for label, model_conf in hf_models_conf.items():
            self.models[label] = Model(
                label,
                model_conf["repo"],
                model_conf["dir"],
                model_conf["file"],
                self.models_dir
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
        flags: List[str] = list(map(str, self.config["flags"]))
        flags.extend(["--alias", self.server_id])

        main_model: Optional[Model] = self.models.get("main")
        if main_model:
            flags.extend(["--model", str(main_model.path)])
        else:
            raise ValueError(f"[{self.server_id}] No main model defined in config.")

        draft_model: Optional[Model] = self.models.get("draft")
        if draft_model:
            flags.extend(["--model-draft", str(draft_model.path)])

        return flags

    def _get_bridge_ip(self) -> Optional[str]:
        try:
            result: str = subprocess.check_output(['ifconfig', self.bridge_interface], text=True)
            for line in result.splitlines():
                if 'inet ' in line:
                    return line.split()[1]
        except (subprocess.CalledProcessError, IndexError, AttributeError):
            return None
        return None

    def _stop_completely(self) -> None:
        if self.server_pid:
            logger.info(f"[Server: {self.server_id}] Stopping server process group (pid {self.server_pid})...")
            stop_process_group(self.server_pid, f"llama-server group {self.server_id}")
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
        self.paths["ref_count_lock"].unlink(missing_ok=True)
        self.paths["download_lock"].unlink(missing_ok=True)

        try:
            self.paths["lock_dir"].rmdir()
            # If the parent .locks directory is now empty, delete it as well.
            if self.lock_dir.exists() and not any(self.lock_dir.iterdir()):
                logger.info(f"[Server: {self.server_id}] .locks directory is empty, deleting {self.lock_dir}")
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
        server_flags: List[str] = self._get_server_flags()

        fcntl.flock(open(self.paths["ref_count_lock"], "a"), fcntl.LOCK_EX)

        ref_count: int = 0
        if self.paths["ref_count_file"].exists():
            try:
                ref_count = int(self.paths["ref_count_file"].read_text().strip())
            except ValueError:
                ref_count = 0

        if ref_count > 0:
            is_running: bool = False
            if self.paths["pid_file"].exists():
                try:
                    with open(self.paths["pid_file"], "r") as pf:
                        lines = pf.read().splitlines()
                        if len(lines) >= 2:
                            potential_pid = int(lines[0])
                            self.port = int(lines[1])
                            try:
                                os.kill(potential_pid, 0)
                                is_running = True
                                self.server_pid = potential_pid
                            except OSError:
                                is_running = False
                except (ValueError, IndexError):
                    pass

            if not is_running:
                logger.warning(f"[Server: {self.server_id}] Server is supposed to be running but process is not found. Cleaning up...")
                self._stop_completely()
                ref_count = 0
            else:
                ref_count += 1

        if ref_count == 0:
            port = get_free_port()
            self.port = port

            self.paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
            cmd: List[str] = [
                self.llama_bin,
                "--host", "127.0.0.1",
                "--port", str(port),
                "--log-file", str(self.paths["log_file"]),
                *server_flags
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

            if not self.wait_for_server():
                self._stop_completely()
                raise Exception(f"Failed to start server {self.server_id}")

            ref_count = 1

        self.paths["ref_count_file"].write_text(str(ref_count))
        return self.port if self.port is not None else -1

    def stop(self) -> None:
        if self.paths["ref_count_file"].exists():
            fcntl.flock(open(self.paths["ref_count_lock"], "a"), fcntl.LOCK_EX)

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
                server_configs.append({"name": name, "val": val})

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        with ExitStack() as stack:
            servers: List[Server] = []
            for item in server_configs:
                base_url = item["val"].get("baseUrl")
                container_port = urlparse(base_url).port if base_url else None

                server = Server(
                    config=item["val"]["serverCustomParameters"],
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

            container_cmd = [
                container_runtime, "run",
                "--rm",
                "--interactive",
                "--tty",
                "--volume", f"{REPO_ROOT}/pi-home:/home/pi",
                "--volume", f"{PROJECT_DIR}:/workspace",
                "--tmpfs", "/home/pi/.gitconfig",
                "--tmpfs", "/home/pi/.cache",
                "--tmpfs", "/home/pi/.local",
                "--tmpfs", "/home/pi/.venv",
                "--tmpfs", "/home/pi/.pi/agent/bin",
                "--workdir", "/workspace",
                "--env", f"LLAMA_PORTS={
                    json.dumps(
                        [{"cp": server.container_port, "hp": server.port} for server in servers]
                    )
                }",
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

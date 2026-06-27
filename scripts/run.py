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
from pathlib import Path
from contextlib import ExitStack
from typing import Any, Dict, List, Optional, Type, Tuple

# Add scripts directory to sys.path so we can import from util
sys.path.append(str(Path(__file__).resolve().parent))
from util import load_dotenv

# ─── Constants ─────────────────────────────────────────────────────────────

SCRIPT_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = SCRIPT_DIR.parent
PROJECT_DIR: Path = Path(os.environ.get("PROJECT_DIR", os.getcwd()))
DOTENV_PATH: Path = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

IMAGE_TAG: str = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
LLAMA_BIN: Optional[str] = os.environ.get("LLAMA_BIN") or shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
MODELS_DIR: Path = REPO_ROOT / "models"
LLAMA_SERVER_LOCK_DIR: Path = REPO_ROOT / ".llama-server.locks"
BRIDGE_INTERFACE: str = os.environ.get("BRIDGE_INTERFACE", "bridge100")

# ─── Utility Functions ─────────────────────────────────────────────────────

def validate_environment(llama_bin: Optional[str]) -> str:
    if llama_bin is None or not os.path.exists(llama_bin):
        print(f"[ERROR] llama-server not found at {llama_bin}. Install via: brew install llama.cpp")
        sys.exit(1)

    if shutil.which("hf") is None:
        print("[ERROR] hf not found. Install via: pip install huggingface_hub[cli]")
        sys.exit(1)

    if shutil.which("socat") is None:
        print("[ERROR] socat not found. Install via: brew install socat")
        sys.exit(1)

    runtime: Optional[str] = None
    if shutil.which("container") is not None:
        runtime = "container"
    elif shutil.which("docker") is not None:
        runtime = "docker"
    elif shutil.which("podman") is not None:
        runtime = "podman"

    if runtime is None:
        print("[ERROR] No supported container runtime found (container, docker or podman).")
        sys.exit(1)

    return runtime

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def handle_signal(signum: int, frame: Any) -> None:
    signame: str = signal.Signals(signum).name
    print(f"\n[INFO] Received {signame}. Initiating clean shutdown...")
    raise SystemExit


def stop_process_gracefully(pid: Optional[int], name: str) -> None:
    if pid is None:
        return
    print(f"[INFO] Stopping {name} (pid {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except OSError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

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
            print(f"[INFO] [Model: {self.label}] Found existing model: {self.path}")
            return

        print(f"[INFO] [Model: {self.label}] Downloading {self.file} from {self.repo}...")
        self.path.parent.mkdir(exist_ok=True, parents=True)
        try:
            # Using 'hf' command as in the original script
            subprocess.run(["hf", "download", self.repo, str(self.file), "--local-dir", str(self.models_dir / self.directory)], check=True)
            print(f"[INFO] [Model: {self.label}] Download complete.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] [Model: {self.label}] Download failed: {e}")
            sys.exit(1)

# ─── Server Class ──────────────────────────────────────────────────────────

class Server:
    def __init__(self, config: Dict[str, Any], models_dir: Path, llama_bin: Optional[str], bridge_interface: str, lock_dir: Path, repo_root: Path) -> None:
        self.config: Dict[str, Any] = config
        self.server_id: str = config["id"]
        self.models_dir: Path = models_dir
        self.llama_bin: str = llama_bin if llama_bin else ""
        self.bridge_interface: str = bridge_interface
        self.lock_dir: Path = lock_dir
        self.repo_root: Path = repo_root
        self.port: Optional[int] = None
        self.server_pid: Optional[int] = None
        self.socat_pid: Optional[int] = None
        self.models: Dict[str, Model] = {}

        # Paths
        server_lock_dir: Path = self.lock_dir / self.server_id
        self.paths: Dict[str, Path] = {
            "lock_dir": server_lock_dir,
            "download_lock": server_lock_dir / ".model_download.lock",
            "ref_count_lock": server_lock_dir / ".llama_server_refcount.lock",
            "ref_count_file": server_lock_dir / ".llama_server_refcount",
            "pid_file": server_lock_dir / ".llama_server.pid",
            "log_file": self.repo_root / "logs" / self.server_id / "llama-server.log"
        }

        # Initialize models
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
            # Get the output of ifconfig for the specific interface
            result: str = subprocess.check_output(['ifconfig', self.bridge_interface], text=True)
            for line in result.splitlines():
                if 'inet ' in line:
                    return line.split()[1]
        except (subprocess.CalledProcessError, IndexError, AttributeError):
            return None
        return None

    def _stop_completely(self) -> None:
        if self.paths["pid_file"].exists():
            try:
                with open(self.paths["pid_file"], "r") as pf:
                    lines: List[str] = pf.readlines()
                    if len(lines) >= 1:
                        server_pid_int: Optional[int] = None
                        try:
                            server_pid_int = int(lines[0].strip())
                        except ValueError:
                            pass
                        if server_pid_int is not None:
                            stop_process_gracefully(server_pid_int, f"llama-server {self.server_id}")
                    if len(lines) >= 3:
                        try:
                            socat_pid_int: int = int(lines[2].strip())
                            stop_process_gracefully(socat_pid_int, f"socat for {self.server_id}")
                        except (ValueError, IndexError):
                            pass
            except Exception as e:
                print(f"[ERROR] [Server: {self.server_id}] Error during complete stop: {e}")
            finally:
                self.paths["pid_file"].unlink(missing_ok=True)
                self.paths["ref_count_file"].unlink(missing_ok=True)
                self.paths["ref_count_lock"].unlink(missing_ok=True)
                self.paths["download_lock"].unlink(missing_ok=True)
                try:
                    self.paths["lock_dir"].rmdir()
                except Exception as e:
                    print(f"[ERROR] [Server: {self.server_id}] Could not remove lock directory: {e}")

    def _start_server_process(self, server_flags: List[str]) -> Tuple[int, int]:
        port: int = get_free_port()
        print(f"[INFO] [Server: {self.server_id}] Allocated port {port}")

        self.paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
        cmd: List[str] = [
            self.llama_bin,
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-file", str(self.paths["log_file"]),
            *server_flags
        ]
        process: subprocess.Popen = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        bridge_ip: Optional[str] = self._get_bridge_ip()
        socat_process: Optional[subprocess.Popen] = None
        if bridge_ip:
            socat_cmd: List[str] = [
                "socat",
                f"TCP-LISTEN:{port},fork,reuseaddr,bind={bridge_ip}",
                f"TCP:127.0.0.1:{port}"
            ]
            try:
                socat_process = subprocess.Popen(socat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[INFO] [Server: {self.server_id}] socat started (pid {socat_process.pid}) listening on {bridge_ip}:{port}")
            except Exception as e:
                print(f"[WARN] [Server: {self.server_id}] Failed to start socat: {e}")
        elif self.bridge_interface:
            print(f"[WARN] [Server: {self.server_id}] Could not find IP for {self.bridge_interface}, skipping socat.")

        with open(self.paths["pid_file"], "w") as pf:
            pf.write(f"{process.pid}\n{port}\n")
            if socat_process:
                pf.write(f"{socat_process.pid}\n")

        return port, process.pid

    def wait_for_server(self, timeout: int = 180) -> bool:
        print(f"[INFO] [Server: {self.server_id}] Waiting for llama-server", end="", flush=True)
        elapsed: int = 0
        while elapsed < timeout:
            try:
                if self.server_pid is not None:
                    os.kill(self.server_pid, 0)
                else:
                    return False
            except OSError as e:
                if e.errno == errno.ESRCH:
                    print(f"\n[ERROR] [Server: {self.server_id}] Process died during startup.")
                    return False
                # Other OSErrors (like permission issues) can be ignored and handled by health check

            try:
                # Check health using curl
                result: subprocess.CompletedProcess[str] = subprocess.run(
                    ["curl", "-sf", f"http://127.0.0.1:{self.port}/health"],
                    capture_output=True, text=True
                )
                if result.stdout and '"status":"ok"' in result.stdout:
                    print(" [OK]", flush=True)
                    return True
            except Exception:
                pass

            time.sleep(2)
            elapsed += 2
            print(".", end="", flush=True)

        print(f"\n[ERROR] [Server: {self.server_id}] Timed out waiting for llama-server")

        log_file: Path = self.paths["log_file"]
        if log_file.exists():
            print(f"[INFO] [Server: {self.server_id}] Last 20 lines of {log_file}:")
            try:
                with open(log_file, 'r') as f:
                    lines: List[str] = f.readlines()
                    last_lines: List[str] = lines[-20:] if len(lines) > 20 else lines
                    for line in last_lines:
                        print(f"  {line.strip()}")
            except Exception as e:
                print(f"[ERROR] [Server: {self.server_id}] Could not read log file: {e}")
        return False

    def start(self) -> int:
        self._ensure_models_downloaded()
        server_flags: List[str] = self._get_server_flags()

        with open(self.paths["ref_count_lock"], "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)

            ref_count: int = 0
            if self.paths["ref_count_file"].exists():
                try:
                    ref_count = int(self.paths["ref_count_file"].read_text().strip())
                except ValueError:
                    ref_count = 0

            if ref_count > 0:
                # Check if existing process is actually running
                is_running: bool = False
                if self.paths["pid_file"].exists():
                    with open(self.paths["pid_file"], "r") as pf:
                        lines: List[str] = pf.readlines()
                        if len(lines) >= 2:
                            try:
                                pid_str: str = lines[0].strip()
                                port_str: str = lines[1].strip()
                                self.server_pid = int(pid_str)
                                self.port = int(port_str)
                                if self.server_pid is not None:
                                    os.kill(self.server_pid, 0) # Check if running
                                    is_running = True
                            except (OSError, ValueError, IndexError):
                                pass

                if not is_running:
                    print(f"[WARN] [Server: {self.server_id}] Server is supposed to be running but process is not found. Cleaning up stale files...")
                    self._stop_completely()
                    ref_count = 0
                else:
                    ref_count += 1

            if ref_count == 0:
                self.port, self.server_pid = self._start_server_process(server_flags)
                if not self.wait_for_server():
                    self._stop_completely()
                    raise Exception(f"Failed to start server {self.server_id}")
                ref_count = 1

            self.paths["ref_count_file"].write_text(str(ref_count))
        return self.port if self.port is not None else -1

    def stop(self) -> None:
        with open(self.paths["ref_count_lock"], "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)

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
    container_runtime: str = validate_environment(LLAMA_BIN)

    config_path: Path = REPO_ROOT / "pi-home" / ".pi" / "agent" / "models.json"
    if not config_path.exists():
         print(f"[ERROR] Config file not found: {config_path}")
         sys.exit(1)

    with open(config_path, 'r') as file:
        data: Dict[str, Any] = json.load(file)
        server_configs: List[Dict[str, Any]] = [
            val for val in data["providers"].values()
            if isinstance(val, dict) and "serverCustomParameters" in val
        ]


    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        with ExitStack() as stack:
            servers: List[Server] = []
            for config in server_configs:
                server = Server(
                    config["serverCustomParameters"],
                    MODELS_DIR,
                    LLAMA_BIN,
                    BRIDGE_INTERFACE,
                    LLAMA_SERVER_LOCK_DIR,
                    REPO_ROOT
                )
                stack.enter_context(server)
                servers.append(server)

            # 2. Run container
            llama_ports_json: str = json.dumps(
                [{"cp":server.config["port-in-container"],"hp":server.port} for server in servers]
            )
            container_cmd: List[str] = [
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
                "--env", f"LLAMA_PORTS={llama_ports_json}",
                IMAGE_TAG,
                *sys.argv[1:]
            ]

            result: subprocess.CompletedProcess = subprocess.run(container_cmd)
            if result.returncode != 0:
                sys.exit(result.returncode)

    except SystemExit:
        # ExitStack will have already cleaned up servers due to the 'with' block
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")
        raise e

if __name__ == "__main__":
    main()

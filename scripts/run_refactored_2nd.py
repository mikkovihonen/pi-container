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

# Add scripts directory to sys.path so we can import from util
sys.path.append(str(Path(__file__).resolve().parent))
from util import load_dotenv

# ─── Constants ─────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", os.getcwd()))
DOTENV_PATH = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

IMAGE_TAG = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
LLAMA_BIN = os.environ.get("LLAMA_BIN") or shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
MODELS_DIR = REPO_ROOT / "models"
LLAMA_SERVER_LOCK_DIR = REPO_ROOT / ".llama-server.locks"
BRIDGE_INTERFACE = os.environ.get("BRIDGE_INTERFACE", "bridge100")

# ─── Utility Functions ─────────────────────────────────────────────────────

def validate_environment(llama_bin):
    if not os.path.exists(llama_bin):
        print(f"[ERROR] llama-server not found at {llama_bin}. Install via: brew install llama.cpp")
        sys.exit(1)
    
    if shutil.which("hf") is None:
        print("[ERROR] hf not found. Install via: pip install huggingface_hub[cli]")
        sys.exit(1)

    if shutil.which("socat") is None:
        print("[ERROR] socat not found. Install via: brew install socat")
        sys.exit(1)

    runtime = None
    if shutil.which("docker") is not None:
        runtime = "docker"
    elif shutil.which("podman") is not None:
        runtime = "podman"
    
    if runtime is None:
        print("[ERROR] No supported container runtime found (docker or podman).")
        sys.exit(1)
        
    return runtime

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def stop_process_gracefully(pid, name):
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
    def __init__(self, label: str, repo: str, directory: Path, file: Path, models_dir: Path):
        self.label = label
        self.repo = repo
        self.directory = directory
        self.file = file
        self.models_dir = models_dir
        self.path = models_dir / directory / file

    def download(self):
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
    def __init__(self, config, models_dir, llama_bin, bridge_interface, lock_dir, repo_root):
        self.config = config
        self.server_id = config["id"]
        self.models_dir = models_dir
        self.llama_bin = llama_bin
        self.bridge_interface = bridge_interface
        self.lock_dir = lock_dir
        self.repo_root = repo_root
        self.port = None
        self.server_pid = None
        self.socat_pid = None
        self.models = {}

        # Paths
        server_lock_dir = self.lock_dir / self.server_id
        self.paths = {
            "lock_dir": server_lock_dir,
            "download_lock": server_lock_dir / ".model_download.lock",
            "ref_count_lock": server_lock_dir / ".llama_server_refcount.lock",
            "ref_count_file": server_lock_dir / ".llama_server_refcount",
            "pid_file": server_lock_dir / ".llama_server.pid",
            "log_file": self.repo_root / "logs" / self.server_id / "llama-server.log"
        }

        # Initialize models
        hf_models_conf = self.config.get("hf-models", {})
        for label, model_conf in hf_models_conf.items():
            self.models[label] = Model(
                label,
                model_conf["repo"],
                model_conf["dir"],
                model_conf["file"],
                self.models_dir
            )

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _ensure_models_downloaded(self):
        self.paths["download_lock"].parent.mkdir(exist_ok=True, parents=True)
        with open(self.paths["download_lock"], "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            for model in self.models.values():
                model.download()

    def _get_server_flags(self):
        flags = list(map(str, self.config["flags"]))
        flags.extend(["--alias", self.server_id])

        main_model = self.models.get("main")
        if main_model:
            flags.extend(["--model", str(main_model.path)])
        else:
            raise ValueError(f"[{self.server_id}] No main model defined in config.")

        draft_model = self.models.get("draft")
        if draft_model:
            flags.extend(["--model-draft", str(draft_model.path)])

        return flags

    def _get_bridge_ip(self):
        try:
            # Get the output of ifconfig for the specific interface
            result = subprocess.check_output(['ifconfig', self.bridge_interface], text=True)
            for line in result.splitlines():
                if 'inet ' in line:
                    return line.split()[1]
        except (subprocess.CalledProcessError, IndexError):
            return None
        return None

    def _stop_completely(self):
        if self.paths["pid_file"].exists():
            try:
                with open(self.paths["pid_file"], "r") as pf:
                    lines = pf.readlines()
                    if len(lines) >= 1:
                        server_pid = int(lines[0].strip())
                        stop_process_gracefully(server_pid, f"llama-server {self.server_id}")
                    if len(lines) >= 3:
                        try:
                            socat_pid = int(lines[2].strip())
                            stop_process_gracefully(socat_pid, f"socat for {self.server_id}")
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

    def _start_server_process(self, server_flags: list[str]):
        port = get_free_port()
        print(f"[INFO] [Server: {self.server_id}] Allocated port {port}")

        self.paths["log_file"].parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.llama_bin,
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-file", str(self.paths["log_file"]),
            *server_flags
        ]
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        bridge_ip = self._get_bridge_ip()
        socat_process = None
        if bridge_ip:
            socat_cmd = [
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

    def wait_for_server(self, timeout=180):
        print(f"[INFO] [Server: {self.server_id}] Waiting for llama-server", end="", flush=True)
        elapsed = 0
        while elapsed < timeout:
            # 1. Check if the process is still running
            try:
                os.kill(self.server_pid, 0)
            except OSError as e:
                if e.errno == errno.ESRCH:
                    print(f"\n[ERROR] [Server: {self.server_id}] Process died during startup.")
                    return False
                # Other OSErrors (like permission issues) can be ignored and handled by health check
            
            # 2. Check if the service is responding to HTTP
            try:
                # Check health using curl
                result = subprocess.run(
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
        return False

    def start(self):
        self._ensure_models_downloaded()
        server_flags = self._get_server_flags()

        with open(self.paths["ref_count_lock"], "a") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)

            ref_count = 0
            if self.paths["ref_count_file"].exists():
                try:
                    ref_count = int(self.paths["ref_count_file"].read_text().strip())
                except ValueError:
                    ref_count = 0

            if ref_count > 0:
                # Check if existing process is actually running
                is_running = False
                if self.paths["pid_file"].exists():
                    with open(self.paths["pid_file"], "r") as pf:
                        lines = pf.readlines()
                        if len(lines) >= 2:
                            try:
                                self.server_pid = int(lines[0].strip())
                                self.port = int(lines[1].strip())
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
            
            # If ref_count is 0, we must start a new process
            if ref_count == 0:
                self.port, self.server_pid = self._start_server_process(server_flags)
                if not self.wait_for_server():
                    self._stop_completely()
                    raise Exception(f"Failed to start server {self.server_id}")
                ref_count = 1
            
            self.paths["ref_count_file"].write_text(str(ref_count))
        return self.port

    def stop(self):
        with open(self.paths["ref_count_lock"], "a") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)

            ref_count = 0
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

def main():
    container_runtime = validate_environment(LLAMA_BIN)

    config_path = REPO_ROOT / "pi-home" / ".pi" / "agent" / "models.json"
    if not config_path.exists():
         print(f"[ERROR] Config file not found: {config_path}")
         sys.exit(1)

    with open(config_path, 'r') as file:
        data = json.load(file)
        server_configs = [
            val for val in data["providers"].values()
            if isinstance(val, dict) and "serverCustomParameters" in val
        ]

    try:
        with ExitStack() as stack:
            servers = []
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
            llama_ports_json = json.dumps(
                [{"cp":server.config["port-in-container"],"hp":server.port} for server in servers]
            )
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
                "--env", f"LLAMA_PORTS={llama_ports_json}",
                IMAGE_TAG,
                *sys.argv[1:]
            ]

            result = subprocess.run(container_cmd)
            sys.exit(result.returncode)

    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")
        raise e

if __name__ == "__main__":
    main()

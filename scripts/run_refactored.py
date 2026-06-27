import os
import sys
import subprocess
import socket
import time
import fcntl
import signal
import json
from pathlib import Path

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
LLAMA_BIN = os.environ.get("LLAMA_BIN", "/opt/homebrew/bin/llama-server")
MODELS_DIR = REPO_ROOT / "models"
LLAMA_SERVER_LOCK_DIR = REPO_ROOT / ".llama-server.locks"
BRIDGE_INTERFACE = os.environ.get("BRIDGE_INTERFACE", "bridge100")

# ─── Utility Functions ─────────────────────────────────────────────────────

def download_if_missing(label: str, repo: str, directory: Path, file: Path):
    path = MODELS_DIR / directory / file
    if path.exists():
        print(f"[{label}] Found: {path}")
        return

    print(f"[{label}] Downloading {file} from {repo} ...")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Using 'hf' command as in the original script
        subprocess.run(["hf", "download", repo, str(file), "--local-dir", str(MODELS_DIR / directory)], check=True)
        print(f"[{label}] Done.")
    except subprocess.CalledProcessError as e:
        print(f"[{label}] Failed to download: {e}")
        sys.exit(1)

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def wait_for_server(port, timeout=180):
    print("Waiting for llama-server", end="", flush=True)
    elapsed = 0
    while elapsed < timeout:
        try:
            # Check health using curl
            result = subprocess.run(
                ["curl", "-sf", f"http://127.0.0.1:{port}/health"],
                capture_output=True, text=True
            )
            if result.stdout and '"status":"ok"' in result.stdout:
                print(" ready!", flush=True)
                return True
        except Exception:
            pass

        time.sleep(2)
        elapsed += 2
        print(".", end="", flush=True)

    print("\nTimed out waiting for llama-server")
    return False

def get_bridge_ip(interface='bridge100'):
    try:
        # Get the output of ifconfig for the specific interface
        result = subprocess.check_output(['ifconfig', interface], text=True)
        for line in result.splitlines():
            if 'inet ' in line:
                return line.split()[1]
    except (subprocess.CalledProcessError, IndexError):
        return None
    return None

def stop_process_gracefully(pid, name):
    if pid is None:
        return
    print(f"Stopping {name} (pid {pid}) ...")
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

def start_server_process(server_id: str, server_flags: list[str], llama_bin: str, bridge_interface: str, llama_log: Path, pid_file: Path):
    port = get_free_port()
    print(f"Allocated port {port} for {server_id}")

    llama_log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        llama_bin,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-file", str(llama_log),
        *server_flags
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

    bridge_ip = get_bridge_ip(bridge_interface)
    socat_process = None
    if bridge_ip:
        socat_cmd = [
            "socat",
            f"TCP-LISTEN:{port},fork,reuseaddr,bind={bridge_ip}",
            f"TCP:127.0.0.1:{port}"
        ]
        try:
            socat_process = subprocess.Popen(socat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"socat started (pid {socat_process.pid}) listening on {bridge_ip}:{port}")
        except Exception as e:
            print(f"Warning: Failed to start socat: {e}")
    elif bridge_interface:
        print(f"Warning: Could not find IP for {bridge_interface}, skipping socat.")

    with open(pid_file, "w") as pf:
        pf.write(f"{process.pid}\n{port}\n")
        if socat_process:
            pf.write(f"{socat_process.pid}\n")

    return port, process.pid

def validate_environment(llama_bin):
    if not os.path.exists(llama_bin):
        print(f"llama-server not found at {llama_bin} — install with: brew install llama.cpp")
        sys.exit(1)
    try:
        hf_check = subprocess.run(["which", "hf"], capture_output=True)
        if hf_check.returncode != 0:
            print("hf not found — install with: pip install huggingface_hub[cli]")
            sys.exit(1)
    except Exception as e:
        raise e

# ─── Server Manager ────────────────────────────────────────────────────────

class ServerManager:
    def __init__(self, models_json_path, models_dir, llama_bin, bridge_interface, lock_dir):
        self.models_json_path = models_json_path
        self.models_dir = models_dir
        self.llama_bin = llama_bin
        self.bridge_interface = bridge_interface
        self.lock_dir = lock_dir
        self.active_servers_config = []

    def _get_paths(self, server_id):
        server_lock_dir = self.lock_dir / server_id
        return {
            "lock_dir": server_lock_dir,
            "download_lock": server_lock_dir / ".model_download.lock",
            "ref_count_lock": server_lock_dir / ".llama_server_refcount.lock",
            "ref_count_file": server_lock_dir / ".llama_server_refcount",
            "pid_file": server_lock_dir / ".llama_server.pid",
            "log_file": REPO_ROOT / "logs" / server_id / "llama-server.log"
        }

    def _ensure_models_downloaded(self, server_id, hf_models):
        paths = self._get_paths(server_id)
        paths["download_lock"].parent.mkdir(exist_ok=True, parents=True)
        with open(paths["download_lock"], "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            for (model, model_conf) in hf_models.items():
                download_if_missing(model, model_conf["repo"], model_conf["dir"], model_conf["file"])

    def _stop_server_completely(self, server_id):
        paths = self._get_paths(server_id)
        if paths["pid_file"].exists():
            try:
                with open(paths["pid_file"], "r") as pf:
                    lines = pf.readlines()
                    if len(lines) >= 1:
                        server_pid = int(lines[0].strip())
                        stop_process_gracefully(server_pid, f"llama-server {server_id}")
                    if len(lines) >= 3:
                        try:
                            socat_pid = int(lines[2].strip())
                            stop_process_gracefully(socat_pid, f"socat for {server_id}")
                        except (ValueError, IndexError):
                            pass
            except Exception as e:
                print(f"Error during complete stop of {server_id}: {e}")
            finally:
                paths["pid_file"].unlink(missing_ok=True)
                paths["ref_count_file"].unlink(missing_ok=True)
                paths["ref_count_lock"].unlink(missing_ok=True)
                paths["download_lock"].unlink(missing_ok=True)
                try:
                    paths["lock_dir"].rmdir()
                except Exception as e:
                    print(f"Could not remove lock directory for {server_id}: {e}")

    def setup_servers(self):
        if not self.models_json_path.exists():
            return []
        
        with open(self.models_json_path, 'r') as file:
            data = json.load(file)
            self.active_servers_config = [
                val for val in data["providers"].values()
                if isinstance(val, dict) and "serverCustomParameters" in val
            ]

        for server in self.active_servers_config:
            params = server["serverCustomParameters"]
            server_id = params["id"]
            paths = self._get_paths(server_id)

            server_flags = list(map(str, params["flags"]))
            server_flags.extend(["--alias", server_id])
            hf_models = params["hf-models"]
            
            try:
                server_flags.extend(["--model", self.models_dir / hf_models["main"]["dir"] / hf_models["main"]["file"]])
            except Exception:
                print(f"[{server_id}] No main model defined, skipping.")
                continue

            try:
                server_flags.extend(["--model-draft", self.models_dir / hf_models["draft"]["dir"] / hf_models["draft"]["file"]])
            except Exception:
                pass

            self._ensure_models_downloaded(server_id, hf_models)

            with open(paths["ref_count_lock"], "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)

                ref_count = 0
                if paths["ref_count_file"].exists():
                    try:
                        ref_count = int(paths["ref_count_file"].read_text().strip())
                    except ValueError:
                        ref_count = 0

                if ref_count == 0:
                    port, _ = start_server_process(
                        server_id, server_flags, self.llama_bin, self.bridge_interface, paths["log_file"], paths["pid_file"]
                    )
                    if not wait_for_server(port):
                        self._stop_server_completely(server_id)
                        sys.exit(1)
                    ref_count = 1
                else:
                    if not paths["pid_file"].exists():
                        print(f"Error: Server {server_id} is supposed to be running but PID file is missing.")
                        sys.exit(1)

                    with open(paths["pid_file"], "r") as pf:
                        lines = pf.readlines()
                        if len(lines) < 2:
                            print(f"Error: Malformed PID file for {server_id}.")
                            sys.exit(1)
                        server_pid = int(lines[0].strip())
                        port = int(lines[1].strip())

                    try:
                        os.kill(server_pid, 0)
                    except OSError:
                        print(f"Error: Server process for {server_id} in PID file is not running. Cleaning up...")
                        self._stop_server_completely(server_id)
                        sys.exit(1)
                    
                    ref_count += 1

                paths["ref_count_file"].write_text(str(ref_count))
                server["hostPort"] = port

    def release_servers(self):
        for server in self.active_servers_config:
            server_id = server["serverCustomParameters"]["id"]
            paths = self._get_paths(server_id)

            with open(paths["ref_count_lock"], "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)

                ref_count = 0
                if paths["ref_count_file"].exists():
                    try:
                        ref_count = int(paths["ref_count_file"].read_text().strip())
                    except ValueError:
                        ref_count = 0

                ref_count -= 1

                if ref_count <= 0:
                    self._stop_server_completely(server_id)
                else:
                    paths["ref_count_file"].write_text(str(ref_count))

# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    validate_environment(LLAMA_BIN)
    manager = ServerManager(
        REPO_ROOT / "pi-home" / ".pi" / "agent" / "models.json",
        MODELS_DIR,
        LLAMA_BIN,
        BRIDGE_INTERFACE,
        LLAMA_SERVER_LOCK_DIR
    )

    try:
        manager.setup_servers()
        local_lama_servers = manager.active_servers_config

        container_cmd = [
            "container", "run",
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
                    [{"cp":server["serverCustomParameters"]["port-in-container"],"hp":server["hostPort"]} for server in local_lama_servers]
                )
            }",
            IMAGE_TAG,
            *sys.argv[1:]
        ]

        result = subprocess.run(container_cmd)
        sys.exit(result.returncode)

    except Exception as e:
        print(f"An error occurred: {e}")
        raise e
    finally:
        manager.release_servers()

if __name__ == "__main__":
    main()

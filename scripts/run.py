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

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", os.getcwd()))
DOTENV_PATH = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

IMAGE_TAG = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")
LLAMA_BIN = os.environ.get("LLAMA_BIN", "/opt/homebrew/bin/llama-server")
MODELS_DIR = REPO_ROOT / "models"
LLAMA_SERVER_LOCK_DIR = REPO_ROOT / ".llama-server.locks"

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

def start_server(server_id: str, server_flags: list[str]):

    SERVER_PID_FILE = REPO_ROOT / ".llama-server.locks" / server_id / ".llama_server.pid"
    LLAMA_LOG = REPO_ROOT / "logs" / server_id / "llama-server.log"

    port = get_free_port()
    print(f"Allocated port {port} for {server_id}")

    LLAMA_LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        LLAMA_BIN,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-file", str(LLAMA_LOG),
        *server_flags
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

    bridge_ip = get_bridge_ip('bridge100')
    socat_cmd = [
        "socat",
        f"TCP-LISTEN:{port},fork,reuseaddr,bind={bridge_ip}",
        f"TCP:127.0.0.1:{port}"
    ]
    socat_process = None

    try:
        socat_process = subprocess.Popen(socat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"socat started (pid {socat_process.pid}) listening on {bridge_ip}:{port}")
    except Exception as e:
        print(f"Warning: Failed to start socat: {e}")

    with open(SERVER_PID_FILE, "w") as pf:
        pf.write(f"{process.pid}\n{port}\n")
        if socat_process:
            pf.write(f"{socat_process.pid}\n")

    print(f"llama-server {server_id} started (pid {process.pid}) — log: {LLAMA_LOG}")
    return port, process.pid

def stop_server(server_id: str):
    SERVER_PID_FILE = REPO_ROOT / ".llama-server.locks" / server_id / ".llama_server.pid"

    if SERVER_PID_FILE.exists():
        try:
            with open(SERVER_PID_FILE, "r") as pf:
                lines = pf.readlines()
                if len(lines) >= 1:
                    pid = int(lines[0].strip())
                    print(f"Stopping {server_id} (pid {pid}) ...")
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
                    if len(lines) >= 3:
                        try:
                            socat_pid = int(lines[2].strip())
                            print(f"Stopping socat (pid {socat_pid}) ...")
                            try:
                                os.kill(socat_pid, signal.SIGTERM)
                                for _ in range(10):
                                    try:
                                        os.kill(socat_pid, 0)
                                        time.sleep(0.5)
                                    except OSError:
                                        break
                                else:
                                    os.kill(socat_pid, signal.SIGKILL)
                            except OSError:
                                pass
                        except (ValueError, IndexError):
                            pass
            SERVER_PID_FILE.unlink(missing_ok=True)
        except Exception as e:
            print(f"Error stopping server: {e}")

def main():
    if not os.path.exists(LLAMA_BIN):
        print(f"llama-server not found at {LLAMA_BIN} — install with: brew install llama.cpp")
        sys.exit(1)
    try:
        hf_check = subprocess.run(["which", "hf"], capture_output=True)
        if hf_check.returncode != 0:
            print("hf not found — install with: pip install huggingface_hub[cli]")
            sys.exit(1)
    except Exception as e:
        raise e

    local_lama_servers = []

    try:
        with open(REPO_ROOT / "pi-home" / ".pi" / "agent" / "models.json", 'r') as file:
            data = json.load(file)

            local_lama_servers = [
                val for val in data["providers"].values()
                if isinstance(val, dict) and "serverCustomParameters" in val
            ]

            for index, server in enumerate(local_lama_servers):
                SERVER_ID = server["serverCustomParameters"]["id"]
                DOWNLOAD_LOCK_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID / ".model_download.lock"
                SERVER_LOCK_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID / ".llama_server_refcount.lock"
                SERVER_REF_COUNT_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID / ".llama_server_refcount"
                SERVER_PID_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID/ ".llama_server.pid"

                server_flags = list(map(str,server["serverCustomParameters"]["flags"]))
                server_flags.extend(["--alias", SERVER_ID])
                hf_models = server["serverCustomParameters"]["hf-models"]
                try:
                    server_flags.extend(["--model", MODELS_DIR / hf_models["main"]["dir"] / hf_models["main"]["file"]])
                except:
                    print("no main model defined")
                    exit()
                try:
                    server_flags.extend(["--model-draft", MODELS_DIR / hf_models["draft"]["dir"] / hf_models["draft"]["file"]])
                except:
                    pass

                DOWNLOAD_LOCK_FILE.parent.mkdir(exist_ok=True, parents=True)
                with open(DOWNLOAD_LOCK_FILE, "w") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    for (model, model_conf) in hf_models.items():
                        download_if_missing(model, model_conf["repo"], model_conf["dir"], model_conf["file"])

                port = None
                server_pid = None

                with open(SERVER_LOCK_FILE, "w") as lock_f:
                    fcntl.flock(lock_f, fcntl.LOCK_EX)

                    if SERVER_REF_COUNT_FILE.exists():
                        try:
                            ref_count = int(SERVER_REF_COUNT_FILE.read_text().strip())
                        except ValueError:
                            ref_count = 0
                    else:
                        ref_count = 0

                    if ref_count == 0:
                        port, server_pid = start_server(SERVER_ID, server_flags)
                        if not wait_for_server(port):
                            stop_server(SERVER_ID)
                            SERVER_REF_COUNT_FILE.unlink(missing_ok=True)
                            sys.exit(1)
                        ref_count = 1
                    else:
                        if not SERVER_PID_FILE.exists():
                            print("Error: Server is supposed to be running but PID file is missing.")
                            sys.exit(1)

                        with open(SERVER_PID_FILE, "r") as pf:
                            lines = pf.readlines()
                            if len(lines) < 2:
                                print("Error: Malformed PID file.")
                                sys.exit(1)
                            server_pid = int(lines[0].strip())
                            port = int(lines[1].strip())

                        # Check if process is alive
                        try:
                            os.kill(server_pid, 0)
                        except OSError:
                            print("Error: Server process in PID file is not running. Cleaning up...")
                            SERVER_PID_FILE.unlink(missing_ok=True)
                            SERVER_REF_COUNT_FILE.unlink(missing_ok=True)
                            sys.exit(1)

                        ref_count += 1

                    SERVER_REF_COUNT_FILE.write_text(str(ref_count))
                    local_lama_servers[index]["hostPort"] = port


    except Exception as e:
        raise e


    try:
        # 3. Run container
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

    finally:
        for server in local_lama_servers:
            SERVER_ID = server["serverCustomParameters"]["id"]
            DOWNLOAD_LOCK_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID / ".model_download.lock"
            SERVER_LOCK_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID / ".llama_server_refcount.lock"
            SERVER_REF_COUNT_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID / ".llama_server_refcount"
            SERVER_PID_FILE = LLAMA_SERVER_LOCK_DIR / SERVER_ID/ ".llama_server.pid"

            # 4. Decrement ref count
            with open(SERVER_LOCK_FILE, "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)

                if SERVER_REF_COUNT_FILE.exists():
                    try:
                        ref_count = int(SERVER_REF_COUNT_FILE.read_text().strip())
                    except ValueError:
                        ref_count = 0
                else:
                    ref_count = 0

                ref_count -= 1

                if ref_count <= 0:
                    stop_server(SERVER_ID)
                    ref_count = 0
                    if SERVER_REF_COUNT_FILE.exists():
                        SERVER_REF_COUNT_FILE.unlink()
                    if SERVER_PID_FILE.exists():
                        SERVER_PID_FILE.unlink()
                    if SERVER_LOCK_FILE.exists():
                        SERVER_LOCK_FILE.unlink()
                    if DOWNLOAD_LOCK_FILE.exists():
                        DOWNLOAD_LOCK_FILE.unlink()
                    if SERVER_LOCK_FILE.parent.exists():
                        try:
                            SERVER_LOCK_FILE.parent.rmdir()
                        except Exception as e:
                            print(f"lock directory removal failed: {e}")
                else:
                    SERVER_REF_COUNT_FILE.write_text(str(ref_count))

if __name__ == "__main__":
    main()
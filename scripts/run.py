import os
import sys
import subprocess
import socket
import time
import fcntl
import signal
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
LLAMA_LOG = REPO_ROOT / "logs/llama-server.log"
SERVER_REF_COUNT_FILE = REPO_ROOT / ".llama_server_refcount"
SERVER_LOCK_FILE = REPO_ROOT / ".llama_server_refcount.lock"
SERVER_PID_FILE = REPO_ROOT / ".llama_server.pid"
DOWNLOAD_LOCK_FILE = REPO_ROOT / ".model_download.lock"

# ─── Model locations & HuggingFace source ────────────────────────────────────

MAIN_MODEL_DIR = MODELS_DIR / "gemma-4-26B-A4B-it-qat-GGUF"
MAIN_MODEL = MAIN_MODEL_DIR / "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf"
MAIN_MODEL_HF_REPO = os.environ.get("MAIN_MODEL_HF_REPO", "unsloth/gemma-4-26B-A4B-it-qat-GGUF")
MAIN_MODEL_HF_FILE = os.environ.get("MAIN_MODEL_HF_FILE", "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf")

DRAFT_MODEL_DIR = MODELS_DIR / "gemma4-26b-mtp"
DRAFT_MODEL = DRAFT_MODEL_DIR / "mtp-gemma-4-26B-A4B-it.gguf"
DRAFT_MODEL_HF_REPO = os.environ.get("DRAFT_MODEL_HF_REPO", "unsloth/gemma-4-26B-A4B-it-qat-GGUF")
DRAFT_MODEL_HF_FILE = os.environ.get("DRAFT_MODEL_HF_FILE", "mtp-gemma-4-26B-A4B-it.gguf")

# ─── Settings ────────────────────────────────────

MODEL_ID = os.environ.get("MODEL_ID", "gemma-4-26b-a4b-it-qat-ud-q4_k_xl")
MODEL_CTX_SIZE = int(os.environ.get("MODEL_CTX_SIZE", 131072))
MODEL_COMPACTION_THRESHOLD = int(os.environ.get("MODEL_COMPACTION_THRESHOLD", 128000))
MODEL_BATCH_SIZE = int(os.environ.get("MODEL_BATCH_SIZE", 4096))
MODEL_TEMPERATURE = float(os.environ.get("MODEL_TEMPERATURE", 0.2))
MODEL_TOP_P = float(os.environ.get("MODEL_TOP_P", 0.95))
MODEL_GPU_LAYERS = int(os.environ.get("MODEL_GPU_LAYERS", 999))
MODEL_THREADS = int(os.environ.get("MODEL_THREADS", 8))
MODEL_THREADS_BATCH = int(os.environ.get("MODEL_THREADS_BATCH", 8))
MODEL_SPEC_TYPE = os.environ.get("MODEL_SPEC_TYPE", "draft-mtp")
MODEL_SPEC_DRAFT_N_MAX = int(os.environ.get("MODEL_SPEC_DRAFT_N_MAX", 4))
MODEL_SPEC_DRAFT_N_MIN = int(os.environ.get("MODEL_SPEC_DRAFT_N_MIN", 1))
MODEL_PARALLEL = int(os.environ.get("MODEL_PARALLEL", 1))
MODEL_U_BATCH_SIZE = int(os.environ.get("MODEL_U_BATCH_SIZE", 512))
MODEL_FLASH_ATTN = os.environ.get("MODEL_FLASH_ATTN", "on")
MODEL_CTX_CHECKPOINTS = int(os.environ.get("MODEL_CTX_CHECKPOINTS", 32))
MODEL_CHECKPOINT_MIN_STEP = int(os.environ.get("MODEL_CHECKPOINT_MIN_STEP", 256))
MODEL_PRIO = int(os.environ.get("MODEL_PRIO", 2))

def download_if_missing(label, path, directory, repo, file):
    if path.exists():
        print(f"[{label}] Found: {path}")
        return

    if not repo:
        print(f"[{label}] Model missing and HF repo not configured: {path}")
        print("  Set the matching *_HF_REPO variable in this script.")
        sys.exit(1)

    print(f"[{label}] Downloading {file} from {repo} ...")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        # Using 'hf' command as in the original script
        subprocess.run(["hf", "download", repo, file, "--local-dir", str(directory)], check=True)
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

def start_server():
    port = get_free_port()
    print(f"Allocated port {port} for llama-server")
    LLAMA_LOG.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        LLAMA_BIN,
        "--model", str(MAIN_MODEL),
        "--alias", MODEL_ID,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--ctx-size", str(MODEL_CTX_SIZE),
        "--n-gpu-layers", str(MODEL_GPU_LAYERS),
        "--threads", str(MODEL_THREADS),
        "--threads-batch", str(MODEL_THREADS_BATCH),
        "--model-draft", str(DRAFT_MODEL),
        "--spec-type", MODEL_SPEC_TYPE,
        "--spec-draft-n-max", str(MODEL_SPEC_DRAFT_N_MAX),
        "--spec-draft-n-min", str(MODEL_SPEC_DRAFT_N_MIN),
        "--parallel", str(MODEL_PARALLEL),
        "--batch-size", str(MODEL_BATCH_SIZE),
        "--ubatch-size", str(MODEL_U_BATCH_SIZE),
        "--flash-attn", str(MODEL_FLASH_ATTN),
        "--no-mmap",
        "--mlock",
        "--kv-offload",
        "--jinja",
        "--ctx-checkpoints", str(MODEL_CTX_CHECKPOINTS),
        "--checkpoint-min-step", str(MODEL_CHECKPOINT_MIN_STEP),
        "--prio", str(MODEL_PRIO),
        "--log-file", str(LLAMA_LOG)
    ]

    # Redirect stdout/stderr to null as in bash script
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Start socat to forward traffic from bridge to localhost
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

    print(f"llama-server started (pid {process.pid}) — log: {LLAMA_LOG}")
    return port, process.pid

def stop_server():
    if SERVER_PID_FILE.exists():
        try:
            with open(SERVER_PID_FILE, "r") as pf:
                lines = pf.readlines()
                if len(lines) >= 1:
                    pid = int(lines[0].strip())
                    print(f"Stopping llama-server (pid {pid}) ...")
                    try:
                        os.kill(pid, signal.SIGTERM)
                        # Wait a bit for it to shut down
                        for _ in range(10):
                            try:
                                os.kill(pid, 0)
                                time.sleep(0.5)
                            except OSError:
                                break
                        else:
                            # Still running, try SIGKILL
                            os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass

                    # Stop socat if it was running
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
    # Check dependencies
    if not os.path.exists(LLAMA_BIN):
        print(f"llama-server not found at {LLAMA_BIN} — install with: brew install llama.cpp")
        sys.exit(1)

    try:
        hf_check = subprocess.run(["which", "hf"], capture_output=True)
        if hf_check.returncode != 0:
            print("hf not found — install with: pip install huggingface_hub[cli]")
            sys.exit(1)
    except Exception:
        pass

    # 1. Download models (with lock)
    with open(DOWNLOAD_LOCK_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        download_if_missing("main", MAIN_MODEL, MAIN_MODEL_DIR, MAIN_MODEL_HF_REPO, MAIN_MODEL_HF_FILE)
        download_if_missing("draft", DRAFT_MODEL, DRAFT_MODEL_DIR, DRAFT_MODEL_HF_REPO, DRAFT_MODEL_HF_FILE)

    # 2. Manage llama-server lifecycle
    port = None
    server_pid = None
    is_first_instance = False

    # Use a lock to manage the reference count
    with open(SERVER_LOCK_FILE, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)

        # Read ref count
        if SERVER_REF_COUNT_FILE.exists():
            try:
                ref_count = int(SERVER_REF_COUNT_FILE.read_text().strip())
            except ValueError:
                ref_count = 0
        else:
            ref_count = 0

        if ref_count == 0:
            # First instance: start server
            port, server_pid = start_server()
            if not wait_for_server(port):
                stop_server()
                SERVER_REF_COUNT_FILE.unlink(missing_ok=True)
                sys.exit(1)
            ref_count = 1
            is_first_instance = True
        else:
            # Subsequent instance: find existing server
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
                print("Error: Server process in PID file is not running. Cleaning up and restarting...")
                SERVER_PID_FILE.unlink(missing_ok=True)
                SERVER_REF_COUNT_FILE.unlink(missing_ok=True)
                sys.exit(1)

            ref_count += 1

        SERVER_REF_COUNT_FILE.write_text(str(ref_count))


    try:
        # 3. Run container
        env_extras = ["--env", "UPDATE_MODEL=1"] if is_first_instance else []
        container_cmd = [
            "container", "run",
            "--rm",
            "--interactive",
            "--tty",
            "--volume", f"{REPO_ROOT}/pi-home:/home/pi",
            "--volume", f"{PROJECT_DIR}:/workspace",
            "--tmpfs", "/home/pi/.cache",
            "--tmpfs", "/home/pi/.local",
            "--tmpfs", "/home/pi/.venv",
            "--tmpfs", "/home/pi/.pi/agent/bin",
            "--workdir", "/workspace",
            "--env", f"LLAMA_PORT={port}",
            "--env", f"MODEL_ID={MODEL_ID}",
            "--env", f"MODEL_CTX_WINDOW={MODEL_CTX_SIZE}",
            "--env", f"MODEL_COMPACTION_THRESHOLD={MODEL_COMPACTION_THRESHOLD}",
            "--env", f"MODEL_MAX_TOKENS={MODEL_BATCH_SIZE}",
            "--env", f"MODEL_TEMPERATURE={MODEL_TEMPERATURE}",
            "--env", f"MODEL_TOP_P={MODEL_TOP_P}",
            *env_extras,
            IMAGE_TAG,
            *sys.argv[1:]
        ]

        result = subprocess.run(container_cmd)
        sys.exit(result.returncode)

    finally:
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
                stop_server()
                ref_count = 0
                if SERVER_REF_COUNT_FILE.exists():
                    SERVER_REF_COUNT_FILE.unlink()
                if SERVER_PID_FILE.exists():
                    SERVER_PID_FILE.unlink()
            else:
                SERVER_REF_COUNT_FILE.write_text(str(ref_count))

if __name__ == "__main__":
    main()
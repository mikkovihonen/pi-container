import sys
sys.dont_write_bytecode = True

import os
import subprocess
from pathlib import Path
from util import load_dotenv

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DOTENV_PATH = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

PI_IMAGE_TAG = os.environ.get("PI_IMAGE_TAG", "pi-coding-agent:local")
PROXY_IMAGE_TAG = os.environ.get("PROXY_IMAGE_TAG", "pi-coding-agent-proxy:local")

def build_proxy():
    print(f"Building proxy image: {PROXY_IMAGE_TAG}")
    cmd = [
        "container", "build",
        "--tag", PROXY_IMAGE_TAG,
        "--file", str(REPO_ROOT / "pi-coding-agent-proxy" / "Containerfile"),
        str(REPO_ROOT)
    ]
    subprocess.run(cmd, check=True)

def build_agent():
    print(f"Building agent image: {PI_IMAGE_TAG}")

    cmd = [
        "container", "build",
        "--tag", PI_IMAGE_TAG,
        "--file", str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
        str(REPO_ROOT)
    ]

    subprocess.run(cmd, check=True)

def main():
    try:
        build_proxy()
        build_agent()
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: 'container' command not found. Please ensure the container CLI is installed and in your PATH.")
        sys.exit(1)

if __name__ == "__main__":
    main()

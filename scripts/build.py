import os
import sys
import subprocess
from pathlib import Path
from util import load_dotenv

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DOTENV_PATH = REPO_ROOT / ".env"

load_dotenv(DOTENV_PATH)

IMAGE_TAG = os.environ.get("IMAGE_TAG", "pi-coding-agent:local")

def main():
    print(f"Building image: {IMAGE_TAG}")
    
    cmd = [
        "container", "build",
        "--tag", IMAGE_TAG,
        "--file", str(REPO_ROOT / "Containerfile"),
        str(REPO_ROOT)
    ]
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: 'container' command not found. Please ensure the container CLI is installed and in your PATH.")
        sys.exit(1)

if __name__ == "__main__":
    main()

import sys
sys.dont_write_bytecode = True

"""Model configuration dataclasses and the downloadable Model."""

import fcntl
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from huggingface_hub import hf_hub_download

from config import LLAMA_SERVER_LOCK_DIR

logger = logging.getLogger(__name__)


# ─── Configuration Dataclasses ───────────────────────────────────────────

@dataclass(frozen=True)
class ModelConfig:
    file_flag: str
    repo: str
    file: str
    directory: Path
    additional_server_flags: List[Any]
    sha256: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ModelConfig':
        return cls(
            file_flag=data["fileFlag"],
            repo=data["repo"],
            file=data["file"],
            directory=Path(data["dir"]),
            additional_server_flags=data.get("additionalServerFlags", []),
            sha256=data.get("sha256"),
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

    def _verify_sha256(self) -> None:
        """Verify the downloaded model file against its expected SHA256 hash.

        Raises:
            ValueError: If the hash does not match or no expected hash is set.
        """
        if not self.config.sha256:
            logger.warning(
                f"[Model: {self.label}] No SHA256 checksum configured for "
                f"{self.config.repo}:{self.config.file}. Skipping integrity check."
            )
            return

        logger.info(f"[Model: {self.label}] Verifying SHA256 checksum...")
        sha256_hash = hashlib.sha256()
        with self.path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        computed = sha256_hash.hexdigest()

        if computed.lower() != self.config.sha256.lower():
            raise ValueError(
                f"[Model: {self.label}] SHA256 mismatch for {self.config.file}: "
                f"expected {self.config.sha256}, got {computed}. "
                f"The model file may be corrupted or tampered with."
            )
        logger.info(f"[Model: {self.label}] SHA256 verification passed.")

    def download(self) -> None:
        if self.path.exists():
            logger.info(f"[Model: {self.label}] Found existing model: {self.path}")
            return

        # Create a shared lock directory for all models
        shared_lock_dir: Path = LLAMA_SERVER_LOCK_DIR / "model_download"
        shared_lock_dir.mkdir(exist_ok=True, parents=True)

        # Create a unique lock filename based on repo and file
        model_id = hashlib.sha256(f"{self.config.repo}:{self.config.file}".encode()).hexdigest()
        lock_file_path = shared_lock_dir / f"{model_id}.lock"

        logger.info(f"[Model: {self.label}] Acquiring lock for download...")
        try:
            with lock_file_path.open("a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)

                # Re-check if exists after acquiring lock
                if self.path.exists():
                    logger.info(f"[Model: {self.label}] Found existing model after acquiring lock: {self.path}")
                    return

                logger.info(f"[Model: {self.label}] Downloading {self.config.file} from {self.config.repo}...")
                self.path.parent.mkdir(exist_ok=True, parents=True)
                hf_hub_download(
                    repo_id=self.config.repo,
                    filename=self.config.file,
                    local_dir=str(self.models_dir / self.config.directory),
                    local_dir_use_symlinks=False
                )
                logger.info(f"[Model: {self.label}] Download complete.")

            # Verify integrity after download
            self._verify_sha256()
        finally:
            lock_file_path.unlink(missing_ok=True)

    @staticmethod
    def cleanup_download_lock_dir(lock_dir: Path) -> None:
        """Remove the model download lock directory if it's empty (last run.py instance cleaned up)."""
        if lock_dir.exists():
            try:
                if not any(lock_dir.iterdir()):
                    lock_dir.rmdir()
                    logger.info(f"Removed empty model download lock directory: {lock_dir}")
                # Clean up parent .locks dir if it's now empty too
                parent = lock_dir.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                    logger.info(f"Removed empty lock directory: {parent}")
            except OSError:
                pass

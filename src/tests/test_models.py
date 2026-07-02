"""
Unit tests for src/models.py

Run with:
    python -m pytest src/tests/test_models.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Model, ModelConfig, ServerConfig

# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------


class TestModelConfig:
    def _make_dict(self, **overrides):
        base = {
            "fileFlag": "--model",
            "repo": "org/repo",
            "file": "model.gguf",
            "dir": "models",
            "additionalServerFlags": [],
            "sha256": None,
        }
        base.update(overrides)
        return base

    def test_basic_from_dict(self):
        mc = ModelConfig.from_dict(self._make_dict())
        assert mc.file_flag == "--model"
        assert mc.repo == "org/repo"
        assert mc.file == "model.gguf"
        assert mc.directory == Path("models")
        assert mc.additional_server_flags == []
        assert mc.sha256 is None

    def test_sha256_passed_through(self):
        sha = "abc123def456"
        mc = ModelConfig.from_dict(self._make_dict(sha256=sha))
        assert mc.sha256 == sha

    def test_sha256_none_when_missing(self):
        d = self._make_dict()
        del d["sha256"]
        mc = ModelConfig.from_dict(d)
        assert mc.sha256 is None

    def test_additional_flags_from_dict(self):
        d = self._make_dict(additionalServerFlags=["--n-gpu-layers", "35"])
        mc = ModelConfig.from_dict(d)
        assert mc.additional_server_flags == ["--n-gpu-layers", "35"]

    def test_additional_flags_default_empty(self):
        d = self._make_dict()
        del d["additionalServerFlags"]
        mc = ModelConfig.from_dict(d)
        assert mc.additional_server_flags == []

    def test_is_frozen(self):
        mc = ModelConfig.from_dict(self._make_dict())
        with pytest.raises(AttributeError):
            mc.file = "other"


# ---------------------------------------------------------------------------
# ServerConfig
# ---------------------------------------------------------------------------


class TestServerConfig:
    def test_from_dict_basic(self):
        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    "repo": "org/repo",
                    "file": "model.gguf",
                    "dir": "models",
                    "additionalServerFlags": [],
                }
            },
            "flags": ["--ctx-size", "4096"],
        }
        sc = ServerConfig.from_dict(data)
        assert "main" in sc.hf_models
        assert sc.hf_models["main"].repo == "org/repo"
        assert sc.flags == ["--ctx-size", "4096"]

    def test_from_dict_multiple_models(self):
        data = {
            "hfModels": {
                "main": {
                    "fileFlag": "--model",
                    "repo": "org/main",
                    "file": "main.gguf",
                    "dir": "models",
                    "additionalServerFlags": [],
                },
                "embedding": {
                    "fileFlag": "--embedding-model",
                    "repo": "org/embed",
                    "file": "embed.gguf",
                    "dir": "embedding",
                    "additionalServerFlags": [],
                },
            },
            "flags": [],
        }
        sc = ServerConfig.from_dict(data)
        assert "main" in sc.hf_models
        assert "embedding" in sc.hf_models
        assert sc.hf_models["embedding"].file == "embed.gguf"

    def test_from_dict_no_hf_models(self):
        sc = ServerConfig.from_dict({"flags": []})
        assert sc.hf_models == {}

    def test_from_dict_no_flags(self):
        data = {"hfModels": {}}
        sc = ServerConfig.from_dict(data)
        assert sc.flags == []

    def test_is_frozen(self):
        sc = ServerConfig.from_dict({"hfModels": {}, "flags": []})
        with pytest.raises(AttributeError):
            sc.flags = ["new"]


# ---------------------------------------------------------------------------
# ModelVerifySha256
# ---------------------------------------------------------------------------


class TestModelVerifySha256:
    def test_no_sha256_skips_verification(self):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": [],
                "sha256": None,
            }
        )
        model = Model(label="test", config=mc, models_dir=Path("/tmp"))
        # Should not raise
        with patch("models.logger") as mock_logger:
            model._verify_sha256()
        mock_logger.warning.assert_called()

    def test_matching_sha256_passes(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": [],
                "sha256": "abc123",
            }
        )
        Model(label="test", config=mc, models_dir=tmp_path)
        model_path = tmp_path / "models" / "model.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"test data")

        # Compute real SHA256
        import hashlib

        h = hashlib.sha256(b"test data").hexdigest()

        mc_with_hash = ModelConfig(
            file_flag=mc.file_flag,
            repo=mc.repo,
            file=mc.file,
            directory=mc.directory,
            additional_server_flags=mc.additional_server_flags,
            sha256=h,
        )
        model_with_hash = Model(label="test", config=mc_with_hash, models_dir=tmp_path)

        with patch("models.logger") as mock_logger:
            model_with_hash._verify_sha256()  # should not raise
        mock_logger.info.assert_called()

    def test_mismatched_sha256_raises(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": [],
                "sha256": "WRONG_HASH_VALUE_123456789012345678901234567890123456789012345678901234",
            }
        )
        model = Model(label="test", config=mc, models_dir=tmp_path)
        model_path = tmp_path / "models" / "model.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"test data")

        with pytest.raises(ValueError, match="SHA256 mismatch"):
            model._verify_sha256()


# ---------------------------------------------------------------------------
# ModelDownload
# ---------------------------------------------------------------------------


class TestModelDownload:
    def test_skips_existing_model(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": [],
                "sha256": None,
            }
        )
        model = Model(label="test", config=mc, models_dir=tmp_path)
        model_path = tmp_path / "models" / "model.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text("existing")

        with patch("models.logger") as mock_logger, patch("models.hf_hub_download") as mock_hf:
            model.download()
            mock_hf.assert_not_called()
            mock_logger.info.assert_called()

    def test_downloads_when_missing(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "models",
                "additionalServerFlags": [],
                "sha256": None,
            }
        )
        model = Model(label="test", config=mc, models_dir=tmp_path)

        with (
            patch("models.hf_hub_download") as mock_hf,
            patch("models.logger") as mock_logger,
            patch("models.hashlib") as mock_hashlib,
            patch("models.fcntl") as mock_fcntl,
            patch("models.LLAMA_SERVER_LOCK_DIR", tmp_path / "locks"),
        ):
            # Mock lock to be a context manager
            MagicMock()
            mock_fcntl.flock = MagicMock()

            mock_hashlib.sha256.return_value.hexdigest.return_value = "abc"

            model.download()
            mock_hf.assert_called_once()
            mock_logger.info.assert_called()

    def test_path_property(self, tmp_path):
        mc = ModelConfig.from_dict(
            {
                "fileFlag": "--model",
                "repo": "org/repo",
                "file": "model.gguf",
                "dir": "subdir",
                "additionalServerFlags": [],
                "sha256": None,
            }
        )
        model = Model(label="test", config=mc, models_dir=tmp_path)
        assert model.path == tmp_path / "subdir" / "model.gguf"


# ---------------------------------------------------------------------------
# ModelCleanupLockDir
# ---------------------------------------------------------------------------


class TestModelCleanupLockDir:
    def test_removes_empty_dir(self, tmp_path):
        lock_dir = tmp_path / "model_download"
        lock_dir.mkdir()
        with patch("models.logger"):
            Model.cleanup_download_lock_dir(lock_dir)
        assert not lock_dir.exists()

    def test_keeps_nonempty_dir(self, tmp_path):
        lock_dir = tmp_path / "model_download"
        lock_dir.mkdir()
        (lock_dir / "file.lock").write_text("data")
        with patch("models.logger"):
            Model.cleanup_download_lock_dir(lock_dir)
        assert lock_dir.exists()

    def test_handles_nonexistent_dir(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        with patch("models.logger"):
            Model.cleanup_download_lock_dir(nonexistent)  # should not raise

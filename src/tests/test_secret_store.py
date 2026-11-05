from __future__ import annotations

import sys
from unittest.mock import patch

import secret_store

sys.dont_write_bytecode = True

"""Unit tests for src/secret_store.py."""


def test_resolve_secret_from_env(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "env_secret_123")
    val = secret_store.resolve_secret("TEST_KEY")
    assert val == "env_secret_123"


def test_resolve_secret_from_keyring(monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    with patch("keyring.get_password", return_value="keyring_secret_456") as mock_get:
        val = secret_store.resolve_secret("TEST_KEY", service="my-service", account="my-account")
        assert val == "keyring_secret_456"
        mock_get.assert_called_once_with("my-service", "my-account")


def test_resolve_secret_defaults_account_to_var_name(monkeypatch):
    monkeypatch.delenv("OPPER_API_KEY", raising=False)
    with patch("keyring.get_password", return_value="opp_key_789") as mock_get:
        val = secret_store.resolve_secret("OPPER_API_KEY")
        assert val == "opp_key_789"
        mock_get.assert_called_once_with(secret_store.DEFAULT_KEYRING_SERVICE, "OPPER_API_KEY")


def test_resolve_secret_not_found(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with patch("keyring.get_password", return_value=None):
        val = secret_store.resolve_secret("MISSING_KEY")
        assert val is None


def test_resolve_secret_keyring_exception_handled(monkeypatch):
    monkeypatch.delenv("FAILING_KEY", raising=False)
    with patch("keyring.get_password", side_effect=RuntimeError("Keyring locked")):
        val = secret_store.resolve_secret("FAILING_KEY")
        assert val is None


def test_set_secret_success():
    with patch("keyring.set_password") as mock_set:
        res = secret_store.set_secret("MY_KEY", "secret_val", service="test-srv", account="acc1")
        assert res is True
        mock_set.assert_called_once_with("test-srv", "acc1", "secret_val")


def test_set_secret_failure():
    with patch("keyring.set_password", side_effect=RuntimeError("Access denied")):
        res = secret_store.set_secret("MY_KEY", "secret_val")
        assert res is False


def test_delete_secret_success():
    with patch("keyring.delete_password") as mock_del:
        res = secret_store.delete_secret("MY_KEY", service="test-srv", account="acc1")
        assert res is True
        mock_del.assert_called_once_with("test-srv", "acc1")


def test_delete_secret_failure():
    with patch("keyring.delete_password", side_effect=RuntimeError("Cannot delete")):
        res = secret_store.delete_secret("MY_KEY")
        assert res is False

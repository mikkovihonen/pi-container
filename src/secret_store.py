from __future__ import annotations

import sys

sys.dont_write_bytecode = True

"""Secret resolution module for host environment variables and OS keyring."""

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_KEYRING_SERVICE = "pi-container"


def get_keyring_secret(
    account: str,
    service: str = DEFAULT_KEYRING_SERVICE,
) -> str | None:
    """Retrieve a secret directly from the OS keyring without checking environment variables."""
    try:
        import keyring

        secret = keyring.get_password(service, account)
        if secret:
            logger.debug(f"Resolved secret from keyring (service='{service}', account='{account}')")
            return secret
    except Exception as e:
        logger.debug(f"Keyring lookup failed for service='{service}', account='{account}': {e}")

    return None


def resolve_secret(
    var_name: str,
    service: str = DEFAULT_KEYRING_SERVICE,
    account: str | None = None,
) -> str | None:
    """Resolve a secret from the host environment, falling back to the OS keyring.

    Precedence:
      1. Host environment variable (``os.environ[var_name]``).
      2. OS keyring entry (service=``service``, account=``account or var_name``).

    Returns the secret value, or ``None`` if not found in either store.
    """
    # 1. Environment variable
    env_val = os.environ.get(var_name)
    if env_val:
        return env_val

    # 2. OS Keyring
    try:
        import keyring

        account_name = account or var_name
        secret = keyring.get_password(service, account_name)
        if secret:
            logger.debug(f"Resolved secret '{var_name}' from keyring (service='{service}', account='{account_name}')")
            return secret
    except Exception as e:
        logger.debug(f"Keyring lookup failed for service='{service}': {e}")

    return None


def set_secret(
    var_name: str,
    value: str,
    service: str = DEFAULT_KEYRING_SERVICE,
    account: str | None = None,
) -> bool:
    """Store a secret in the user's OS keyring."""
    account_name = account or var_name
    try:
        import keyring

        keyring.set_password(service, account_name, value)
        logger.info(f"Stored secret '{var_name}' in keyring (service='{service}', account='{account_name}')")
        return True
    except Exception as e:
        logger.error(f"Failed to store secret in keyring: {e}")
        return False


def delete_secret(
    var_name: str,
    service: str = DEFAULT_KEYRING_SERVICE,
    account: str | None = None,
) -> bool:
    """Delete a secret from the user's OS keyring."""
    account_name = account or var_name
    try:
        import keyring

        keyring.delete_password(service, account_name)
        logger.info(f"Deleted secret '{var_name}' from keyring (service='{service}', account='{account_name}')")
        return True
    except Exception as e:
        logger.error(f"Failed to delete secret from keyring: {e}")
        return False

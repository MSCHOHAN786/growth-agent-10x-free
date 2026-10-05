"""Fernet encryption helpers for secrets at rest.

YouTube OAuth refresh tokens (and any other long-lived secret) are encrypted
before they are stored in Supabase. The encryption key itself is never stored
in code — it lives in the ``ENCRYPTION_KEY`` environment variable.

First-time setup: run ``python -c "from core.crypto import generate_key; generate_key()"``
to print a fresh key, then set ``ENCRYPTION_KEY`` in your environment.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Lazily import cryptography so this module stays importable without it.
_Fernet = None


def _fernet():
    """Import Fernet on first use, raising a helpful error if missing."""
    global _Fernet
    if _Fernet is None:
        try:
            from cryptography.fernet import Fernet as _RealFernet

            _Fernet = _RealFernet
        except ImportError as exc:
            raise RuntimeError(
                "The 'cryptography' package is required for token encryption. "
                "Install it with: pip install cryptography"
            ) from exc
    return _Fernet


def generate_key() -> str:
    """Generate a fresh Fernet key for first-time setup.

    Prints instructions for storing it, then returns the key as a string.

    Returns:
        The new key, URL-safe base64-encoded.
    """
    Fernet = _fernet()
    key = Fernet.generate_key().decode("utf-8")
    print("Generated ENCRYPTION_KEY. Set it in your environment:\n")
    print(f"    export ENCRYPTION_KEY='{key}'\n")
    print("For GitHub Actions, add it as a repository secret named ENCRYPTION_KEY.")
    logger.info("Generated a new ENCRYPTION_KEY")
    return key


def get_encryption_key() -> bytes:
    """Read the Fernet key from the ENCRYPTION_KEY environment variable.

    Returns:
        The key as bytes, ready for :class:`cryptography.fernet.Fernet`.

    Raises:
        RuntimeError: If ENCRYPTION_KEY is not set, with setup instructions.
        ValueError: If the value is not a valid Fernet key.
    """
    raw = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError(
            "ENCRYPTION_KEY environment variable is not set. "
            "Generate one with: python -c \"from core.crypto import generate_key; "
            "generate_key()\" and set it as the ENCRYPTION_KEY env var."
        )
    Fernet = _fernet()
    try:
        Fernet(raw.encode("utf-8"))  # validates the key format
    except Exception as exc:
        raise ValueError(
            "ENCRYPTION_KEY is not a valid Fernet key (must be 32-byte "
            "URL-safe base64). Generate a new one with generate_key()."
        ) from exc
    return raw.encode("utf-8")


def encrypt_token(plaintext: str, key: Optional[bytes] = None) -> str:
    """Encrypt a secret (e.g. a YouTube OAuth refresh token).

    Args:
        plaintext: The secret to encrypt. Must be a non-empty string.
        key: Optional explicit key bytes. Defaults to ENCRYPTION_KEY env.

    Returns:
        The Fernet ciphertext as a UTF-8 string.

    Raises:
        ValueError: If plaintext is empty or not a string.
    """
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("plaintext must be a non-empty string")
    Fernet = _fernet()
    key_bytes = key if key is not None else get_encryption_key()
    token = Fernet(key_bytes).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    logger.info("Encrypted a token (%d chars)", len(plaintext))
    return token


def decrypt_token(ciphertext: str, key: Optional[bytes] = None) -> str:
    """Decrypt a ciphertext produced by :func:`encrypt_token`.

    Args:
        ciphertext: The Fernet ciphertext string.
        key: Optional explicit key bytes. Defaults to ENCRYPTION_KEY env.

    Returns:
        The original plaintext secret.

    Raises:
        ValueError: If ciphertext is empty or not a string.
        cryptography.fernet.InvalidToken: If the token is invalid/expired
            or the key does not match.
    """
    if not isinstance(ciphertext, str) or not ciphertext:
        raise ValueError("ciphertext must be a non-empty string")
    Fernet = _fernet()
    key_bytes = key if key is not None else get_encryption_key()
    plaintext = Fernet(key_bytes).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    logger.info("Decrypted a token successfully")
    return plaintext

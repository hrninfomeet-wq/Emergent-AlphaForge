"""Fernet encryption helper for storing Upstox access tokens at rest."""
import os
from functools import lru_cache
from cryptography.fernet import Fernet


def fernet_startup_warning() -> str | None:
    """Return a safe startup warning when durable token encryption is unavailable.

    Do not call ``get_fernet`` here: doing so would create the transient fallback
    key while merely checking configuration.  The warning intentionally never
    includes a configured key or any token value.
    """
    key = os.environ.get("FERNET_KEY")
    if not isinstance(key, str) or not key.strip():
        return (
            "FERNET_KEY is not configured; stored Upstox OAuth tokens written by this "
            "process cannot be decrypted after restart. Set FERNET_KEY in backend/.env."
        )
    try:
        Fernet(key.encode("utf-8"))
    except (TypeError, ValueError):
        return (
            "FERNET_KEY is invalid; stored Upstox OAuth tokens may be unreadable. "
            "Replace it with a valid Fernet key in backend/.env before using OAuth."
        )
    return None


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    key = os.environ.get("FERNET_KEY")
    if not key:
        # Auto-generate a transient key (NOT persisted across restarts). Tokens encrypted with
        # this key will be unrecoverable after restart. Set FERNET_KEY in .env for production.
        key = Fernet.generate_key().decode()
        os.environ["FERNET_KEY"] = key
    return Fernet(key.encode("utf-8"))


def encrypt_str(plain: str) -> str:
    if plain is None:
        return None
    return get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_str(cipher: str) -> str:
    if cipher is None:
        return None
    return get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")

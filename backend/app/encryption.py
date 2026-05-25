"""Fernet encryption helper for storing Upstox access tokens at rest."""
import os
from functools import lru_cache
from cryptography.fernet import Fernet


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

"""Encrypt stored credentials with a local key (Fernet)."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _fernet() -> Fernet:
    if not config.KEY_PATH.exists():
        key = Fernet.generate_key()
        config.KEY_PATH.write_bytes(key)
        try:
            os.chmod(config.KEY_PATH, 0o600)
        except OSError:
            pass
    return Fernet(config.KEY_PATH.read_bytes())


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""

"""Encrypts each organization's credentials (its Telegram bot token, its calendar key) before they reach the database.

Envelope encryption: a master key held only in the server's environment encrypts a data key per organization, and that data key
encrypts the organization's secrets. Every ciphertext is bound to the organization and the secret's name (as AES-GCM associated
data), so moving a stored value to another organization or another name makes it fail to open instead of silently working.
Versioned prefixes (`v1.`) leave room to rotate the master key later without guessing which key a value used.
"""
from __future__ import annotations

import base64
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"
NONCE_BYTES = 12
KEY_BYTES = 32


class VaultError(Exception):
    """The master key is missing or wrong, or a stored value is damaged or does not belong where it was found."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_master_key() -> str:
    """A new master key, to be put in SECRETS_MASTER_KEY. Keep it out of the database and out of version control."""
    return _b64(os.urandom(KEY_BYTES))


class Vault:
    def __init__(self, master_key: str):
        try:
            raw = _unb64(master_key.strip())
        except (ValueError, TypeError) as exc:
            raise VaultError("SECRETS_MASTER_KEY is not valid base64") from exc
        if len(raw) != KEY_BYTES:
            raise VaultError(f"SECRETS_MASTER_KEY must be {KEY_BYTES} bytes ({KEY_BYTES * 4 // 3 + 1} base64 characters)")
        self._master = AESGCM(raw)

    # ---- the per-organization data key ----

    def new_data_key(self, org_id: UUID | str) -> tuple[bytes, str]:
        """A fresh data key and the form of it to store (encrypted with the master key)."""
        key = os.urandom(KEY_BYTES)
        return key, self._seal(self._master, key, f"key:{org_id}")

    def open_data_key(self, org_id: UUID | str, wrapped: str) -> bytes:
        return self._open(self._master, wrapped, f"key:{org_id}")

    # ---- an organization's secrets ----

    def seal(self, data_key: bytes, org_id: UUID | str, name: str, plaintext: str) -> str:
        return self._seal(AESGCM(data_key), plaintext.encode(), f"secret:{org_id}:{name}")

    def open(self, data_key: bytes, org_id: UUID | str, name: str, ciphertext: str) -> str:
        return self._open(AESGCM(data_key), ciphertext, f"secret:{org_id}:{name}").decode()

    # ---- internals ----

    @staticmethod
    def _seal(box: AESGCM, data: bytes, context: str) -> str:
        nonce = os.urandom(NONCE_BYTES)
        return f"{VERSION}.{_b64(nonce + box.encrypt(nonce, data, context.encode()))}"

    @staticmethod
    def _open(box: AESGCM, stored: str, context: str) -> bytes:
        version, _, body = stored.partition(".")
        if version != VERSION or not body:
            raise VaultError("unknown or damaged stored value")
        try:
            raw = _unb64(body)
            return box.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode())
        except (InvalidTag, ValueError) as exc:
            raise VaultError("a stored value could not be opened: wrong key, or it was moved from another place") from exc

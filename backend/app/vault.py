"""Encrypts each organization's credentials (its Telegram bot token, its calendar key) before they reach the database.

Envelope encryption: a master key held only in the server's environment encrypts a data key per organization, and that data key
encrypts the organization's secrets. Every ciphertext is bound to the organization and the secret's name (as AES-GCM associated
data), so moving a stored value to another organization or another name makes it fail to open instead of silently working.
A data key sealed by the master key is stored as `v2.<key id>.<nonce + ciphertext>`; the key id is a short fingerprint of the master
key, so the vault knows which of its keys opens it. During a rotation the server holds several master keys (SECRETS_MASTER_KEYS, newest
first): it seals with the newest and opens with whichever a value names. Values written before key ids existed (`v1.`) are opened by
trying each key; AES-GCM refuses the wrong ones. An agency's secrets are sealed with its data key, not the master key, so a rotation
re-seals one data key per agency and never touches the secrets (scripts/rotate_master_key.py).
"""
from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Sequence
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"        # secrets sealed with a data key, and data keys sealed before key ids existed
KEYED = "v2"          # a data key sealed with the master key named after it
NONCE_BYTES = 12
KEY_BYTES = 32


class VaultError(Exception):
    """The master key is missing or wrong, or a stored value is damaged or does not belong where it was found."""


class UnknownKey(VaultError):
    """A stored value names a master key this server was not given."""

    def __init__(self, key_id: str):
        super().__init__(f"a stored value was sealed with master key {key_id}, which is not loaded")
        self.key_id = key_id


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def key_id(master_key: str) -> str:
    """The fingerprint a master key is known by: safe to show and to log, useless for opening anything."""
    return _b64(hashlib.sha256(b"proppulse master key id:" + _parse(master_key)).digest()[:6])


def _parse(master_key: str) -> bytes:
    try:
        raw = _unb64(master_key.strip())
    except (ValueError, TypeError) as exc:
        raise VaultError("SECRETS_MASTER_KEY is not valid base64") from exc
    if len(raw) != KEY_BYTES:
        raise VaultError(f"SECRETS_MASTER_KEY must be {KEY_BYTES} bytes ({KEY_BYTES * 4 // 3 + 1} base64 characters)")
    return raw


def stored_key_id(wrapped: str) -> str | None:
    """The master key a stored data key names, or None for a `v1.` value, which names none."""
    version, _, rest = wrapped.partition(".")
    if version == KEYED:
        kid, dot, body = rest.partition(".")
        if dot and kid and body:
            return kid
    if version == VERSION and rest:
        return None
    raise VaultError("unknown or damaged stored value")


def generate_master_key() -> str:
    """A new master key, to be put in SECRETS_MASTER_KEY. Keep it out of the database and out of version control."""
    return _b64(os.urandom(KEY_BYTES))


class Vault:
    def __init__(self, master_keys: str | Sequence[str]):
        """One master key, or several newest first (a rotation in progress): the first seals, any of them opens."""
        keys = [master_keys] if isinstance(master_keys, str) else list(master_keys)
        if not keys:
            raise VaultError("no master key was given")
        self._masters: dict[str, AESGCM] = {}
        for k in keys:
            self._masters.setdefault(key_id(k), AESGCM(_parse(k)))
        self.current_key_id = next(iter(self._masters))

    @property
    def key_ids(self) -> list[str]:
        return list(self._masters)

    # ---- the per-organization data key ----

    def new_data_key(self, org_id: UUID | str) -> tuple[bytes, str]:
        """A fresh data key and the form of it to store (encrypted with the newest master key)."""
        key = os.urandom(KEY_BYTES)
        return key, self._wrap(key, org_id)

    def open_data_key(self, org_id: UUID | str, wrapped: str) -> bytes:
        return self._unwrap(org_id, wrapped)[0]

    def opening_key_id(self, org_id: UUID | str, wrapped: str) -> str:
        """Which loaded master key opens this data key (for a `v1.` value, found by trying). Raises when none does."""
        return self._unwrap(org_id, wrapped)[1]

    def rewrap(self, org_id: UUID | str, wrapped: str) -> str:
        """The same data key, sealed with the newest master key. The secrets it seals are untouched."""
        return self._wrap(self.open_data_key(org_id, wrapped), org_id)

    def _wrap(self, key: bytes, org_id: UUID | str) -> str:
        body = self._seal(self._masters[self.current_key_id], key, f"key:{org_id}").partition(".")[2]
        return f"{KEYED}.{self.current_key_id}.{body}"

    def _unwrap(self, org_id: UUID | str, wrapped: str) -> tuple[bytes, str]:
        context = f"key:{org_id}"
        kid = stored_key_id(wrapped)
        if kid is not None:
            box = self._masters.get(kid)
            if box is None:
                raise UnknownKey(kid)
            return self._open(box, f"{VERSION}.{wrapped.split('.', 2)[2]}", context), kid
        for candidate, box in self._masters.items():  # a v1 value names no key: the one that opens it is its key
            try:
                return self._open(box, wrapped, context), candidate
            except VaultError:
                continue
        raise VaultError("a stored value could not be opened with any loaded master key: wrong key, or it was moved from another place")

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

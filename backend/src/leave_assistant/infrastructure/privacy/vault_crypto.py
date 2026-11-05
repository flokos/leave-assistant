"""Vault cryptography (ADR-0017): HMAC-SHA256 lookup keys and AES-256-GCM originals, keys from the environment."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


def decode_key(value: str | None, *, name: str) -> bytes | None:
    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError(f"{name} must be base64") from exc
    if len(raw) != 32:
        raise ValueError(f"{name} must decode to 32 bytes (openssl rand -base64 32)")
    return raw


class VaultCrypto:
    def __init__(self, hmac_key: bytes | None, kek: bytes | None) -> None:
        self._hmac_key = hmac_key
        self._kek = kek

    @property
    def configured(self) -> bool:
        return self._hmac_key is not None and self._kek is not None

    def _require(self) -> tuple[bytes, bytes]:
        if self._hmac_key is None or self._kek is None:
            raise RuntimeError("vault keys are not configured")
        return self._hmac_key, self._kek

    def hmac(self, tenant_id: UUID, key: str) -> bytes:
        hmac_key, _ = self._require()
        return hmac.new(hmac_key, f"{tenant_id}\x1f{key}".encode(), hashlib.sha256).digest()

    def encrypt(self, tenant_id: UUID, value: str) -> bytes:
        _, kek = self._require()
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(kek).encrypt(nonce, value.encode(), str(tenant_id).encode())

    def decrypt(self, tenant_id: UUID, data: bytes) -> str:
        _, kek = self._require()
        nonce, ciphertext = data[:NONCE_BYTES], data[NONCE_BYTES:]
        return AESGCM(kek).decrypt(nonce, ciphertext, str(tenant_id).encode()).decode()

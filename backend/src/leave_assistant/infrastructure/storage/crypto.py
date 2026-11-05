"""Envelope encryption for supporting documents (ADR-0016): per-file AES-256-GCM DEK wrapped by `APP_KEK` (AES-KW)."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap

NONCE_BYTES = 12


class EnvelopeCipher:
    def __init__(self, kek: bytes | None) -> None:
        self._kek = kek

    @property
    def configured(self) -> bool:
        return self._kek is not None

    def encrypt(self, data: bytes, *, aad: bytes) -> tuple[bytes, bytes]:
        if self._kek is None:
            raise RuntimeError("APP_KEK is not configured")
        dek = AESGCM.generate_key(256)
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = nonce + AESGCM(dek).encrypt(nonce, data, aad)
        return ciphertext, aes_key_wrap(self._kek, dek)

    def decrypt(self, ciphertext: bytes, wrapped_dek: bytes, *, aad: bytes) -> bytes:
        if self._kek is None:
            raise RuntimeError("APP_KEK is not configured")
        dek = aes_key_unwrap(self._kek, wrapped_dek)
        nonce, body = ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:]
        return AESGCM(dek).decrypt(nonce, body, aad)

"""OWNS: AEAD for the .trace body; AAD binds the plaintext header (spec 06 §2).

Production is XChaCha20-Poly1305 via libsodium — SodiumCrypto below stays a
stub until that dependency is admitted. StdlibAead is the zero-dependency
stand-in: HMAC-SHA256 counter-mode keystream + encrypt-then-MAC over
(header | nonce | ciphertext). It is a real authenticated cipher built from
stdlib primitives, NOT a toy XOR — but it is slower than sodium and not
misuse-hardened; the swap is mechanical behind the same three-method port.
No cipher agility: the format byte pins exactly one suite per version.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Tuple


class AuthFailure(Exception):
    pass


def hkdf(key: bytes, info: bytes, length: int = 32,
         salt: bytes = b"tracemem-hkdf-v1") -> bytes:
    """RFC 5869 HKDF-SHA256, stdlib only."""
    prk = hmac.new(salt, key, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


class StdlibAead:
    NONCE_LEN = 16
    TAG_LEN = 32

    def __init__(self, key: bytes) -> None:
        if len(key) < 16:
            raise ValueError("key must be >= 16 bytes")
        self._enc_key = hkdf(key, b"enc")
        self._mac_key = hkdf(key, b"mac")

    def _keystream(self, nonce: bytes, n: int) -> bytes:
        out, i = b"", 0
        while len(out) < n:
            out += hmac.new(self._enc_key, nonce + i.to_bytes(8, "big"),
                            hashlib.sha256).digest()
            i += 1
        return out[:n]

    def encrypt(self, header: bytes, plaintext: bytes) -> Tuple[bytes, bytes]:
        nonce = os.urandom(self.NONCE_LEN)
        ks = self._keystream(nonce, len(plaintext))
        ct = bytes(a ^ b for a, b in zip(plaintext, ks))
        tag = hmac.new(self._mac_key, header + nonce + ct, hashlib.sha256).digest()
        return nonce, ct + tag

    def decrypt(self, header: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
        if len(ciphertext) < self.TAG_LEN:
            raise AuthFailure("truncated ciphertext")
        ct, tag = ciphertext[:-self.TAG_LEN], ciphertext[-self.TAG_LEN:]
        want = hmac.new(self._mac_key, header + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, want):
            raise AuthFailure("MAC mismatch: wrong key, or header/body tampered")
        ks = self._keystream(nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, ks))


class SodiumCrypto:
    """Production suite: XChaCha20-Poly1305 with AAD = header (spec 06 §2).

    Backed by PyCryptodome (already pinned in the venv — the libsodium
    construction admitted with zero new installs; WO-012 decision, board
    2026-07-19). 24-byte random nonce = the misuse margin XChaCha exists for.
    Same three-method port as StdlibAead; the header is cryptographically
    bound, so a tampered route fails decryption, not just a checksum.
    """

    NONCE_LEN = 24
    TAG_LEN = 16

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("XChaCha20-Poly1305 needs a 32-byte key")
        self._key = key

    def encrypt(self, header: bytes, plaintext: bytes) -> Tuple[bytes, bytes]:
        from Crypto.Cipher import ChaCha20_Poly1305
        nonce = os.urandom(self.NONCE_LEN)
        c = ChaCha20_Poly1305.new(key=self._key, nonce=nonce)
        c.update(header)
        ct, tag = c.encrypt_and_digest(plaintext)
        return nonce, ct + tag

    def decrypt(self, header: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
        from Crypto.Cipher import ChaCha20_Poly1305
        if len(ciphertext) < self.TAG_LEN:
            raise AuthFailure("truncated ciphertext")
        ct, tag = ciphertext[:-self.TAG_LEN], ciphertext[-self.TAG_LEN:]
        c = ChaCha20_Poly1305.new(key=self._key, nonce=nonce)
        c.update(header)
        try:
            return c.decrypt_and_verify(ct, tag)
        except ValueError:
            raise AuthFailure("AEAD verify failed: wrong key, or header/body tampered")

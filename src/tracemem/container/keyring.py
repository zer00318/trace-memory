"""OWNS: the user-sovereign master key (spec 06 §3, K2).

Generated on device; lives in the injected keychain port (macOS Keychain in
production, a dict in tests). NO export API: no method ever returns the
master in plaintext — the QR device-add path emits only a sealed enrollment
payload redeemable with a short one-time code shown on the source device.
Purpose keys come from HKDF, so compromising one never exposes another.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from tracemem.container.crypto import AuthFailure, hkdf

_SLOT = "tracemem.master.v1"


class KeyMissing(RuntimeError):
    """No master key where one must already exist."""


class Keyring:
    def __init__(self, keychain, create: bool = True) -> None:
        """`create=False` for every reader of an EXISTING memory: a missing
        key there means the wrong keychain was asked, and minting a fresh
        random master would plant a wrong key and then read nothing
        (found 2026-10-20: the falsification tool asked the legacy item the
        key had been moved out of)."""
        self._keychain = keychain
        master = keychain.get(_SLOT)
        if master is None:
            if not create:
                raise KeyMissing("no master key in this keychain; open the "
                                 "memory through the signed app (./trace)")
            master = os.urandom(32)                  # born on device, never elsewhere
            keychain.set(_SLOT, master)
        self.__master = master                       # name-mangled; no getter exists

    def derive(self, purpose: str, length: int = 32) -> bytes:
        """Stable purpose keys: derive('transport') is the sync identity (A6)."""
        return hkdf(self.__master, b"purpose:" + purpose.encode(), length)

    # -- device add: sealed QR + one-time code, never plaintext export --

    def device_add_qr(self):
        """Returns (qr_payload_str, one_time_code). The QR alone is useless;
        the code alone is useless; production shows them on different surfaces."""
        code = base64.b32encode(os.urandom(5)).decode().rstrip("=")
        wrap = hkdf(code.encode(), b"enroll-wrap", 32)
        sealed = bytes(a ^ b for a, b in zip(self.__master, wrap))
        tag = hmac.new(wrap, sealed, hashlib.sha256).hexdigest()[:16]
        payload = json.dumps({"v": 1, "sealed": base64.b64encode(sealed).decode(),
                              "tag": tag})
        return payload, code

    @classmethod
    def redeem_qr(cls, payload: str, code: str, keychain) -> "Keyring":
        d = json.loads(payload)
        sealed = base64.b64decode(d["sealed"])
        wrap = hkdf(code.encode(), b"enroll-wrap", 32)
        tag = hmac.new(wrap, sealed, hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(tag, d["tag"]):
            raise AuthFailure("wrong enrollment code")
        keychain.set(_SLOT, bytes(a ^ b for a, b in zip(sealed, wrap)))
        return cls(keychain)

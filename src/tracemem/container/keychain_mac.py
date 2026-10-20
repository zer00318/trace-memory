"""OWNS: the macOS Keychain implementation of the keyring's storage port
(WO-012b, map: Yours alone / Keychain residence).

`Keyring` takes an injected keychain with `get(slot)` / `set(slot, bytes)`.
Tests inject a dict. This is the production one.

WHY ctypes AGAINST Security.framework AND NOT THE `security` CLI. The CLI
form is `security add-generic-password ... -w <secret>`, which puts the
master key on a command line — visible in `ps` to every process on the
machine for as long as the call runs, and potentially in a shell history.
For the one secret in this product that unlocks a person's entire life,
that is not an acceptable few milliseconds. The framework call passes the
bytes in memory.

WHY THE OLDER SecKeychain* API rather than SecItemAdd. SecItemAdd needs a
CoreFoundation dictionary assembled through ctypes, which is a great deal of
fragile pointer work whose failure mode is a silent misconfiguration — the
worst possible shape for the code that decides where the master key lives.
The SecKeychain* family takes plain C strings and lengths. It is deprecated
and it is legible, and legibility wins here.

THE COST OF THAT CHOICE, stated because it is real: the SecKeychain* API
writes to the FILE-BASED login keychain rather than the modern data
protection keychain. Consequences —
  · No iCloud sync. That is what the brief asks for ("this-device-only, no
    iCloud sync"), and here it comes free rather than as a flag that could
    be set wrong.
  · No kSecAttrAccessible control. The item is protected by the login
    keychain's own unlock state, which on a normal Mac means it unlocks with
    the session. That satisfies "unlocks with session"; it does NOT give the
    finer-grained WhenUnlockedThisDeviceOnly guarantee.
If the finer control is ever required, the migration is to SecItemAdd with
kSecUseDataProtectionKeychain — and it is a migration, not a flag flip,
because items do not move between the two keychains by themselves.

WHAT IS NOT PROVEN (2026-07-22): "key survives reboot". That needs a
reboot. And this module's live path is deliberately NOT exercised by the
suite — writing to the founder's real login keychain as a side effect of
running tests is unacceptable, and it can raise an interactive unlock
prompt. `tests/unit/test_keychain_mac.py` covers the contract with a fake
and gates the live check behind TRACE_LIVE_KEYCHAIN=1.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional

SECURITY_PATH = "/System/Library/Frameworks/Security.framework/Security"

# The two OSStatus values this code must tell apart. Everything else is an
# error worth raising: a keyring that swallows failures hands out a fresh
# random master and silently orphans the store it was supposed to open.
_ERR_SEC_SUCCESS = 0
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_DUPLICATE_ITEM = -25299

DEFAULT_SERVICE = "com.trace.watcher"


class KeychainError(RuntimeError):
    """A keychain operation failed for a reason that is not 'absent'."""


def _load():
    lib = ctypes.CDLL(SECURITY_PATH)
    lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainItemDelete.restype = ctypes.c_int32
    lib.SecKeychainItemFreeContent.restype = ctypes.c_int32
    lib.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    return lib


class MacKeychain:
    """get/set over the login keychain, keyed by (service, slot).

    Deliberately the same two-method surface the tests' dict provides. The
    Keyring must not be able to tell which it got — that is what keeps the
    suite Keychain-free by construction rather than by discipline.
    """

    def __init__(self, service: str = DEFAULT_SERVICE, lib=None) -> None:
        self.service = service
        self._lib = lib or _load()

    # -- the port ----------------------------------------------------------
    def get(self, slot: str) -> Optional[bytes]:
        svc = self.service.encode()
        acct = slot.encode()
        length = ctypes.c_uint32(0)
        data = ctypes.c_void_p()

        status = self._lib.SecKeychainFindGenericPassword(
            None, len(svc), svc, len(acct), acct,
            ctypes.byref(length), ctypes.byref(data), None)

        if status == _ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(self._describe("read", slot, status))

        try:
            return ctypes.string_at(data, length.value)
        finally:
            # Always freed, even if string_at raises: the buffer belongs to
            # the framework and leaking it leaks key material into a page
            # this process no longer tracks.
            self._lib.SecKeychainItemFreeContent(None, data)

    def set(self, slot: str, value: bytes) -> None:
        svc = self.service.encode()
        acct = slot.encode()

        status = self._lib.SecKeychainAddGenericPassword(
            None, len(svc), svc, len(acct), acct,
            len(value), value, None)

        if status == _ERR_SEC_DUPLICATE_ITEM:
            # A master already exists for this slot. Overwriting is the
            # correct behaviour for the port contract, but it is the single
            # most destructive operation in this codebase — it orphans every
            # claim ever sealed with the old key — so it goes through its
            # own named method rather than happening quietly inside set().
            self._replace(svc, acct, value, slot)
            return
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(self._describe("write", slot, status))

    def delete(self, slot: str) -> bool:
        """Returns whether something was there. Present for tests and for
        uninstall ('leave the machine as found'); NOT called by Keyring."""
        svc = self.service.encode()
        acct = slot.encode()
        item = ctypes.c_void_p()
        status = self._lib.SecKeychainFindGenericPassword(
            None, len(svc), svc, len(acct), acct, None, None,
            ctypes.byref(item))
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            return False
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(self._describe("find-for-delete", slot, status))
        status = self._lib.SecKeychainItemDelete(item)
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(self._describe("delete", slot, status))
        return True

    # -- internals ---------------------------------------------------------
    def _replace(self, svc: bytes, acct: bytes, value: bytes, slot: str) -> None:
        item = ctypes.c_void_p()
        status = self._lib.SecKeychainFindGenericPassword(
            None, len(svc), svc, len(acct), acct, None, None,
            ctypes.byref(item))
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(self._describe("find-for-replace", slot, status))
        status = self._lib.SecKeychainItemModifyAttributesAndData(
            item, None, len(value), value)
        if status != _ERR_SEC_SUCCESS:
            raise KeychainError(self._describe("replace", slot, status))

    def _describe(self, op: str, slot: str, status: int) -> str:
        return ("keychain %s failed for slot %r on service %r: OSStatus %d. "
                "This is NOT 'no key yet' — that is reported as None. A "
                "keyring that swallowed this would mint a fresh random "
                "master and silently orphan the store it was meant to open."
                % (op, slot, self.service, status))

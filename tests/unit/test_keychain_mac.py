"""ORACLE for Keychain residence (09_yours, WO-012b).

PROOF: "Live: key survives reboot, unlocks with session; suite runs
Keychain-free."

THE SECOND CLAUSE IS ENFORCED HERE, and it is the one these tests can
actually settle. Writing to the founder's real login keychain as a side
effect of running the suite is unacceptable — it mutates their machine and
can raise an interactive unlock prompt — so every test below drives a fake
Security library. The live check is gated behind TRACE_LIVE_KEYCHAIN=1 and
is skipped by default.

The reboot clause cannot be tested by any test. It is recorded as unproven
on the board rather than approximated by something that looks like proof.

The tests that carry the most weight are the ones about NOT swallowing
errors. A keyring whose storage returns None on a failure it did not
understand will mint a fresh random master and orphan every claim ever
sealed with the old one — silently, and permanently. "Absent" and "broken"
must never collapse into the same return value.
"""
import ctypes
import os

import pytest

from tracemem.container.keychain_mac import (
    DEFAULT_SERVICE, KeychainError, MacKeychain,
)
from tracemem.container.keyring import Keyring

ITEM_NOT_FOUND = -25300
DUPLICATE_ITEM = -25299


class FakeSecurity:
    """A stand-in for Security.framework with the same OSStatus contract."""

    def __init__(self, items=None, fail_with=None):
        self.items = dict(items or {})
        self.fail_with = fail_with
        self.calls = []
        self.freed = 0
        self._buffers = {}

    # -- the four entry points the adapter uses ---------------------------
    def SecKeychainFindGenericPassword(self, _kc, svc_len, svc, acct_len,
                                       acct, length_p, data_p, item_p):
        self.calls.append(("find", svc[:svc_len], acct[:acct_len]))
        if self.fail_with is not None:
            return self.fail_with
        key = (svc[:svc_len], acct[:acct_len])
        if key not in self.items:
            return ITEM_NOT_FOUND
        value = self.items[key]
        if length_p is not None and data_p is not None:
            buf = ctypes.create_string_buffer(value, len(value))
            self._buffers[ctypes.addressof(buf)] = buf   # keep it alive
            length_p._obj.value = len(value)
            data_p._obj.value = ctypes.addressof(buf)
        if item_p is not None:
            item_p._obj.value = 1234
        return 0

    def SecKeychainAddGenericPassword(self, _kc, svc_len, svc, acct_len,
                                      acct, val_len, val, _item):
        self.calls.append(("add", svc[:svc_len], acct[:acct_len]))
        if self.fail_with is not None:
            return self.fail_with
        key = (svc[:svc_len], acct[:acct_len])
        if key in self.items:
            return DUPLICATE_ITEM
        self.items[key] = bytes(val[:val_len])
        return 0

    def SecKeychainItemModifyAttributesAndData(self, _item, _attrs, val_len, val):
        self.calls.append(("modify",))
        if self.fail_with is not None:
            return self.fail_with
        key = list(self.items)[0] if self.items else None
        if key is not None:
            self.items[key] = bytes(val[:val_len])
        return 0

    def SecKeychainItemDelete(self, _item):
        self.calls.append(("delete",))
        if self.fail_with is not None:
            return self.fail_with
        if self.items:
            self.items.pop(list(self.items)[0])
        return 0

    def SecKeychainItemFreeContent(self, _attrs, _data):
        self.freed += 1
        return 0


def kc(**kw):
    return MacKeychain(lib=FakeSecurity(**kw))


# -- the port contract -----------------------------------------------------
def test_a_missing_slot_reads_as_none_not_an_error():
    """'No key yet' is the normal first-boot state and must be ordinary."""
    assert kc().get("tracemem.master.v1") is None


def test_set_then_get_round_trips():
    keychain = kc()
    keychain.set("slot", b"\x00\x01secret\xff")
    assert keychain.get("slot") == b"\x00\x01secret\xff"


def test_binary_keys_survive_intact():
    """The master is 32 random bytes, not text. A path that stringified it
    anywhere would corrupt roughly one key in every few."""
    keychain = kc()
    raw = bytes(range(256))
    keychain.set("slot", raw)
    assert keychain.get("slot") == raw


def test_slots_are_independent():
    keychain = kc()
    keychain.set("a", b"one")
    keychain.set("b", b"two")
    assert keychain.get("a") == b"one" and keychain.get("b") == b"two"


def test_the_service_namespaces_the_items():
    lib = FakeSecurity()
    MacKeychain(service="com.other.app", lib=lib).set("slot", b"x")
    assert MacKeychain(service=DEFAULT_SERVICE, lib=lib).get("slot") is None


# -- errors must not look like absence ------------------------------------
def test_a_read_failure_raises_rather_than_reporting_absent():
    """THE dangerous one. If a failure returned None, Keyring would mint a
    fresh random master and orphan every claim ever sealed with the old
    key — silently, and with no way back."""
    with pytest.raises(KeychainError) as exc:
        kc(fail_with=-25293).get("slot")     # errSecAuthFailed
    assert "-25293" in str(exc.value)
    assert "orphan" in str(exc.value)


def test_a_write_failure_raises():
    with pytest.raises(KeychainError):
        kc(fail_with=-25308).set("slot", b"x")   # errSecInteractionNotAllowed


def test_the_error_names_the_operation_and_the_slot():
    """An OSStatus alone sends the reader to a web search; the operation and
    slot make it diagnosable from the log line."""
    with pytest.raises(KeychainError) as exc:
        kc(fail_with=-1).get("tracemem.master.v1")
    assert "read" in str(exc.value)
    assert "tracemem.master.v1" in str(exc.value)


# -- the buffer the framework owns ----------------------------------------
def test_the_read_buffer_is_always_freed():
    """It holds key material. Leaking it leaves the master in a page this
    process no longer tracks."""
    lib = FakeSecurity()
    keychain = MacKeychain(lib=lib)
    keychain.set("slot", b"secret")
    keychain.get("slot")
    assert lib.freed == 1


# -- overwrite is a named, deliberate path --------------------------------
def test_overwriting_an_existing_slot_goes_through_replace():
    """Overwriting the master orphans every claim sealed with the old key —
    the most destructive operation in the codebase. It must not happen as an
    unnoticed side effect of a duplicate add."""
    lib = FakeSecurity()
    keychain = MacKeychain(lib=lib)
    keychain.set("slot", b"first")
    keychain.set("slot", b"second")
    assert keychain.get("slot") == b"second"
    assert ("modify",) in lib.calls


def test_delete_reports_whether_anything_was_there():
    keychain = kc()
    assert keychain.delete("slot") is False
    keychain.set("slot", b"x")
    assert keychain.delete("slot") is True


# -- the Keyring cannot tell which storage it got -------------------------
def test_the_keyring_works_identically_over_the_real_adapter():
    """The suite stays Keychain-free by CONSTRUCTION: Keyring sees two
    methods and cannot distinguish a dict from the framework."""
    keychain = kc()
    ring = Keyring(keychain)
    first = ring.derive("transport")

    again = Keyring(keychain)                 # reopens the same stored master
    assert again.derive("transport") == first
    assert ring.derive("index") != first      # purposes stay separated


def test_the_master_is_never_exposed_by_the_adapter_path():
    """No export API (K2). The adapter stores bytes; nothing hands them
    back up through Keyring."""
    ring = Keyring(kc())
    public = [n for n in dir(ring) if not n.startswith("_")]
    assert "master" not in public
    for name in public:
        assert "export" not in name and "secret" not in name


# -- the live check, off by default ---------------------------------------
@pytest.mark.skipif(os.environ.get("TRACE_LIVE_KEYCHAIN") != "1",
                    reason="writes to the real login keychain; opt in with "
                           "TRACE_LIVE_KEYCHAIN=1")
def test_live_round_trip_against_the_real_keychain():
    """Not run by the suite, on purpose: it mutates the founder's machine
    and can raise an interactive unlock prompt. The 'survives reboot' half
    of the oracle cannot be tested by any test and is recorded as unproven
    on the board."""
    keychain = MacKeychain(service="com.trace.watcher.selftest")
    slot = "selftest.v1"
    try:
        keychain.set(slot, b"live-round-trip")
        assert keychain.get(slot) == b"live-round-trip"
    finally:
        keychain.delete(slot)
    assert keychain.get(slot) is None

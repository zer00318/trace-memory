"""Container: envelope, AEAD, egress gate, keyring, sync convergence."""
from __future__ import annotations

import pytest

from tests.helpers import make_claim
from tracemem.container.crypto import AuthFailure, StdlibAead, hkdf
from tracemem.container.format import pack, peek_header, unpack
from tracemem.container.keyring import Keyring
from tracemem.container.pillar_boundary import EgressViolation, assert_egress_safe

KEY = b"k" * 32


class DictKeychain:
    def __init__(self):
        self._d = {}
    def get(self, slot):
        return self._d.get(slot)
    def set(self, slot, value):
        self._d[slot] = value


def test_envelope_round_trip():
    claims = [make_claim(text="hello kiln", t_ms=5),
              make_claim(text="bye", t_ms=9, frame="f-2")]
    blob = pack(claims, KEY, {"device_id": "mac-1"})
    header, out = unpack(blob, KEY)
    assert header["count"] == 2 and header["t_min_ms"] == 5
    assert [c.id for c in out] == [c.id for c in claims]     # identity round-trip


def test_header_rejects_non_routing_fields():
    with pytest.raises(ValueError):
        pack([make_claim()], KEY, {"claim_text_preview": "secret"})


def test_tamper_and_wrong_key_fail_closed():
    blob = bytearray(pack([make_claim()], KEY, {"device_id": "m"}))
    with pytest.raises(AuthFailure):
        unpack(bytes(blob), b"w" * 32)                       # wrong key
    blob[-1] ^= 0xFF
    with pytest.raises(AuthFailure):
        unpack(bytes(blob), KEY)                             # flipped body bit
    blob2 = bytearray(pack([make_claim()], KEY, {"device_id": "m"}))
    idx = blob2.index(b'"m"')                                # flip a HEADER byte
    blob2[idx + 1] = ord("x")
    with pytest.raises((AuthFailure, ValueError)):
        unpack(bytes(blob2), KEY)                            # AAD binds header


def test_aead_basics():
    aead = StdlibAead(KEY)
    nonce, ct = aead.encrypt(b"hdr", b"payload")
    assert aead.decrypt(b"hdr", nonce, ct) == b"payload"
    with pytest.raises(AuthFailure):
        aead.decrypt(b"HDR", nonce, ct)


def test_egress_gate_typed_and_sniffed():
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    assert_egress_safe([make_claim(), Episode("e", TimeRange(0, 1), None)])
    with pytest.raises(EgressViolation):
        assert_egress_safe([b"raw frame bytes"])
    with pytest.raises(EgressViolation):
        assert_egress_safe([make_claim(text="data:image/png;base64,AAAA")])


def test_keyring_no_export_and_stable_derive():
    kc = DictKeychain()
    kr = Keyring(kc)
    assert kr.derive("transport") == kr.derive("transport")
    assert kr.derive("transport") != kr.derive("storage")
    public = [m for m in dir(kr) if not m.startswith("_")]
    assert set(public) <= {"derive", "device_add_qr", "redeem_qr"}  # no getter exists
    assert hkdf(b"a" * 32, b"i") == hkdf(b"a" * 32, b"i")


def test_qr_enrollment_pairs_devices():
    kc1 = DictKeychain()
    kr1 = Keyring(kc1)
    payload, code = kr1.device_add_qr()
    assert "sealed" in payload and kc1._d  # payload is ciphertext, not the key
    kr2 = Keyring.redeem_qr(payload, code, DictKeychain())
    assert kr2.derive("transport") == kr1.derive("transport")   # same identity
    with pytest.raises(AuthFailure):
        Keyring.redeem_qr(payload, "WRONGCODE", DictKeychain())

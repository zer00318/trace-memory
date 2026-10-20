"""The pairing code: what the Mac draws is what the phone app parses."""
from __future__ import annotations

import base64
import json

from tracemem.container import phone_pairing as pp
from tracemem.container.phone_link import PORT, PROTOCOL


def test_the_code_carries_exactly_what_the_phone_parses():
    """Swift's Pairing.fromCode reads p, h, a, port and k. A renamed field
    here would pair nothing and say nothing."""
    key = bytes(range(32))
    obj = json.loads(pp.payload(key, "Mac.local", ["192.168.1.20"],
                                device="phone-0a1b"))
    assert obj == {"p": PROTOCOL, "h": "Mac.local", "a": ["192.168.1.20"],
                   "port": PORT, "d": "phone-0a1b",
                   "k": base64.b64encode(key).decode()}


def test_the_code_is_drawn_black_on_white_in_any_terminal_theme():
    art = pp.render([[True, False], [False, True]])
    for line in art.splitlines():
        assert line.startswith("\x1b[30;47m") and line.endswith("\x1b[0m")


def test_the_master_key_never_appears_in_a_pairing_code():
    """Only the phone purpose key travels; the master stays in the Keychain."""
    from tracemem.container.keyring import Keyring
    from tracemem.container.phone_link import phone_key

    class Chain:
        def __init__(self):
            self.slot = {}

        def get(self, k):
            return self.slot.get(k)

        def set(self, k, v):
            self.slot[k] = v

    chain = Chain()
    ring = Keyring(chain)
    master = next(iter(chain.slot.values()))
    from tracemem.container.phone_link import device_key
    root = phone_key(ring)
    code = pp.payload(device_key(root, "phone-1"), "Mac.local", [],
                      device="phone-1")
    for secret in (master, root):
        assert base64.b64encode(secret).decode() not in code
        assert secret.hex() not in code


def test_the_window_shows_a_code_that_decodes_to_the_exact_payload():
    """What the phone is pointed at must read back byte for byte."""
    Quartz = __import__("pytest").importorskip("Quartz")
    text = pp.payload(bytes(range(32)), "Mac.local", ["10.0.0.2"])
    cg, _size = pp.qr_cgimage(text)
    det = Quartz.CIDetector.detectorOfType_context_options_(
        Quartz.CIDetectorTypeQRCode, None, None)
    found = det.featuresInImage_(Quartz.CIImage.imageWithCGImage_(cg))
    assert [f.messageString() for f in found] == [text]

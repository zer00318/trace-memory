"""OWNS: the ONLY egress seam (spec 06 §5, K3).

Typed gate: only Claim and Episode instances may cross toward sync/export.
Defense in depth: even a Claim is rejected if its text smells like smuggled
media (frames never leave the device — the constitution's hardest law).
"""
from __future__ import annotations

import base64
import binascii
import re

from tracemem.domain import Claim, Episode

_DATA_URI = re.compile(r"data:(image|video|audio)/", re.I)
_B64_CHUNK = re.compile(r"[A-Za-z0-9+/=]{512,}")
_MEDIA_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"ftyp", b"\x00\x00\x01\xba")


class EgressViolation(Exception):
    pass


def _looks_like_media(text: str) -> bool:
    if _DATA_URI.search(text):
        return True
    for m in _B64_CHUNK.finditer(text):
        try:
            head = base64.b64decode(m.group(0)[:64] + "==", validate=False)[:12]
        except (binascii.Error, ValueError):
            continue
        if any(head.startswith(sig) or sig in head for sig in _MEDIA_MAGIC):
            return True
        return True          # any >=512B opaque base64 blob has no business in a claim
    return False


def assert_egress_safe(items) -> None:
    for it in items:
        if not isinstance(it, (Claim, Episode)):
            raise EgressViolation(
                "only Claim/Episode may cross the pillar boundary, got %s"
                % type(it).__name__)
        if isinstance(it, Claim) and _looks_like_media(it.text):
            raise EgressViolation(
                "claim %s carries an embedded media payload — frames never "
                "leave the device" % it.id[:8])

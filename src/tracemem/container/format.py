"""OWNS: the .trace envelope (spec 06 §1, K1/K5).

MAGIC | ver | header_len | header(JSON plaintext) | nonce | body(ciphertext).
Header carries ONLY routing fields and is readable without any key; the body
is AEAD-sealed with AAD = the exact header bytes, so a relay that flips one
header bit kills decryption. (Spec names CBOR; v1 uses canonical JSON —
stdlib-only ruling. The version byte makes that swap non-breaking.)
"""
from __future__ import annotations

import json
import struct
from typing import Dict, List, Tuple

from tracemem.container.crypto import StdlibAead
from tracemem.container.pillar_boundary import assert_egress_safe
from tracemem.domain import Claim
from tracemem.text.claim_log import _unwire, _wire

MAGIC = b"TRCE"
VERSION = 1

_ROUTING_FIELDS = {"v", "count", "t_min_ms", "t_max_ms", "device_id", "kind"}


def _header_bytes(meta: Dict) -> bytes:
    rogue = set(meta) - _ROUTING_FIELDS
    if rogue:
        raise ValueError("header may carry routing fields only; rogue: %s"
                         % sorted(rogue))   # semantics belong in the sealed body
    return json.dumps(meta, sort_keys=True).encode()


def pack(items: List[Claim], key: bytes, header_meta: Dict) -> bytes:
    assert_egress_safe(items)                        # the seam guards itself
    meta = dict(header_meta)
    meta.setdefault("v", VERSION)
    meta.setdefault("kind", "claims")
    meta["count"] = len(items)
    if items:
        ts = [c.coord.t_ms for c in items]
        meta.setdefault("t_min_ms", min(ts))
        meta.setdefault("t_max_ms", max(ts))
    header = _header_bytes(meta)
    payload = json.dumps([_wire(c).decode() for c in items]).encode()
    nonce, body = StdlibAead(key).encrypt(header, payload)
    return (MAGIC + struct.pack(">BI", VERSION, len(header)) + header
            + struct.pack(">H", len(nonce)) + nonce + body)


def peek_header(blob: bytes) -> Dict:
    """Routing WITHOUT the key — what a relay is allowed to see (K1)."""
    if blob[:4] != MAGIC:
        raise ValueError("not a .trace container")
    ver, hlen = struct.unpack(">BI", blob[4:9])
    if ver != VERSION:
        raise ValueError("unknown container version %d" % ver)
    return json.loads(blob[9:9 + hlen].decode())


def unpack(blob: bytes, key: bytes) -> Tuple[Dict, List[Claim]]:
    header_meta = peek_header(blob)
    _ver, hlen = struct.unpack(">BI", blob[4:9])
    header = blob[9:9 + hlen]
    off = 9 + hlen
    (nlen,) = struct.unpack(">H", blob[off:off + 2])
    nonce = blob[off + 2:off + 2 + nlen]
    body = blob[off + 2 + nlen:]
    payload = StdlibAead(key).decrypt(header, nonce, body)   # AuthFailure pre-parse
    return header_meta, [_unwire(w.encode()) for w in json.loads(payload.decode())]

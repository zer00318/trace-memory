"""OWNS: the wire-payload version guard (spec 02 §4, S2).

Migrate-on-read: an old payload is upgraded through the MIGRATIONS chain the
moment it is read; an UNKNOWN (future) version raises — new code reading old
data is a migration, old code reading future data is corruption-in-waiting.
"""
from __future__ import annotations

import json


class SchemaVersionError(Exception):
    pass


CURRENT = 1
MIGRATIONS = {}          # from_version -> fn(dict) -> dict (with bumped "v")


def wire(claim) -> bytes:
    from tracemem.text.claim_log import _wire
    return _wire(claim)


def unwire(payload_bytes: bytes):
    from tracemem.text.claim_log import _unwire
    d = json.loads(payload_bytes.decode("utf-8"))
    seen = set()
    while d.get("v") in MIGRATIONS:
        if d["v"] in seen:
            raise SchemaVersionError("migration cycle at v%r" % d["v"])
        seen.add(d["v"])
        d = MIGRATIONS[d["v"]](d)
    if d.get("v") != CURRENT:
        raise SchemaVersionError("unknown claim wire version: %r" % d.get("v"))
    return _unwire(json.dumps(d, sort_keys=True).encode("utf-8"))

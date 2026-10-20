"""A cold-start claim must not write the user's file path in the clear.

WITNESSED 2026-10-15 (independent review): all 737 OWNED claims on the live
store carried the full file path in the plaintext `bid_id` column, beside a
ciphertext that sealed everything else. The column is an index copy nothing
queries; the claim's own bid_id lives, sealed, in the wire.
"""
from __future__ import annotations

import sqlite3

from tracemem.owned.reader import _owned_claim
from tracemem.text.claim_log import ClaimLog
from tracemem.container.crypto import SodiumCrypto


def test_an_owned_claims_path_is_sealed_not_indexed(tmp_path):
    path = "/Users/someone/Documents/Medical/biopsy-results-2026.pdf"
    claim = _owned_claim("Biopsy results", path, "mac-1", 1_700_000_000_000)
    assert path in claim.bid_id
    store = tmp_path / "s.sqlite3"
    log = ClaimLog(str(store), crypto=SodiumCrypto(b"k" * 32))
    log.append(claim)

    raw = sqlite3.connect(str(store)).execute(
        "select bid_id, bidder from claim").fetchone()
    assert "biopsy" not in raw[0] and "/Users/" not in raw[0], raw
    assert raw[0].startswith("owned:")
    assert log.get(claim.id).bid_id == claim.bid_id, "the sealed claim keeps it"
    # idempotent by content: the same file read again is the same claim
    log.append(claim)
    assert log.count() == 1

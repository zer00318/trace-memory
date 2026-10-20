"""The store checks itself on the way up.

`verify_chain` has existed and been thorough since July, and the DAEMON never
called it — only the snapshotter, the crypto migration and a torture tool.
So a running TRACE never once checked whether its own hash chain still held.
Disk rot, a half-written row, a claim altered after the fact: all invisible.

The never-lie promise rests on this log. An answer cited from a claim that
was changed after it was written is a lie the product cannot detect and would
defend under questioning.

These tests plant the corruption and check that the walk NAMES it — the
oracle's "both paths tested against planted corruption".
"""
from __future__ import annotations

from tracemem.domain import (CaptureCoord, CoordinateStack, SpanCoord,
                             WorldCoord)
from tracemem.extraction.claim_builder import build_claim
from tracemem.text.claim_log import ClaimLog


def _claim(text, t_ms):
    coord = CoordinateStack(world=WorldCoord(place_key="p"),
                            capture=CaptureCoord(frame_id="f", device_id="d"),
                            span=SpanCoord(), t_ms=t_ms)
    return build_claim({"text": text}, coord, "ocr", bidder="b", bid_id="i",
                       bid_predicted_value=0.0, actual_cost=0.0, scope="frame")


def _log(tmp_path, n=6):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    for i in range(n):
        log.append(_claim("claim %d" % i, 1000 + i))
    return log


def test_an_intact_store_passes(tmp_path):
    r = _log(tmp_path).verify_chain()
    assert r["ok"] is True
    assert r["checked"] == 6
    assert r["complete"] is True


def test_a_tail_pass_is_never_mistaken_for_the_whole_store(tmp_path):
    """`complete` is the field that stops a cheap boot check being read as a
    clean bill of health over six hundred thousand rows."""
    r = _log(tmp_path).verify_chain(tail=3)
    assert r["ok"] is True
    assert r["complete"] is False
    assert r["checked"] <= 3


def test_an_altered_claim_is_named(tmp_path):
    """The row itself was changed after it was written."""
    log = _log(tmp_path)
    victim = list(log._db.execute(
        "SELECT id FROM claim ORDER BY rowid LIMIT 1 OFFSET 3"))[0][0]
    log._db.execute("UPDATE claim SET ciphertext = ? WHERE id = ?",
                    (b"tampered", victim))
    log._db.commit()

    r = log.verify_chain()
    assert r["ok"] is False
    assert r["broken_at"] == victim
    assert r["reason"] in ("content", "link")


def test_a_deleted_claim_is_named(tmp_path):
    """A row between two others is gone — the chain no longer joins up, and
    the claim whose parent vanished is the one that must be reported."""
    log = _log(tmp_path)
    victim = list(log._db.execute(
        "SELECT id FROM claim ORDER BY rowid LIMIT 1 OFFSET 2"))[0][0]
    log._db.execute("DELETE FROM claim WHERE id = ?", (victim,))
    log._db.commit()

    r = log.verify_chain()
    assert r["ok"] is False
    assert r["reason"] == "link"
    assert r["broken_at"] != victim, \
        "it named the row that is gone rather than the one that noticed"


def test_boot_walks_a_tail_not_the_world():
    """Boot is not the place for a walk over 600k rows. The constant is the
    contract; the night owns the deep pass."""
    from tracemem.__main__ import BOOT_CHAIN_TAIL

    assert 0 < BOOT_CHAIN_TAIL <= 5000


def test_corruption_reports_rather_than_refusing_to_start():
    """A broken link does not make the rest worthless, and a memory that will
    not start is worse than one that starts and says what is wrong."""
    import inspect

    from tracemem import __main__ as m

    src = inspect.getsource(m)
    i = src.index("verify_chain(tail=BOOT_CHAIN_TAIL)")
    after = src[i:i + 1200]
    assert "HASH CHAIN IS BROKEN" in after
    assert "return 2" not in after.split("def ")[0]

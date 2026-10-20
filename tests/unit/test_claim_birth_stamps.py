"""OWNS: the claim WRITE path's oracle (sweep #7, 2026-07-19).

Every built claim must land in the store stamped AT BIRTH with five fields:
bidder, bid_id, bid_predicted_value, actual_cost (ruling dd,
campaign/STATE_BOARD.md) and scope (specs/claim_match.md A4). Neither is
retrofittable — a claim written without them can never be priced or fairly
graded later. This file is the oracle: round-trip with all five stamps
intact, and loud rejection (never a silent default) when any is missing.
"""
from __future__ import annotations

import pytest

from tracemem.binder.cross_pillar import SensorCtx
from tracemem.domain import Grade
from tracemem.extraction.claim_builder import build_claim
from tracemem.extraction.coordinate_builder import build_stack
from tracemem.text.claim_log import ClaimLog


def _stamped_claim(text="the reading eye saw X", bidder="node:jar", bid_id="node:jar#1",
                    bid_predicted_value=0.7, actual_cost=0.03, scope="frame"):
    stack, _ = build_stack((0.1, 0.1, 0.2, 0.2), {"frame_id": "f-1", "app": "Safari"},
                           SensorCtx(device_id="mac-1"), t_ms=1000)
    return build_claim({"text": text}, stack, "ocr:vision",
                        bidder=bidder, bid_id=bid_id,
                        bid_predicted_value=bid_predicted_value,
                        actual_cost=actual_cost, scope=scope)


def test_build_claim_threads_all_five_birth_stamps():
    c = _stamped_claim()
    assert c.bidder == "node:jar" and c.bid_id == "node:jar#1"
    assert c.bid_predicted_value == 0.7 and c.actual_cost == 0.03
    assert c.scope == "frame"


def test_build_claim_without_bid_kwargs_leaves_stamps_none():
    """Scratch/synthetic construction (no auction involved) is still legal —
    the STORE is the gate, not the builder."""
    stack, _ = build_stack((0.1, 0.1, 0.2, 0.2), {"frame_id": "f-1"},
                           SensorCtx(device_id="mac-1"), t_ms=1)
    c = build_claim({"text": "x"}, stack, "ocr:vision")
    assert c.bidder is None and c.scope is None


def test_claim_rejects_bogus_scope_at_construction():
    with pytest.raises(ValueError):
        _stamped_claim(scope="whatever")


def test_store_round_trips_all_five_birth_stamps(tmp_path):
    log = ClaimLog(str(tmp_path / "claims.sqlite3"))
    claim = _stamped_claim()

    # oracle: BEFORE building the gate, witness the failure this file exists
    # to prevent — an unstamped claim used to write silently. Now:
    log.append(claim)
    back = log.get(claim.id)

    assert back is not None
    assert back.bidder == claim.bidder
    assert back.bid_id == claim.bid_id
    assert back.bid_predicted_value == claim.bid_predicted_value
    assert back.actual_cost == claim.actual_cost
    assert back.scope == claim.scope
    # full built -> stored -> queried round trip, not just the stamps
    assert back.text == claim.text and back.grade == Grade.VERBATIM


def test_store_rejects_write_missing_any_single_stamp_loudly(tmp_path):
    log = ClaimLog(str(tmp_path / "claims.sqlite3"))
    for missing_field in ("bidder", "bid_id", "bid_predicted_value", "actual_cost", "scope"):
        kwargs = dict(bidder="b", bid_id="b#1", bid_predicted_value=0.5,
                      actual_cost=0.1, scope="frame")
        kwargs[missing_field] = None
        claim = _stamped_claim(**kwargs)
        with pytest.raises(ValueError, match="birth stamp"):
            log.append(claim)


def test_store_never_silently_defaults_a_bare_synthetic_claim(tmp_path):
    """The exact failure sweep #7 found unowned: a claim built with no bid
    info at all must not slip into the store silently."""
    stack, _ = build_stack((0.1, 0.1, 0.2, 0.2), {"frame_id": "f-1"},
                           SensorCtx(device_id="mac-1"), t_ms=1)
    bare = build_claim({"text": "no bid behind this one"}, stack, "ocr:vision")
    log = ClaimLog(str(tmp_path / "claims.sqlite3"))
    with pytest.raises(ValueError, match="birth stamp"):
        log.append(bare)
    assert log.get(bare.id) is None  # confirms nothing landed


def test_predicted_vs_actual_by_bidder_calibration_table(tmp_path):
    log = ClaimLog(str(tmp_path / "claims.sqlite3"))
    # bidder "hot" over-predicts relative to what it actually cost
    log.append(_stamped_claim(text="a", bidder="hot", bid_id="hot#1",
                              bid_predicted_value=1.0, actual_cost=0.2))
    log.append(_stamped_claim(text="b", bidder="hot", bid_id="hot#2",
                              bid_predicted_value=0.8, actual_cost=0.1))
    log.append(_stamped_claim(text="c", bidder="calibrated", bid_id="cal#1",
                              bid_predicted_value=0.5, actual_cost=0.5))

    table = {row["bidder"]: row for row in log.predicted_vs_actual_by_bidder()}
    assert table["hot"]["n"] == 2
    assert table["hot"]["avg_predicted_value"] == pytest.approx(0.9)
    assert table["hot"]["avg_actual_cost"] == pytest.approx(0.15)
    assert table["calibrated"]["avg_predicted_value"] == pytest.approx(0.5)
    assert table["calibrated"]["avg_actual_cost"] == pytest.approx(0.5)
    # the discountable signal: hot's predicted runs well above its actual
    assert table["hot"]["avg_predicted_value"] > table["hot"]["avg_actual_cost"]

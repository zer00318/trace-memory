"""OWNS: perceiver output -> Claim with the grade the perceiver EARNS
(spec 01 §11, P3.1-3.2). An unknown perceiver is a construction error, not a
default grade — a silent default would launder gist as verbatim.

Also OWNS (sweep #7, 2026-07-19): threading the five birth stamps a real
helper's winning bid carries — bidder, bid_id, bid_predicted_value (ruling
dd) and scope (claim_match.md A4) — onto the Claim it builds, so the WRITE
path (text/claim_log.py ClaimLog.append) has something to enforce. This
function does not itself require them: many callers (synthetic fixtures,
tests, gist-only paths with no auction involved) build Claims that never
reach the real store. The store is the gate.
"""
from __future__ import annotations

from typing import Optional

from tracemem.domain import Claim, Confidence, Grade, Modality, Provenance
from tracemem.domain.grading import GRADE_BY_SOURCE, grade_for

# THE grade table moved to domain/grading.py (Grading rules per source atom,
# 2026-07-21): one table, in domain/, imported by every builder. Re-exported
# here so existing importers keep working, but this module no longer OWNS it
# — and must never grow a grade literal of its own again.
GRADE_BY_PERCEIVER = GRADE_BY_SOURCE


def build_claim(read_item: dict, coord_stack, perceiver_name: str, *,
                 bidder: Optional[str] = None,
                 bid_id: Optional[str] = None,
                 bid_predicted_value: Optional[float] = None,
                 actual_cost: Optional[float] = None,
                 scope: Optional[str] = None) -> Claim:
    """read_item: {text, confidence?, structured?, modality?, source_ids?}.

    bidder/bid_id/bid_predicted_value/actual_cost/scope: the five birth
    stamps (ruling dd + claim_match.md A4). Pass all five when the claim
    came from a real winning bid destined for the store; leave them None
    for scratch/synthetic Claims that will never be written there.
    """
    earned = grade_for(perceiver_name)      # THE table, domain/grading.py
    return Claim(
        text=read_item["text"],
        coord=coord_stack,
        provenance=Provenance(
            perceiver=perceiver_name,
            modality=Modality(read_item.get("modality", "screen")),
            source_ids=tuple(read_item.get("source_ids", ()))),
        grade=earned,
        confidence=Confidence(float(read_item.get("confidence", 0.9))),
        structured=read_item.get("structured"),
        bidder=bidder,
        bid_id=bid_id,
        bid_predicted_value=bid_predicted_value,
        actual_cost=actual_cost,
        scope=scope)

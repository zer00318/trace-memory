"""OWNS: Claim — the RAW atom (spec 00 §5).

CL1 frozen (no mutating methods can exist); CL2 content-derived id;
CL3 no delete/update anywhere on the type.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from tracemem.domain.confidence import Confidence
from tracemem.domain.coordinate import CoordinateStack
from tracemem.domain.grade import Grade
from tracemem.domain.ids import claim_id
from tracemem.domain.provenance import Provenance


SCOPES = ("frame", "memory")  # specs/claim_match.md A4: precision is assessed
                              # only on frame-scoped claims; memory-scoped is
                              # out of scope for that function.


@dataclass(frozen=True)
class Claim:
    text: str
    coord: CoordinateStack
    provenance: Provenance
    grade: Grade
    confidence: Confidence
    structured: Optional[Mapping[str, Any]] = None
    id: str = field(default="")
    # Birth stamps (sweep #7 / ruling dd / claim_match.md A4). Unretrofittable:
    # a claim written to the store without these can never be priced or fairly
    # graded. Optional here (many in-memory/synthetic Claims never touch the
    # real store — evalx fixtures, binder scratch work); the STORE is the
    # gate that refuses writes missing any of the five (see text/claim_log.py
    # ClaimLog.append). None of these five participate in content identity —
    # they describe what the claim COST and WHERE it counts, not what it SAYS.
    bidder: Optional[str] = None                    # who placed the winning bid
    bid_id: Optional[str] = None                    # the specific bid instance
    bid_predicted_value: Optional[float] = None      # bid.value at auction time
    actual_cost: Optional[float] = None              # measured cost to produce this claim
    scope: Optional[str] = None                      # "frame" | "memory" (A4)

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(
                self, "id",
                claim_id(self.text, self.coord, self.provenance,
                         structured=self.structured, grade=self.grade,
                         confidence=self.confidence))
        if self.scope is not None and self.scope not in SCOPES:
            raise ValueError(
                "Claim.scope must be one of %r or None, got %r — "
                "never guess a scope, the A4 asymmetry depends on it" % (SCOPES, self.scope))
        # Tier + world honesty (SCHEMA §2.1 part 7). A raw claim is an
        # OBSERVATION of one of two worlds, never a derived belief; and its
        # class must agree with its coordinate — an OWNED fact carries a
        # source, a LIVED fact a frame. This is what makes "a plan never poses
        # as an observation" a property of the type, not a hope of the caller.
        from tracemem.domain.provenance import ProvenanceClass
        klass = self.provenance.klass
        if klass is ProvenanceClass.BELIEF:
            raise ValueError(
                "a raw Claim cannot be BELIEF-class — belief is the META tier "
                "(a Node), not a raw observation")
        if klass is ProvenanceClass.OWNED and self.coord.capture.source is None:
            raise ValueError(
                "an OWNED claim must carry coord.capture.source (its corpus "
                "locator) — an owned fact without its source cannot be re-read")
        if klass is ProvenanceClass.LIVED and self.coord.capture.frame_id is None:
            raise ValueError(
                "a LIVED claim must carry coord.capture.frame_id (its screen "
                "frame) — a lived observation without its frame is unmoored")

    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.text.encode("utf-8"))
        h.update(str(self.coord).encode("utf-8"))
        h.update(str(self.provenance).encode("utf-8"))
        h.update(str(self.grade).encode("utf-8"))
        h.update(str(self.confidence).encode("utf-8"))
        h.update(str(self.structured).encode("utf-8"))
        return h.hexdigest()

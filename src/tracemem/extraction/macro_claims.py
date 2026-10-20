"""OWNS: the macro eye's understanding → store-ready Claims.

The VLM returns {"kind": "object|activity|inference|participant", "text": …}.
This welds each into a Claim carrying the grade that perceiver EARNS.

THE GRADE IS THE WHOLE POINT. `domain/grading.py` maps "vlm" → Grade.GIST,
and everything downstream depends on that being honest. A scene description
is lossy interpretation: "the user is playing chess" is a reading of pixels,
not a transcription of them. It sits in the same store as OCR verbatim, and
if it could outrank that verbatim then a confident wrong inference would
overrule text the machine actually read — precisely the failure the grade
scale exists to prevent. So this module builds through `build_claim` with
perceiver="vlm" and never passes a grade of its own. There is one table.

NO BBOX, DELIBERATELY. OCR claims carry a span because a run of text lives
somewhere on the frame; "the user appears to be reading documentation" lives
on the whole frame and nowhere in particular. Inventing a full-frame bbox
would let a whole-scene inference match a span query as though it had been
localised, which is a quiet way to make a gist claim look like a reading. The
span LAYER is still present and empty — `domain/coordinate` C2 makes the
structure mandatory precisely so that "no bbox" is stated rather than
implied by a missing field.

THE KIND IS STRUCTURE, NOT PROSE. It goes in `structured` rather than being
mashed into the text ("object: a chess board"), because the four kinds are
the same vocabulary the ceiling is scored against — keeping them machine-
readable is what lets tools/context_gap.py compare like with like.
"""
from __future__ import annotations

from typing import List, Sequence

from tracemem.domain import (CaptureCoord, Claim, CoordinateStack,
                            SpanCoord, WorldCoord)
from tracemem.extraction.claim_builder import build_claim
from tracemem.extraction.vlm_macro import KINDS

# What one glance is worth as a confidence. Below the OCR eye's 0.9 default
# on purpose: this sense is guessing in a way reading is not, and the number
# should say so before any downstream consumer has to reason about grades.
MACRO_CONFIDENCE = 0.6


def macro_claims(seen: Sequence[dict], *, t_ms: int, frame_id: str,
                 device_id: str, app: str = "", place_key: str = "",
                 bidder: str = "vlm_macro", bid_id: str = "",
                 bid_predicted_value: float = 0.0,
                 actual_cost: float = 0.0) -> List[Claim]:
    """VLM output → Claims. Junk in the list is dropped, never raised on.

    A malformed entry from a local model must cost that entry and nothing
    else: the alternative is one bad dict taking down a whole macro pass and
    with it every good claim in the same batch.
    """
    # A REAL BID ID EVEN WHEN NOBODY PASSED ONE. The store's gate only
    # rejects None, so "" would sail through — and `predicted_vs_actual_by_
    # bidder` aggregates over these, so a batch of empty ids silently merges
    # every macro pass this daemon ever ran into one uncostable blob. One
    # pass over one frame is one bid; naming it after the frame makes that
    # true on the wire.
    bid = bid_id or ("macro:%s" % frame_id)
    out: List[Claim] = []
    for item in seen:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        text = item.get("text")
        if kind not in KINDS or not isinstance(text, str) or not text.strip():
            continue
        coord = CoordinateStack(
            world=WorldCoord(place_key=place_key),
            capture=CaptureCoord(frame_id=frame_id, device_id=device_id,
                                 app=app),
            # SpanCoord() with no bbox — the layer is structurally
            # mandatory (domain/coordinate C2) and the bbox inside it is not,
            # which is exactly the shape a claim that lives nowhere in
            # particular needs. See the module docstring.
            span=SpanCoord(),
            t_ms=t_ms)
        out.append(build_claim(
            {"text": text.strip(), "confidence": MACRO_CONFIDENCE,
             "structured": {"kind": kind}},
            coord, "vlm",
            bidder=bidder, bid_id=bid,
            bid_predicted_value=bid_predicted_value,
            actual_cost=actual_cost, scope="frame"))
    return out

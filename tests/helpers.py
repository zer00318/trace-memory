"""Shared fixture factories."""
from __future__ import annotations

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Provenance, SpanCoord, WorldCoord,
)


def make_claim(text="hello", t_ms=1000, place="wifi:aaaa", device="mac-1",
               frame="f-1", bbox=(0.1, 0.1, 0.5, 0.2), modality=Modality.SCREEN,
               perceiver="ocr", grade=Grade.VERBATIM, app="TestApp",
               bidder="fixture:make_claim", bid_id=None,
               bid_predicted_value=0.0, actual_cost=0.0, scope="frame"):
    coord = CoordinateStack(
        world=WorldCoord(place_key=place),
        capture=CaptureCoord(frame_id=frame, device_id=device, app=app),
        span=SpanCoord(bbox=bbox),
        t_ms=t_ms,
    )
    return Claim(
        text=text, coord=coord,
        provenance=Provenance(perceiver=perceiver, modality=modality),
        grade=grade, confidence=Confidence(0.9),
        # birth stamps (ruling dd / claim_match.md A4): this fixture writes
        # through the real ClaimLog in several suites, so it must carry the
        # five stamps like any real write would — synthetic bidder/cost, but
        # never None (the store gate rejects None loudly).
        bidder=bidder, bid_id=bid_id or ("%s:%d" % (bidder, t_ms)),
        bid_predicted_value=bid_predicted_value, actual_cost=actual_cost,
        scope=scope,
    )


def judged_people(graph, *surfaces):
    """Record the night's verdict that these surfaces ARE people.

    Since 2026-08-20 a person mention needs two things to become a node:
    corroboration across episodes, AND a verdict from the local model that
    the capitalised word is a person at all (nodes/birth/person_judge.py).
    Measured cause: 110 surfaces had been born as people on the founder's
    graph and roughly 30 were people — the rest were cities (Springfield), apps
    (Instagram), UI chrome (Trash, Watch, Edit) and German nouns.

    Tests that exercise the CORROBORATION machinery should not also have to
    stand up a model, so they state the verdict directly, exactly as a night
    that had already run would have left it.
    """
    from tracemem.nodes.birth.person_judge import VERDICT_KEY

    for surface in surfaces:
        graph.set_meta(VERDICT_KEY % ("person:%s" % surface.strip().lower()),
                       "yes")

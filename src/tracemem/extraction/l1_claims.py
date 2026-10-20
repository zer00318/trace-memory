"""OWNS: the last L1 weld — ScreenRuns become store-ready Claims.

read_meal() anchored text on the source frame in pixels; this module
stamps each run into a Claim: normalized span bbox, the five birth
stamps from the winning bid (ruling dd), scope="frame" (these ARE frame
extractions — claim_match A4 grades their precision). Reading order is
applied here so claims arrive the way a human read the screen.
"""
from __future__ import annotations

from typing import List, Sequence

from tracemem.domain import CaptureCoord, Claim, CoordinateStack, SpanCoord, WorldCoord
from tracemem.extraction.claim_builder import build_claim
from tracemem.extraction.reading_order import order


def runs_to_claims(runs: Sequence, *, frame_w: int, frame_h: int, t_ms: int,
                   frame_id: str, device_id: str, app: str, place_key: str,
                   bidder: str, bid_id: str, bid_predicted_value: float,
                   actual_cost: float, window: str = "",
                   attribution=None) -> List[Claim]:
    """ScreenRuns (absolute px) → ordered, stamped, frame-scoped Claims."""
    if frame_w <= 0 or frame_h <= 0:
        raise ValueError("degenerate frame dimensions")

    class _N:  # reading_order needs normalized .x/.y/.w/.h
        __slots__ = ("x", "y", "w", "h", "run")
        def __init__(self, r):
            # CLIPPED TO THE FRAME (2026-10-14). OCR can return a rectangle
            # that overhangs the frame edge; unclipped it raised in SpanCoord
            # and the beat lost every claim of the frame. The text is on
            # screen, so it keeps the part of its box that is.
            x0 = min(max(r.x_px / frame_w, 0.0), 1.0)
            y0 = min(max(r.y_px / frame_h, 0.0), 1.0)
            x1 = min(max((r.x_px + r.w_px) / frame_w, 0.0), 1.0)
            y1 = min(max((r.y_px + r.h_px) / frame_h, 0.0), 1.0)
            self.x, self.y = x0, y0
            self.w, self.h = max(0.0, x1 - x0), max(0.0, y1 - y0)
            self.run = r

    def _inside(r) -> bool:
        """Any part of the box on the frame. One wholly outside is not a
        reading of this frame, and inventing a box for it would be a lie."""
        return (r.x_px < frame_w and r.y_px < frame_h
                and r.x_px + r.w_px > 0 and r.y_px + r.h_px > 0)

    claims: List[Claim] = []
    for n in order([_N(r) for r in runs if _inside(r)]):
        run_app, run_window = app, window
        if attribution is not None:
            attributed = attribution(n.run)
            if attributed is not None:
                run_app, run_window = attributed
        coord = CoordinateStack(
            world=WorldCoord(place_key=place_key),
            capture=CaptureCoord(frame_id=frame_id, device_id=device_id,
                                 app=run_app, window=run_window or None),
            span=SpanCoord(bbox=(n.x, n.y, n.x + n.w, n.y + n.h)),   # corners, house law
            t_ms=t_ms)
        claims.append(build_claim(
            {"text": n.run.text, "confidence": n.run.confidence},
            coord, "ocr",
            bidder=bidder, bid_id=bid_id,
            bid_predicted_value=bid_predicted_value,
            actual_cost=actual_cost, scope="frame"))
    return claims

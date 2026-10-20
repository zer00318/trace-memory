"""OWNS: assemble the CoordinateStack at the write seam (spec 01 §5, P2.1).

THE single highest-invisibility file in the system: if the span bbox is
dropped here, every downstream cite/point/drill silently degrades and nothing
ever errors. Therefore: the bbox given is the bbox stored, bit-identical —
this function transforms nothing, it only assembles. Its test pins every
emitted Claim to the exact OCR bbox.
"""
from __future__ import annotations

from typing import Optional, Tuple

from tracemem.binder.cross_pillar import SensorCtx, place_key
from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord


def build_stack(span_bbox: Optional[Tuple[float, float, float, float]],
                frame_ctx: dict, sensor_ctx: SensorCtx, t_ms: int,
                private_ssids=frozenset(), public_ssids=frozenset()
                ) -> Tuple[CoordinateStack, float]:
    """Returns (stack, co_location_confidence). The confidence travels with
    the binder's cross-device merge decision, never into the Claim itself."""
    key, co_conf = place_key(sensor_ctx, private_ssids, public_ssids)
    stack = CoordinateStack(
        world=WorldCoord(gps=sensor_ctx.gps, place_key=key),
        capture=CaptureCoord(
            frame_id=frame_ctx["frame_id"],
            device_id=sensor_ctx.device_id,
            app=frame_ctx.get("app"),
            window=frame_ctx.get("window")),
        span=SpanCoord(bbox=span_bbox),          # pass-through, NEVER recomputed
        t_ms=t_ms)
    return stack, co_conf

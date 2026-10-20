"""OWNS: the pixel→point conversion, and it is the ONLY place it happens.

THE LAW (Points-vs-pixels law atom, campaign/tree/01_world.json; decided
2026-07-21): every coordinate that leaves the capture boundary speaks in
POINTS — the display-independent space macOS lays windows out in. Backing
pixels exist only inside a frame buffer and die there.

Why the law exists at all: a Retina display reports a window at 2x the
points its own layout engine used. If two organs each "helpfully" convert,
a claim's bbox lands at 4x or 0.25x and NOTHING ERRORS — the number is
still a plausible rectangle. That is the silent-failure class this file
exists to make impossible: one converter, at one seam, with a test that
greps the rest of the tree for a second one.

The conversion seam is the capture boundary — retina_screen.py stamps
FrameRecord.scale when the frame is grabbed (Scale stamping atom). Above
that line, `scale` is not a concept any organ knows about.

WHO CALLS THIS TODAY: nothing. Stated plainly because this docstring used
to name window_stack.py and attention_mac.py as callers (audit 2026-07-21
caught it) — neither converts, and neither should: CGWindowList and
NSWorkspace already report POINTS, so the correct behaviour at those seams
is exactly the no-op they perform. The converter's real customer arrives
when a claim's geometry is derived from frame PIXELS. Until then this is a
law with a tested implementation and no traffic, which is the honest state
of it.

Normalized [0,1] span bboxes (domain/coordinate.py, INVARIANT C1) are
already scale-free by construction and never pass through here — the two
schemes coexist: normalized for spans inside a frame, points for absolute
geometry on the desktop.

SPEC: 01 §5 (coordinate truth)
ORACLE: tests/unit/test_coordinate_space.py — a 2x frame's pixel rect
        halves into points; and the grep trap (tests/invariants) proves no
        second converter exists in src/.
"""
from __future__ import annotations

from typing import Tuple

Rect = Tuple[float, float, float, float]
Point = Tuple[float, float]


def _check_scale(scale: float) -> float:
    """A bad scale must be loud here rather than silently rescaling the world."""
    s = float(scale)
    if s <= 0.0:
        raise ValueError(f"scale must be positive, got {scale!r}")
    return s


def points_from_pixels(pixels: float, scale: float) -> float:
    """One length: backing pixels → layout points."""
    return float(pixels) / _check_scale(scale)


def pixels_from_points(points: float, scale: float) -> float:
    """The inverse, for the rare downward trip (addressing a frame buffer)."""
    return float(points) * _check_scale(scale)


def rect_points_from_pixels(rect: Rect, scale: float) -> Rect:
    """A whole (x0, y0, x1, y1) rectangle: backing pixels → layout points."""
    s = _check_scale(scale)
    if len(rect) != 4:
        raise ValueError(f"rect must be (x0, y0, x1, y1), got {rect!r}")
    x0, y0, x1, y1 = (float(v) for v in rect)
    if x0 > x1 or y0 > y1:
        raise ValueError(f"rect must be well-ordered, got {rect!r}")
    return (x0 / s, y0 / s, x1 / s, y1 / s)


def point_from_frame_pixels(point: Point, frame_record) -> Point:
    """One point in a stamped frame buffer → macOS layout points.

    The caller hands over the frame record rather than reading its backing
    scale. That keeps knowledge of the stamp and the arithmetic at this one
    capture-boundary seam.
    """
    if len(point) != 2:
        raise ValueError(f"point must be (x, y), got {point!r}")
    s = _check_scale(frame_record.scale)
    return (float(point[0]) / s, float(point[1]) / s)


def normalized_from_pixels(rect: Rect, frame_w_px: float,
                           frame_h_px: float) -> Rect:
    """Pixel rect INSIDE a frame → the normalized [0,1] span bbox the
    domain demands. Scale-free on both sides (a fraction of the frame is
    the same fraction at any backing scale), so this needs no `scale` —
    which is exactly why span bboxes never touch the points law."""
    if frame_w_px <= 0 or frame_h_px <= 0:
        raise ValueError(f"frame size must be positive, "
                         f"got {frame_w_px!r}x{frame_h_px!r}")
    x0, y0, x1, y1 = (float(v) for v in rect)
    clamp = lambda v: min(1.0, max(0.0, v))     # noqa: E731 — local, one line
    return (clamp(x0 / frame_w_px), clamp(y0 / frame_h_px),
            clamp(x1 / frame_w_px), clamp(y1 / frame_h_px))

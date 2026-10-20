"""OWNS: mapping OCR-normalized boxes back to absolute screen pixels (L1).

The Binder's whole coordinate world depends on this being right:
a word read inside a cropped meal must land on the SCREEN it came from.
Pure math; round-trip tested.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CropRect:
    """Where a meal's crop sits on its source frame, in absolute pixels."""
    x: int
    y: int
    w: int
    h: int


def to_screen(nx: float, ny: float, nw: float, nh: float,
              crop: CropRect) -> tuple:
    """Normalized top-left box within the crop → absolute pixel box."""
    return (crop.x + nx * crop.w, crop.y + ny * crop.h,
            nw * crop.w, nh * crop.h)


def to_crop(px: float, py: float, pw: float, ph: float,
            crop: CropRect) -> tuple:
    """Absolute pixel box → normalized top-left box within the crop."""
    if crop.w <= 0 or crop.h <= 0:
        raise ValueError("degenerate crop")
    return ((px - crop.x) / crop.w, (py - crop.y) / crop.h,
            pw / crop.w, ph / crop.h)

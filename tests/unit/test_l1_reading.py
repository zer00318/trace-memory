"""L1 pure parts: reading order and coordinate round-trip."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tracemem.extraction.coord_map import CropRect, to_crop, to_screen
from tracemem.extraction.reading_order import order


@dataclass(frozen=True)
class R:
    x: float; y: float; w: float; h: float
    text: str = ""


def test_two_columns_read_left_column_first():
    left = [R(0.05, 0.1, 0.3, 0.02, "L1"), R(0.05, 0.3, 0.3, 0.02, "L2")]
    right = [R(0.55, 0.05, 0.3, 0.02, "R1"), R(0.55, 0.2, 0.3, 0.02, "R2")]
    got = [r.text for r in order(right + left)]
    assert got == ["L1", "L2", "R1", "R2"]


def test_same_line_reads_left_to_right():
    a = R(0.4, 0.100, 0.1, 0.02, "b")
    b = R(0.1, 0.104, 0.2, 0.02, "a")     # same line within tolerance
    assert [r.text for r in order([a, b])] == ["a", "b"]


def test_single_column_top_to_bottom():
    rs = [R(0.1, y, 0.5, 0.02, str(i)) for i, y in enumerate([0.9, 0.1, 0.5])]
    assert [r.text for r in order(rs)] == ["1", "2", "0"]


def test_coord_round_trip():
    crop = CropRect(x=100, y=200, w=640, h=480)
    px = to_screen(0.25, 0.5, 0.1, 0.05, crop)
    assert px == (260.0, 440.0, 64.0, 24.0)
    back = to_crop(*px, crop)
    assert all(abs(a - b) < 1e-9 for a, b in zip(back, (0.25, 0.5, 0.1, 0.05)))


def test_degenerate_crop_refuses():
    with pytest.raises(ValueError):
        to_crop(0, 0, 1, 1, CropRect(0, 0, 0, 10))

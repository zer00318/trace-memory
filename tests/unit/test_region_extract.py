"""Changed tiles cluster correctly; noise stays small; shapes stay honest."""
from __future__ import annotations

from tracemem.extraction.region_extract import extract


def test_l_shape_is_one_region_with_honest_tile_count():
    l_shape = [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]
    (r,) = extract(l_shape)
    assert (r.col0, r.row0, r.col1, r.row1) == (0, 0, 2, 2)
    assert r.tile_count == 5               # bbox area is 9; the count doesn't lie


def test_split_changes_are_two_regions():
    got = extract([(0, 0), (1, 0), (8, 8), (8, 9)])
    assert len(got) == 2


def test_single_tile_noise_stays_single():
    got = extract([(5, 5)])
    assert len(got) == 1 and got[0].tile_count == 1
    assert (got[0].col0, got[0].col1) == (5, 5)


def test_diagonal_is_not_connected():
    got = extract([(0, 0), (1, 1)])        # 4-connectivity: corners don't touch
    assert len(got) == 2


def test_empty_and_order_stability():
    assert extract([]) == []
    a = extract([(9, 9), (0, 0)])
    assert (a[0].row0, a[0].col0) == (0, 0)   # reading order: top-left first

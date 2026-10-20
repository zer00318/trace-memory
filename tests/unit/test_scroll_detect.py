"""Scroll vs change: translation found, chaos refused, stillness not ours."""
from __future__ import annotations

import random

from tracemem.extraction.scroll_detect import detect_scroll


def doc_rows(n=120, seed=3):
    rng = random.Random(seed)
    return [rng.uniform(1000, 50000) for _ in range(n)]


def test_downward_scroll_detected_with_exact_offset():
    doc = doc_rows(200)
    prev, curr = doc[0:120], doc[7:127]        # viewport slid 7 rows down
    assert detect_scroll(prev, curr) == 7


def test_upward_scroll_detected():
    doc = doc_rows(200)
    prev, curr = doc[7:127], doc[0:120]
    assert detect_scroll(prev, curr) == -7


def test_real_change_is_not_a_scroll():
    assert detect_scroll(doc_rows(seed=1), doc_rows(seed=2)) is None


def test_stillness_is_not_the_scroll_detectors_business():
    rows = doc_rows()
    assert detect_scroll(rows, list(rows)) is None


def test_scroll_survives_cursor_blink_noise():
    doc = doc_rows(200)
    prev, curr = doc[0:120], [r * 1.01 for r in doc[5:125]]   # 1% dither
    assert detect_scroll(prev, curr) == 5


def test_too_short_regions_refuse():
    assert detect_scroll([1.0] * 4, [1.0] * 4) is None

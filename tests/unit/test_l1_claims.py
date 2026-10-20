"""ScreenRuns → Claims: graded VERBATIM (earned), stamped, ordered, normalized."""
from __future__ import annotations

import pytest

from tracemem.domain import Grade
from tracemem.extraction.l1_read import ScreenRun
from tracemem.extraction.l1_claims import runs_to_claims

STAMPS = dict(bidder="peripheral:change", bid_id="b-1",
              bid_predicted_value=2.0, actual_cost=0.4)


def _run(text, x, y, w=200, h=20, conf=0.95):
    return ScreenRun(text=text, x_px=x, y_px=y, w_px=w, h_px=h, confidence=conf)


def test_claims_are_verbatim_frame_scoped_and_stamped():
    claims = runs_to_claims([_run("hello", 100, 50)], frame_w=2000, frame_h=1000,
                            t_ms=123, frame_id="f-1", device_id="mac-1",
                            app="Brave", place_key="wifi:home", **STAMPS)
    c = claims[0]
    assert c.grade == Grade.VERBATIM and c.scope == "frame"
    assert c.bidder == "peripheral:change" and c.bid_predicted_value == 2.0
    assert c.coord.span.bbox == pytest.approx((0.05, 0.05, 0.15, 0.07))  # corners
    assert c.coord.capture.app == "Brave" and c.coord.t_ms == 123


def test_claims_arrive_in_reading_order():
    runs = [_run("second", 100, 500), _run("first", 100, 100),
            _run("right-col", 1500, 100)]
    texts = [c.text for c in runs_to_claims(
        runs, frame_w=2000, frame_h=1000, t_ms=1, frame_id="f", device_id="m",
        app="A", place_key="p", **STAMPS)]
    assert texts == ["first", "second", "right-col"]


def test_degenerate_frame_refused():
    with pytest.raises(ValueError):
        runs_to_claims([], frame_w=0, frame_h=100, t_ms=1, frame_id="f",
                       device_id="m", app="A", place_key="p", **STAMPS)


def test_each_run_can_keep_the_window_that_owned_its_pixels():
    claims = runs_to_claims(
        [_run("left", 10, 10), _run("right", 1500, 10)],
        frame_w=2000, frame_h=1000, t_ms=1, frame_id="f", device_id="d",
        app="fallback", place_key="p", **STAMPS,
        attribution=lambda r: (("Editor", "window:7") if r.x_px < 1000
                               else ("Browser", "window:8")))
    assert [(c.coord.capture.app, c.coord.capture.window) for c in claims] == [
        ("Editor", "window:7"), ("Browser", "window:8")]


def test_a_box_overhanging_the_frame_is_clipped_not_fatal():
    """WITNESSED 2026-10-14 on the live eye: ValueError('bbox must be
    normalized to [0,1]'). OCR can return a rectangle that pokes past the
    frame edge; the exception escaped the beat and took EVERY claim of that
    frame with it. The text is on screen; only its box overhangs."""
    runs = [_run("kept", 100, 100), _run("edge", 1900, 980, w=200, h=40),
            _run("nowhere", 2100, 50)]
    claims = runs_to_claims(runs, frame_w=2000, frame_h=1000, t_ms=1,
                            frame_id="f", device_id="mac-1", app="A",
                            place_key=None, **STAMPS)
    by_text = {c.text: c for c in claims}
    assert set(by_text) == {"kept", "edge"}, "a box wholly outside is not a reading"
    assert by_text["edge"].coord.span.bbox == pytest.approx((0.95, 0.98, 1.0, 1.0))

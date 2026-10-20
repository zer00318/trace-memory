"""ORACLE for the Points-vs-pixels law + Scale stamping atoms (01_world).

The law: coordinates crossing the capture boundary are in POINTS, converted
in exactly one module. These tests are that law's teeth — the arithmetic
trap here, the "no second converter" grep trap in tests/invariants.
"""
import pytest

from tracemem.capture.capture_config import COORDINATE_SPACE
from tracemem.capture.coordinate_space import (
    normalized_from_pixels, pixels_from_points, points_from_pixels,
    point_from_frame_pixels, rect_points_from_pixels,
)
from tracemem.extraction.retina import FrameRecord


def test_the_law_is_declared_in_one_place():
    """A worker must be able to find the ruling without reading code."""
    assert COORDINATE_SPACE == "points"


# -- the conversion oracle: a 2x frame halves into points -------------------
def test_retina_2x_rect_halves_into_points():
    """The atom's PROOF, verbatim: 2x frame → coordinates halve into points."""
    assert rect_points_from_pixels((0.0, 0.0, 2560.0, 1600.0), 2.0) == \
        (0.0, 0.0, 1280.0, 800.0)


def test_non_retina_frame_is_the_identity():
    assert rect_points_from_pixels((10.0, 20.0, 30.0, 40.0), 1.0) == \
        (10.0, 20.0, 30.0, 40.0)


def test_single_lengths_convert_both_directions():
    assert points_from_pixels(100.0, 2.0) == 50.0
    assert pixels_from_points(50.0, 2.0) == 100.0


def test_round_trip_is_lossless_at_real_scales():
    for scale in (1.0, 2.0, 1.5):        # 1.5x exists on scaled-mode displays
        assert pixels_from_points(points_from_pixels(937.0, scale), scale) == \
            pytest.approx(937.0)


# -- a bad scale is LOUD, never a silent rescale of the world ---------------
@pytest.mark.parametrize("bad", [0.0, -1.0, -2.5])
def test_non_positive_scale_refuses(bad):
    with pytest.raises(ValueError):
        points_from_pixels(10.0, bad)
    with pytest.raises(ValueError):
        rect_points_from_pixels((0.0, 0.0, 1.0, 1.0), bad)


def test_disordered_rect_refuses():
    """A flipped rect means the caller mixed up an origin — say so."""
    with pytest.raises(ValueError):
        rect_points_from_pixels((100.0, 0.0, 10.0, 50.0), 2.0)


# -- span bboxes stay scale-free: the two schemes must not touch ------------
def test_normalized_span_is_scale_free():
    """Same rectangle, same fraction of the frame, at 1x and at 2x."""
    at_1x = normalized_from_pixels((320.0, 180.0, 640.0, 360.0), 1280.0, 720.0)
    at_2x = normalized_from_pixels((640.0, 360.0, 1280.0, 720.0), 2560.0, 1440.0)
    assert at_1x == at_2x == (0.25, 0.25, 0.5, 0.5)


def test_normalized_clamps_into_the_domains_invariant():
    """INVARIANT C1 (domain/coordinate.py) demands [0,1]: a rect running off
    the frame edge is clamped here, not rejected downstream."""
    assert normalized_from_pixels((-10.0, -10.0, 2000.0, 2000.0),
                                  1000.0, 1000.0) == (0.0, 0.0, 1.0, 1.0)


def test_zero_sized_frame_refuses():
    with pytest.raises(ValueError):
        normalized_from_pixels((0.0, 0.0, 1.0, 1.0), 0.0, 100.0)


# -- Scale stamping: the frame carries its own scale ------------------------
def test_frame_record_defaults_to_non_retina():
    """Synthetic/fixture frames have no display; 1.0 keeps them honest."""
    assert FrameRecord(t_ms=0, display="d", frame=None, nbytes=1).scale == 1.0


def test_frame_record_carries_its_scale_to_the_converter():
    """The stamped scale is what the conversion uses — no display lookup."""
    rec = FrameRecord(t_ms=0, display="built-in", frame=None, nbytes=1, scale=2.0)
    assert rect_points_from_pixels((0.0, 0.0, 100.0, 100.0), rec.scale) == \
        (0.0, 0.0, 50.0, 50.0)


def test_a_frame_point_crosses_the_single_conversion_seam():
    rec = FrameRecord(t_ms=0, display="built-in", frame=None, nbytes=1,
                      scale=2.0)
    assert point_from_frame_pixels((200.0, 100.0), rec) == (100.0, 50.0)


def test_frame_record_refuses_an_impossible_scale():
    with pytest.raises(ValueError):
        FrameRecord(t_ms=0, display="d", frame=None, nbytes=1, scale=0.0)


# -- the stamping seam: capture measures the scale, never assumes it --------
def _session_landing(pixel_width, points_width):
    """An SCStreamSession wired to a fake pixel reader — no ScreenCaptureKit,
    no display, just the stamping arithmetic under test."""
    from tracemem.capture.retina_screen import SCStreamSession
    landed = []
    s = SCStreamSession(
        retina=None, on_frame=landed.append,
        pixel_reader=lambda _sb: (pixel_width, 100, pixel_width * 4,
                                  b"x" * (pixel_width * 4)),
        clock=lambda: 0.0)
    s._display_points_width = points_width
    s._on_sample_buffer(object())
    return landed[0]


def test_capture_stamps_a_measured_retina_scale():
    """2560 pixels delivered across a 1280-point display = 2.0, measured."""
    assert _session_landing(2560, 1280.0).scale == 2.0


def test_downscaled_capture_off_a_retina_display_stamps_1x():
    """cfg.setWidth_() may downscale: the frame really IS 1x, and the
    measurement says so where an assumed hardware scale would lie."""
    assert _session_landing(1280, 1280.0).scale == 1.0


def test_unknown_display_geometry_stamps_the_honest_identity():
    assert _session_landing(2560, None).scale == 1.0


# -- RE-AUDIT 2026-07-21: the _native_pixels fix had NO regression test ----
def test_native_pixels_reads_this_macs_real_backing_size():
    """The fix that made the points law reachable: SCStreamConfiguration
    wants PIXELS, SCDisplay reports POINTS. Passing one into the other made
    the reading eye capture at half linear resolution on Retina and pinned
    every stamped scale at 1.0."""
    import Quartz
    from tracemem.capture.retina_screen import _native_pixels
    from tracemem.capture.displays import read_displays

    # FLAKE FIX 2026-07-22: this failed mid-session with IndexError when the
    # Mac's display slept — read_displays() correctly returns nothing when
    # there is no display, and a test about the REAL backing size has
    # nothing to read. Skipping is the honest outcome: the machine did not
    # answer, which is a fact about the environment, not about the code.
    # Failing here trains people to re-run until the screen wakes up.
    displays = read_displays()
    if not displays:
        pytest.skip("no display attached or the screen is asleep — nothing "
                    "to read a real backing size from")
    d = displays[0]

    class FakeSCDisplay:                      # only displayID() is used
        def displayID(self):
            return d.display_id

    px_w, px_h = _native_pixels(FakeSCDisplay())
    assert px_w == d.pixels_w and px_h == d.pixels_h
    assert px_w > d.points_w, "backing pixels must exceed layout points on Retina"
    assert px_w / d.points_w == 2.0


def test_native_pixels_degrades_to_none_rather_than_lying():
    """Falling back to the points figure is survivable; inventing a size is
    not — the caller needs to know it did not get an answer."""
    from tracemem.capture.retina_screen import _native_pixels

    class Broken:
        def displayID(self):
            raise RuntimeError("no display")

    assert _native_pixels(Broken()) == (None, None)


def test_the_frame_weight_consequence_is_booked_not_discovered_later():
    """The native-resolution fix made frames 10.4x heavier, cutting the
    retina ring from ~117 frames to ~11. A measurement with a real downstream
    consequence must be written down where the next worker will find it."""
    from tracemem.capture.capture_config import FULL_RES_FRAME_BYTES_MEASURED
    from tracemem.extraction.retina import DEFAULT_BUDGET_BYTES

    frames_held = DEFAULT_BUDGET_BYTES // FULL_RES_FRAME_BYTES_MEASURED
    assert frames_held == 11, (
        f"ring depth moved to {frames_held} frames — if this changed "
        f"deliberately, update the note in capture_config.py")

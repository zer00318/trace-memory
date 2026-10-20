"""Diff-kernel real-frame wiring tests (Extraction / Diff kernel).

Peripheral itself stays pure (synthetic-grid gauntlet elsewhere); these
tests pin the NEW real-frame adapter: BGRA8 bytes -> grayscale grid, and
the feed_motion_frame wiring that runs a motion-sense FrameRecord through
the kernel. No ObjC/ScreenCaptureKit involved — plain injected fakes, so
this suite runs on any interpreter.
"""
from dataclasses import dataclass

from tracemem.extraction.peripheral import (
    Peripheral, RegionEvent, feed_motion_frame, grayscale_grid_from_bgra,
)


@dataclass(frozen=True)
class FakePayload:
    """Duck-typed stand-in for capture/retina_screen.py's PixelPayload —
    proves feed_motion_frame only needs the four named attributes."""
    width: int
    height: int
    bytes_per_row: int
    data: bytes


def _solid_bgra(width, height, b, g, r):
    row = bytes([b, g, r, 255]) * width
    return row * height


def test_grayscale_grid_from_bgra_luma():
    # pure white -> luma 255; pure black -> 0; tight stride (no row padding)
    data = _solid_bgra(2, 2, 255, 255, 255)
    grid = grayscale_grid_from_bgra(2, 2, 2 * 4, data)
    assert grid == [[255, 255], [255, 255]]

    data = _solid_bgra(2, 2, 0, 0, 0)
    grid = grayscale_grid_from_bgra(2, 2, 2 * 4, data)
    assert grid == [[0, 0], [0, 0]]


def test_grayscale_grid_honors_row_padding():
    # 2px wide but bytes_per_row padded to 5 pixels' worth of bytes;
    # a tight-stride assumption would misread row 2 by 3 pixels' offset.
    width, height, stride = 2, 2, 5 * 4
    row0 = bytes([10, 10, 10, 255]) * width + b"\x00" * (stride - width * 4)
    row1 = bytes([200, 200, 200, 255]) * width + b"\x00" * (stride - width * 4)
    data = row0 + row1
    grid = grayscale_grid_from_bgra(width, height, stride, data)
    assert grid == [[10, 10], [200, 200]]


def test_feed_motion_frame_first_frame_marks_offered_and_reports_change():
    p = Peripheral(dwell_ms=50)
    payload = FakePayload(width=32, height=20, bytes_per_row=32 * 4,
                          data=_solid_bgra(32, 20, 0, 0, 0))
    events = feed_motion_frame(p, payload, t_ms=1000)
    assert p.honest_rate()["offered"] == 1
    assert all(isinstance(e, RegionEvent) for e in events)
    assert any(e.kind == "change" for e in events)


def test_feed_motion_frame_still_screen_reports_no_change():
    p = Peripheral(dwell_ms=50)
    black = FakePayload(width=32, height=20, bytes_per_row=32 * 4,
                        data=_solid_bgra(32, 20, 0, 0, 0))
    feed_motion_frame(p, black, t_ms=1000)     # first frame: baseline
    events = feed_motion_frame(p, black, t_ms=1010)   # identical: no change
    assert not any(e.kind == "change" for e in events)
    assert p.honest_rate()["offered"] == 2


def test_feed_motion_frame_real_change_fires_change_event():
    p = Peripheral(dwell_ms=50)
    black = FakePayload(width=32, height=20, bytes_per_row=32 * 4,
                        data=_solid_bgra(32, 20, 0, 0, 0))
    white = FakePayload(width=32, height=20, bytes_per_row=32 * 4,
                        data=_solid_bgra(32, 20, 255, 255, 255))
    feed_motion_frame(p, black, t_ms=1000)
    events = feed_motion_frame(p, white, t_ms=1010)
    assert any(e.kind == "change" for e in events)


# -- CPU diagnosis fix, 2026-07-19 (eval_runs/motion_cpu_profile_20260719.md) --
# The motion-sense oracle measured 32% CPU vs a <2% oracle on a (contaminated)
# "still" window. Profiling offline (synthetic 480x270 BGRA frames, no live
# capture) found the per-frame cost concentrated in two O(width*height)
# pure-Python passes: the BGRA->grayscale luma conversion and the region
# diff. These tests pin the two fixes: (1) grayscale_grid_from_bgra's LUT
# rewrite is byte-identical to the original multiply/floordiv formula, and
# (2) feed_motion_frame's raw-bytes fast path (Peripheral.observe_unchanged)
# produces exactly the same RegionEvent sequence a full observe() would for
# a byte-identical frame — including correct dwell-event firing — while
# skipping the expensive path.

def test_grayscale_lut_matches_naive_formula():
    import random
    rnd = random.Random(20260719)
    width, height = 37, 5   # odd width: exercises non-power-of-two striding too
    data = bytes(rnd.getrandbits(8) for _ in range(width * height * 4))

    def naive(width, height, bytes_per_row, data):
        grid = []
        for y in range(height):
            row_off = y * bytes_per_row
            row_bytes = data[row_off: row_off + width * 4]
            b_ch, g_ch, r_ch = row_bytes[0::4], row_bytes[1::4], row_bytes[2::4]
            grid.append([(r * 299 + g * 587 + b * 114) // 1000
                         for b, g, r in zip(b_ch, g_ch, r_ch)])
        return grid

    expected = naive(width, height, width * 4, data)
    actual = grayscale_grid_from_bgra(width, height, width * 4, data)
    assert actual == expected


def test_fastpath_matches_full_observe_on_identical_frame_no_dwell_yet():
    """Two peripherals fed the same two identical frames: one through the
    real feed_motion_frame fast path, one forced through the slow path by
    calling Peripheral.observe() directly on matching grids. Same events."""
    payload = FakePayload(width=16, height=10, bytes_per_row=16 * 4,
                          data=_solid_bgra(16, 10, 30, 60, 90))
    grid = grayscale_grid_from_bgra(payload.width, payload.height,
                                    payload.bytes_per_row, payload.data)

    p_fast = Peripheral(dwell_ms=5_000)
    feed_motion_frame(p_fast, payload, t_ms=1000)          # baseline frame
    fast_events = feed_motion_frame(p_fast, payload, t_ms=1010)  # fast path

    p_slow = Peripheral(dwell_ms=5_000)
    p_slow.observe(grid, 1000)
    slow_events = p_slow.observe(grid, 1010)

    assert fast_events == slow_events == []


def test_fastpath_fires_dwell_event_matching_full_observe():
    payload = FakePayload(width=16, height=10, bytes_per_row=16 * 4,
                          data=_solid_bgra(16, 10, 30, 60, 90))
    grid = grayscale_grid_from_bgra(payload.width, payload.height,
                                    payload.bytes_per_row, payload.data)

    p_fast = Peripheral(dwell_ms=50)
    feed_motion_frame(p_fast, payload, t_ms=1000)
    fast_events = feed_motion_frame(p_fast, payload, t_ms=1060)  # crosses dwell_ms

    p_slow = Peripheral(dwell_ms=50)
    p_slow.observe(grid, 1000)
    slow_events = p_slow.observe(grid, 1060)

    assert fast_events == slow_events
    assert any(e.kind == "dwell" for e in fast_events)
    # once fired, a second identical frame must not re-fire it (either path)
    fast_events_2 = feed_motion_frame(p_fast, payload, t_ms=1120)
    slow_events_2 = p_slow.observe(grid, 1120)
    assert fast_events_2 == slow_events_2 == []


def test_fastpath_counts_toward_honest_watched_rate():
    p = Peripheral(dwell_ms=5_000)
    payload = FakePayload(width=16, height=10, bytes_per_row=16 * 4,
                          data=_solid_bgra(16, 10, 1, 2, 3))
    feed_motion_frame(p, payload, t_ms=0)      # slow path (first frame)
    feed_motion_frame(p, payload, t_ms=10)     # fast path (identical)
    feed_motion_frame(p, payload, t_ms=20)     # fast path (identical)
    rate = p.honest_rate()
    assert rate["offered"] == 3
    assert rate["watched"] == 3


# -- FOUND BY RUNNING THE DAEMON (2026-07-28): the eye pinned a core ------
def _bgra(width, height, fill):
    row = bytes(fill) * width
    return row * height


def test_the_numpy_grid_is_byte_identical_to_the_reference():
    """The fast path must be the SAME arithmetic, not an approximation —
    a sampling shortcut would change which regions are seen to move, and
    that is the eye's whole judgement."""
    import os
    from tracemem.extraction.peripheral import (
        _grayscale_grid_python, grayscale_grid_from_bgra,
    )
    w, h, bpr = 64, 40, 64 * 4 + 12          # padded stride, as real captures use
    data = os.urandom(h * bpr)
    assert grayscale_grid_from_bgra(w, h, bpr, data) == \
        _grayscale_grid_python(w, h, bpr, data)


def test_the_vectorised_region_verdicts_match_the_reference():
    """Same predicate, same block geometry, same edge clipping."""
    import os
    from tracemem.extraction.peripheral import (
        Peripheral, grayscale_grid_from_bgra,
    )
    w, h, bpr = 160, 100, 160 * 4
    a = bytearray(os.urandom(h * bpr))
    b = bytearray(a)
    for i in range(0, len(b) // 3, 4):        # move part of the frame
        b[i] = (b[i] + 90) % 256
    g1 = grayscale_grid_from_bgra(w, h, bpr, bytes(a))
    g2 = grayscale_grid_from_bgra(w, h, bpr, bytes(b))

    reference = {r for r in Peripheral._all_regions(g2)
                 if Peripheral._region_changed(g2, g1, r)}
    assert Peripheral._changed_regions(g2, g1) == reference


def test_an_unchanged_frame_moves_no_region_either_way():
    from tracemem.extraction.peripheral import Peripheral, grayscale_grid_from_bgra
    w, h, bpr = 96, 60, 96 * 4
    data = _bgra(w, h, (10, 20, 30, 255))
    g = grayscale_grid_from_bgra(w, h, bpr, data)
    assert Peripheral._changed_regions(g, g) == set()


def test_the_motion_path_stays_in_numpy_and_agrees_with_the_reference():
    """2026-10-09: tolist() + asarray() per frame kept the capture thread in
    Python and starved questions of the GIL. The array path must give the
    identical regions as the reference on real-shaped frames."""
    import numpy as np
    from tracemem.capture.capture_config import GRID_X, GRID_Y
    from tracemem.extraction import peripheral as P

    rng = np.random.default_rng(7)
    w, h = 480, 270
    assert w % GRID_X == 0 and h % GRID_Y == 0
    for trial in range(5):
        a = rng.integers(0, 256, (h, w * 4), dtype=np.uint8)
        b = a.copy()
        y0, x0 = rng.integers(0, h - 40), rng.integers(0, w - 60)
        b[y0:y0 + 40, x0 * 4:(x0 + 60) * 4] ^= 0x7F      # move one patch
        ga = P._grayscale_array(w, h, w * 4, a.tobytes())
        gb = P._grayscale_array(w, h, w * 4, b.tobytes())
        assert isinstance(ga, np.ndarray)
        assert ga.tolist() == P._grayscale_grid_python(w, h, w * 4, a.tobytes())
        fast = P.Peripheral._changed_regions(gb, ga)
        ref = {r for r in P.Peripheral._all_regions(gb.tolist())
               if P.Peripheral._region_changed(gb.tolist(), ga.tolist(), r)}
        assert fast == ref and fast


def test_untiled_frames_and_the_blur_kernel_agree_with_their_references():
    import numpy as np
    from tracemem.extraction import blur_gate, peripheral as P

    rng = np.random.default_rng(3)
    for (w, h) in ((480, 300), (517, 263), (64, 40)):
        a = rng.integers(0, 256, (h, w), dtype=np.int32)
        b = a.copy()
        b[: h // 3, : w // 2] = 255 - b[: h // 3, : w // 2]
        fast = P.Peripheral._changed_regions(b, a)
        ref = {r for r in P.Peripheral._all_regions(b.tolist())
               if P.Peripheral._region_changed(b.tolist(), a.tolist(), r)}
        assert fast == ref and fast, (w, h)
        crop = a[:30, :50]
        assert abs(blur_gate.laplacian_var(crop)
                   - blur_gate.laplacian_var(crop.tolist())) < 1e-6

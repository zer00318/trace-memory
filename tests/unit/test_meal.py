"""Meal preparation (Extraction / The feeding economy / Meal preparation).

Real geometry, synthetic pixels: full BGRA PixelPayloads built in-process
(same convention as tests/unit/test_retina_screen.py) so this exercises the
actual crop/upscale/blur/novelty math without live capture or gold frames.
"""
from __future__ import annotations

from tracemem.capture.retina_screen import PixelPayload
from tracemem.extraction.blur_gate import BLUR_FLOOR
from tracemem.extraction.diet import Bid, Diet, DietRegistry
from tracemem.extraction.auction import Auction
from tracemem.extraction.meal import (
    Crop,
    Meal,
    NoveltyLedger,
    make_meal_preparer,
    region_bbox,
    upscale_factor,
    upscale_nearest,
)
from tracemem.extraction.peripheral import GRID_X, GRID_Y
from tracemem.extraction.retina import FrameRecord, Retina

WIDTH, HEIGHT = 320, 200     # divisible cleanly by the 16x10 grid: 20x20 cells


def _solid_bgra(width, height, val=128):
    return bytes([val, val, val, 255]) * width * height


def _checker_bgra(width, height, lo=0, hi=255, square=2):
    """High-frequency checkerboard: sharp under the Laplacian, unlike a
    flat fill."""
    row_pixels = []
    for x in range(width):
        row_pixels.append(hi if (x // square) % 2 == 0 else lo)
    out = bytearray()
    for y in range(height):
        flip = (y // square) % 2
        for x in range(width):
            v = row_pixels[x] if flip == 0 else (hi + lo - row_pixels[x])
            out += bytes([v, v, v, 255])
    return bytes(out)


def _frame_with_region_pattern(region, pattern_bytes, base_val=128,
                                width=WIDTH, height=HEIGHT):
    """A WIDTHxHEIGHT flat BGRA frame with `pattern_bytes` stamped into the
    pixel rectangle for `region`, no margin (caller's pattern must already
    be sized to the raw cell, margin is added by region_bbox at read time)."""
    bw, bh = width // GRID_X, height // GRID_Y
    gx, gy = region
    x0, y0 = gx * bw, gy * bh
    canvas = bytearray(_solid_bgra(width, height, base_val))
    stride = width * 4
    for row in range(bh):
        src_off = row * bw * 4
        dst_off = (y0 + row) * stride + x0 * 4
        canvas[dst_off: dst_off + bw * 4] = pattern_bytes[src_off: src_off + bw * 4]
    return bytes(canvas)


def _push_frame(retina, data, t_ms=1000, display="main"):
    payload = PixelPayload(width=WIDTH, height=HEIGHT, bytes_per_row=WIDTH * 4, data=data)
    retina.push(FrameRecord(t_ms=t_ms, display=display, frame=payload, nbytes=len(data)))


# ------------------------------------------------------------- geometry --

def test_region_bbox_matches_peripheral_grid_with_margin():
    bw, bh = WIDTH // GRID_X, HEIGHT // GRID_Y
    x0, y0, x1, y1 = region_bbox((5, 3), WIDTH, HEIGHT, margin_frac=0.0)
    assert (x0, y0, x1, y1) == (5 * bw, 3 * bh, 6 * bw, 4 * bh)
    mx0, my0, mx1, my1 = region_bbox((5, 3), WIDTH, HEIGHT, margin_frac=0.5)
    assert mx0 < x0 and my0 < y0 and mx1 > x1 and my1 > y1


def test_region_bbox_clips_to_frame_edges():
    x0, y0, x1, y1 = region_bbox((0, 0), WIDTH, HEIGHT, margin_frac=1.0)
    assert x0 == 0 and y0 == 0                     # never negative


# --------------------------------------------------------------- upscale --

def test_small_crop_is_upscaled_others_are_not():
    assert upscale_factor(20, 20, min_dim=240) == 6     # ceil(240/20)=12, capped at default max 6
    assert upscale_factor(20, 20, min_dim=240, max_upscale=20) == 12  # uncapped: the real ceil
    assert upscale_factor(300, 300, min_dim=240) == 1


def test_upscale_nearest_duplicates_pixels_without_inventing_detail():
    small = Crop(width=2, height=2, bytes_per_row=8,
                 data=bytes([10, 10, 10, 255, 20, 20, 20, 255,
                             30, 30, 30, 255, 40, 40, 40, 255]))
    big = upscale_nearest(small, 3)
    assert (big.width, big.height) == (6, 6)
    # top-left 3x3 block is all the original top-left pixel
    for y in range(3):
        row = big.data[y * big.bytes_per_row: y * big.bytes_per_row + 12]
        assert row == bytes([10, 10, 10, 255] * 3)


def test_upscale_factor_one_is_identity():
    c = Crop(width=5, height=5, bytes_per_row=20, data=b"x" * 100)
    assert upscale_nearest(c, 1) is c


# ------------------------------------------------------------ blur gate --

def test_prepared_meal_flags_sharp_region_readable_and_flat_region_not():
    """A pin+unpin cycle deliberately consumes the pinned FrameRecord out
    of the Retina (pixels-die law: once a scene has been read, it is not
    kept around "just in case") — so each prepare() call here re-lands a
    fresh copy of the same frame first, exactly as continuous capture
    would between two auction beats."""
    retina = Retina()
    bw, bh = WIDTH // GRID_X, HEIGHT // GRID_Y
    sharp_pattern = _checker_bgra(bw, bh, square=1)
    data = _frame_with_region_pattern((4, 4), sharp_pattern)
    prepare = make_meal_preparer(retina, clock=lambda: 2000)

    _push_frame(retina, data, t_ms=1000)
    sharp_meal = prepare((4, 4))
    _push_frame(retina, data, t_ms=1001)
    flat_meal = prepare((10, 8))     # untouched, still the flat base fill

    assert sharp_meal.readable is True
    assert sharp_meal.blur_score >= BLUR_FLOOR
    assert flat_meal.readable is False
    assert flat_meal.blur_score < BLUR_FLOOR


def test_meal_ocrs_better_proxy_sharp_crop_score_beats_full_frame_score():
    """Proxy for the atom's real oracle (banked gold frames + real OCR,
    both out of scope this session: no live capture, gold/frames/ untouched).
    Stand-in claim actually checked here: the prepared crop's own
    readability score is not silently worse than judging the WHOLE raw
    frame at once (a flat frame with one sharp region scores low as a
    whole; the prepared meal isolates and correctly reports the sharp
    region as readable)."""
    from tracemem.extraction.blur_gate import laplacian_var
    from tracemem.extraction.peripheral import grayscale_grid_from_bgra

    retina = Retina()
    bw, bh = WIDTH // GRID_X, HEIGHT // GRID_Y
    sharp_pattern = _checker_bgra(bw, bh, square=1)
    data = _frame_with_region_pattern((4, 4), sharp_pattern)
    _push_frame(retina, data)
    prepare = make_meal_preparer(retina, clock=lambda: 2000)

    meal = prepare((4, 4))
    full_gray = grayscale_grid_from_bgra(WIDTH, HEIGHT, WIDTH * 4, data)
    full_score = laplacian_var(full_gray)

    assert meal.readable is True
    assert meal.blur_score > full_score      # the meal sees the sharp signal
                                              # the diluted whole frame hides


# --------------------------------------------------------------- novelty --

def test_novelty_ledger_silences_a_repeat_of_unchanged_content():
    """Two successive captures of the SAME unchanged content (the pin+unpin
    cycle consumes each FrameRecord, so a fresh push stands in for the next
    capture tick — see the readable/flat test above for why)."""
    retina = Retina()
    data = _solid_bgra(WIDTH, HEIGHT, 100)
    prepare = make_meal_preparer(retina, clock=lambda: 1000)

    _push_frame(retina, data, t_ms=1000)
    first = prepare((2, 2))
    _push_frame(retina, data, t_ms=1001)
    second = prepare((2, 2))     # same content, same region: nothing changed
    assert first.novel is True
    assert second.novel is False


def test_novelty_ledger_wakes_back_up_when_region_content_changes():
    retina = Retina()
    bw, bh = WIDTH // GRID_X, HEIGHT // GRID_Y
    base = _solid_bgra(WIDTH, HEIGHT, 100)
    _push_frame(retina, base, t_ms=1000)
    prepare = make_meal_preparer(retina, clock=lambda: 1000)
    prepare((2, 2))    # first sighting: consumes novelty

    changed_pattern = _checker_bgra(bw, bh, square=1)
    changed = _frame_with_region_pattern((2, 2), changed_pattern, base_val=100)
    _push_frame(retina, changed, t_ms=2000)
    again = prepare((2, 2))
    assert again.novel is True


def test_ledger_is_per_region_not_global():
    ledger = NoveltyLedger()
    gray_a = [[1, 2], [3, 4]]
    gray_b = [[9, 9], [9, 9]]
    assert ledger.check_and_mark("region-a", gray_a) is True
    assert ledger.check_and_mark("region-b", gray_a) is True   # first sight, different key
    assert ledger.check_and_mark("region-a", gray_a) is False  # unchanged
    assert ledger.check_and_mark("region-a", gray_b) is True   # changed


# -------------------------------------------------------------- pinning --

def test_prepare_pins_and_unpins_leaving_no_leaked_pin():
    retina = Retina()
    data = _solid_bgra(WIDTH, HEIGHT, 100)
    _push_frame(retina, data)
    prepare = make_meal_preparer(retina, clock=lambda: 1000)

    meal = prepare((1, 1))
    assert meal.pin_secured is True
    assert retina._pins == {}     # unpinned again after the crop was read
    assert retina.bytes_pinned == 0


def test_prepare_returns_none_when_retina_is_empty():
    retina = Retina()
    prepare = make_meal_preparer(retina, clock=lambda: 1000)
    assert prepare((0, 0)) is None


def test_prepare_returns_none_for_opaque_non_geometry_frame():
    """The pre-capture_geometry reading-eye path pushes raw bytes with no
    width/height — meal prep must refuse loudly (None), not guess."""
    retina = Retina()
    retina.push(FrameRecord(t_ms=1000, display="main", frame=b"\xffJPEGISH", nbytes=8))
    prepare = make_meal_preparer(retina, clock=lambda: 1000)
    assert prepare((0, 0)) is None


def test_provenance_ties_meal_to_region_and_time():
    retina = Retina()
    _push_frame(retina, _solid_bgra(WIDTH, HEIGHT, 100))
    prepare = make_meal_preparer(retina, clock=lambda: 4242)
    meal = prepare((3, 3))
    assert meal.provenance == "(3, 3)@4242"
    assert meal.t_ms == 4242 and meal.region == (3, 3)


# ------------------------------------------------------ wired into Auction --

def test_meal_preparer_plugs_into_the_real_auction():
    """auction.py's Auction takes any Callable[[Region], Any] as `prepare`
    and calls it exactly once per winning gaze; this proves the real
    preparer satisfies that contract end-to-end, not just standalone."""
    retina = Retina()
    _push_frame(retina, _solid_bgra(WIDTH, HEIGHT, 100))
    preparer = make_meal_preparer(retina, clock=lambda: 5000)

    registry = DietRegistry()
    registry.register(Diet("ocr"))
    auction = Auction(registry, prepare=preparer)

    class _Ev:
        def __init__(self, kind, region):
            self.kind, self.region = kind, region

    auction.on_events([_Ev("change", (2, 2))])
    gaze = auction.beat(now_ms=0)

    assert isinstance(gaze.meal, Meal)
    assert gaze.meal.region == (2, 2)
    assert auction.prepare_calls == 1
    assert retina._pins == {}          # the preparer cleaned up after itself

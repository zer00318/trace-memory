"""OWNS: full-rate watching of the COMPLETE stream (map: Extraction /
Peripheral vision).

Every frame gets the cheap look — a region-grid diff that says WHERE
changed, not just that something did. Still regions accrue stability;
crossing the dwell threshold emits ONE dwell event per stillness period
(the Nutella trigger). It counts its own honest coverage rate: frames
offered by capture vs frames actually watched — the number the cockpit
shows, never estimated. Pure: frames are downscaled grayscale grids
(the adapter's job), same convention as change_gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from tracemem.capture.capture_config import GRID_X, GRID_Y

PIX_DELTA = 12          # per-pixel difference that counts as movement
REGION_FRAC = 0.15      # fraction of a region's pixels moving = region changed
DWELL_MS = 8_000        # stillness that earns a deeper look

Frame = Sequence[Sequence[int]]
Region = Tuple[int, int]


@dataclass(frozen=True)
class RegionEvent:
    kind: str           # "change" | "dwell"
    region: Region
    t_ms: int


class Peripheral:
    def __init__(self, dwell_ms: int = DWELL_MS):
        self.dwell_ms = dwell_ms
        self._last: Optional[Frame] = None
        self._still_since: Dict[Region, int] = {}
        self._dwell_fired: Dict[Region, bool] = {}
        self._offered = 0
        self._watched = 0
        # Opaque to this class's own logic — never read by observe()/
        # _region_changed(). Solely a slot for the real-frame wiring
        # (feed_motion_frame, below) to cache the last raw BGRA bytes it
        # built a grid from, so it can prove byte-identical consecutive
        # frames without importing anything capture-specific in here.
        # See CPU diagnosis, eval_runs/motion_cpu_profile_20260719.md.
        self._last_raw: Optional[bytes] = None

    # -- honest rate -------------------------------------------------------
    def mark_offered(self, n: int = 1) -> None:
        self._offered += n

    def honest_rate(self) -> dict:
        return {"offered": self._offered, "watched": self._watched,
                "rate": (self._watched / self._offered) if self._offered else 1.0}

    # -- the cheap look, every frame ---------------------------------------
    def observe(self, frame: Frame, t_ms: int) -> List[RegionEvent]:
        self._watched += 1
        events: List[RegionEvent] = []
        if self._last is None:
            self._last = frame
            for region in self._all_regions(frame):
                self._still_since[region] = t_ms
                self._dwell_fired[region] = False
            return [RegionEvent("change", r, t_ms)
                    for r in self._all_regions(frame)]
        changed = self._changed_regions(frame, self._last)
        for region in self._all_regions(frame):
            if region in changed:
                events.append(RegionEvent("change", region, t_ms))
                self._still_since[region] = t_ms
                self._dwell_fired[region] = False
            elif (not self._dwell_fired.get(region, False)
                  and t_ms - self._still_since.get(region, t_ms) >= self.dwell_ms):
                events.append(RegionEvent("dwell", region, t_ms))
                self._dwell_fired[region] = True     # once per stillness period
        self._last = frame
        return events

    # -- fast path: a frame proven byte-identical to the last one ----------
    def observe_unchanged(self, t_ms: int) -> List[RegionEvent]:
        """Same contract as observe(), for a caller that has ALREADY proven
        (by a cheaper means — see feed_motion_frame's raw-bytes check) that
        this frame is identical to self._last. Byte-identical input means
        no region can have changed, so this skips the O(width*height) pixel
        diff entirely and only runs the O(regions) dwell-timer bookkeeping
        every frame must still perform. Produces exactly the RegionEvent
        list observe() would produce for a frame equal to self._last —
        pinned by tests/unit/test_peripheral.py's
        test_fastpath_matches_full_observe_on_identical_frame_no_dwell_yet
        and test_fastpath_fires_dwell_event_matching_full_observe.

        CPU diagnosis (2026-07-19, eval_runs/motion_cpu_profile_20260719.md):
        this is the fix for the motion-sense oracle's 32%-CPU-on-a-still-
        screen failure — a still screen means (near-)identical consecutive
        frames, and the old code ran the full per-pixel diff on every one
        of them regardless.
        """
        self._watched += 1
        if self._last is None:
            # Nothing to compare against — caller should not reach this
            # branch (feed_motion_frame only takes this path once a prior
            # full observe() has run), but stay honest rather than silently
            # doing nothing.
            return []
        events: List[RegionEvent] = []
        for region in self._all_regions(self._last):
            if (not self._dwell_fired.get(region, False)
                    and t_ms - self._still_since.get(region, t_ms) >= self.dwell_ms):
                events.append(RegionEvent("dwell", region, t_ms))
                self._dwell_fired[region] = True     # once per stillness period
        return events

    # -- geometry ----------------------------------------------------------
    @staticmethod
    def _all_regions(frame: Frame) -> List[Region]:
        return [(gx, gy) for gy in range(GRID_Y) for gx in range(GRID_X)]

    @classmethod
    def _changed_regions(cls, frame: Frame, last: Frame) -> set:
        """Which regions moved. Vectorised when numpy is importable.

        The per-region path below walks EVERY pixel of the frame in Python,
        once per region — 129,600 comparisons per 480x270 frame, measured at
        14 ms/frame, 11% of a core at 8 fps, on top of the grid conversion.
        The eye pinned a core and starved the loop that answers questions
        (see grayscale_grid_from_bgra).

        This computes the identical predicate: |a - b| > PIX_DELTA counted
        per region block, changed when the moved FRACTION reaches
        REGION_FRAC. Same block geometry, same clipping at the edges, same
        `total > 0` guard — `_region_changed` remains the reference and a
        test asserts the two agree region-for-region on real-shaped frames.
        """
        if _np is None:
            return {r for r in cls._all_regions(frame)
                    if cls._region_changed(frame, last, r)}
        a = _np.asarray(frame, dtype=_np.int32)
        b = _np.asarray(last, dtype=_np.int32)
        if a.shape != b.shape or a.ndim != 2:
            return {r for r in cls._all_regions(frame)
                    if cls._region_changed(frame, last, r)}
        h, w = a.shape
        bh, bw = max(1, h // GRID_Y), max(1, w // GRID_X)
        moved_mask = _np.abs(a - b) > PIX_DELTA
        if h >= GRID_Y and w >= GRID_X:
            # One reshape, no loop. Blocks are bh x bw from the origin and the
            # reference ignores the remainder rows/columns past GRID*block
            # (y1 = min(gy*bh + bh, h) never reaches them), so cropping to
            # the tiled area computes the identical predicate for ANY frame
            # size — the founder's display is not an exact multiple, which
            # kept the live eye on the per-block loop.
            tiled = moved_mask[:GRID_Y * bh, :GRID_X * bw]
            frac = tiled.reshape(GRID_Y, bh, GRID_X, bw).mean(axis=(1, 3))
            ys, xs = _np.nonzero(frac >= REGION_FRAC)
            return {(int(x), int(y)) for y, x in zip(ys, xs)}
        out = set()
        for gy in range(GRID_Y):
            y0, y1 = gy * bh, min(gy * bh + bh, h)
            if y1 <= y0:
                continue
            for gx in range(GRID_X):
                x0, x1 = gx * bw, min(gx * bw + bw, w)
                if x1 <= x0:
                    continue
                block = moved_mask[y0:y1, x0:x1]
                total = block.size
                if total and (int(block.sum()) / total) >= REGION_FRAC:
                    out.add((gx, gy))
        return out

    @staticmethod
    def _region_changed(frame: Frame, last: Frame, region: Region) -> bool:
        h, w = len(frame), len(frame[0])
        bh, bw = max(1, h // GRID_Y), max(1, w // GRID_X)
        gx, gy = region
        y0, x0 = gy * bh, gx * bw
        moved = total = 0
        for y in range(y0, min(y0 + bh, h)):
            frow, lrow = frame[y], last[y]
            for x in range(x0, min(x0 + bw, w)):
                total += 1
                if abs(frow[x] - lrow[x]) > PIX_DELTA:
                    moved += 1
        return total > 0 and (moved / total) >= REGION_FRAC


# -- real-frame wiring: the motion-sense stream feeds this kernel ----------
#
# Ruling (z): the motion-sense stream (World / Motion sense stream,
# capture/retina_screen.py) is configured ~480p by SCStreamConfiguration
# itself, so ScreenCaptureKit has already done the downscaling natively.
# The only work left here is FORMAT conversion — native BGRA8 bytes to the
# grayscale int grid this module has always consumed — never a software
# resize. Kept decoupled from the capture layer: this takes plain
# width/height/bytes_per_row/data, not an import of PixelPayload, so
# peripheral.py stays capture-agnostic (duck-typed against
# capture/retina_screen.py's PixelPayload attribute names).

# Per-channel Rec. 601 luma weight tables (CPU diagnosis, 2026-07-19,
# eval_runs/motion_cpu_profile_20260719.md): replaces the per-pixel
# "r*299 + g*587 + b*114" multiply with a list lookup. Table lookups still
# cost a Python-level index+add per pixel (no numpy in this venv — see the
# profile doc for why a full vectorized rewrite is out of scope for this
# atom), but the profiled ~15-20% saving is real and free: summing three
# LUT lookups then flooring ONCE reproduces the original integer math
# exactly (byte-identical grids — pinned by
# test_grayscale_lut_matches_naive_formula), unlike pre-dividing each
# channel separately (which rounds three times instead of once and drifts
# by up to 2 luma levels — measured and rejected during profiling).
try:                                  # numpy is already a hard dep
    import numpy as _np                   # (sentence-transformers pulls it)
except Exception:                         # pragma: no cover
    _np = None

_LUT_R = [v * 299 for v in range(256)]
_LUT_G = [v * 587 for v in range(256)]
_LUT_B = [v * 114 for v in range(256)]


def grayscale_grid_from_bgra(width: int, height: int, bytes_per_row: int,
                              data: bytes) -> Frame:
    """Adapter: one native BGRA8 CVPixelBuffer payload -> a grayscale int
    grid (Rec. 601 luma). bytes_per_row may exceed width*4 (row padding);
    honoring it (rather than assuming a tight stride) is what makes this
    correct on real captures, not just synthetic test grids.

    Sliced per-channel (b/g/r = row_bytes[0::4] etc, a C-level strided
    slice) rather than a per-byte indexed inner loop: witnessed on real
    480x270 motion-sense frames (Full-rate bench atom, 2026-07-19) at
    ~3x the naive indexed version. Still pure-Python O(w*h) per frame —
    the honest remaining CPU cost is documented on that atom, not hidden
    here.

    THE EYE USED TO PIN A CORE AT 100% (found by running the daemon,
    2026-07-28). This is 129,600 luma values per frame at 480x270, eight
    times a second, in CPython — and it saturated the GIL, which starved the
    daemon loop that serves questions: `--stats` timed out at 25s and an ask
    at 120s WHILE capture itself kept working. The product could watch or be
    asked, never both. Measured with the eye alone, no brain, no binding:
    99.7% CPU, steady.

    So the same arithmetic runs vectorised when numpy is importable. Not a
    different algorithm and not a sampling approximation — the identical
    Rec. 601 integer luma with the identical //1000 truncation, which is why
    the pure-Python path stays right here as the reference and a test asserts
    the two agree BYTE FOR BYTE on real-shaped frames. numpy is already a
    hard dependency (sentence-transformers pulls it), so this adds nothing to
    install; the fallback exists so the module never becomes unimportable on
    a machine that somehow lacks it.
    """
    if _np is not None:
        return _grayscale_grid_numpy(width, height, bytes_per_row, data)
    return _grayscale_grid_python(width, height, bytes_per_row, data)


def _grayscale_array(width: int, height: int, bytes_per_row: int,
                     data: bytes):
    """The same luma as `_grayscale_grid_numpy`, LEFT AS AN ARRAY, or None.

    THE MOTION PATH NEVER LEAVES NUMPY (2026-10-09). `luma.tolist()` built
    ~130,000 Python ints per frame and `_changed_regions` turned them straight
    back into an array, twice per frame (this frame and the last). That is
    pure-Python work on the capture thread at every frame, holding the GIL:
    a sampled live daemon had the capture thread in `_changed_regions` most
    of the time while a question read 28k claims in 57s of wall and 11s of
    its own CPU.
    """
    if _np is None:
        return None
    arr = _np.frombuffer(data, dtype=_np.uint8)
    usable = height * bytes_per_row
    if arr.size < usable:
        return None
    rows = arr[:usable].reshape(height, bytes_per_row)
    px = rows[:, : width * 4].reshape(height, width, 4)
    return (px[:, :, 2].astype(_np.int32) * 299
            + px[:, :, 1].astype(_np.int32) * 587
            + px[:, :, 0].astype(_np.int32) * 114) // 1000


def _grayscale_grid_numpy(width: int, height: int, bytes_per_row: int,
                          data: bytes) -> Frame:
    arr = _np.frombuffer(data, dtype=_np.uint8)
    usable = height * bytes_per_row
    if arr.size < usable:                     # short buffer: fall back rather
        return _grayscale_grid_python(width, height, bytes_per_row, data)
    rows = arr[:usable].reshape(height, bytes_per_row)
    px = rows[:, : width * 4].reshape(height, width, 4)
    b = px[:, :, 0].astype(_np.int32)
    g = px[:, :, 1].astype(_np.int32)
    r = px[:, :, 2].astype(_np.int32)
    luma = (r * 299 + g * 587 + b * 114) // 1000
    return luma.tolist()


def _grayscale_grid_python(width: int, height: int, bytes_per_row: int,
                           data: bytes) -> Frame:
    """The reference implementation. Kept because it is the definition of
    what the fast path must reproduce."""
    grid: List[List[int]] = []
    lut_r, lut_g, lut_b = _LUT_R, _LUT_G, _LUT_B   # locals: faster lookup in CPython
    for y in range(height):
        row_off = y * bytes_per_row
        row_bytes = data[row_off: row_off + width * 4]
        b_ch = row_bytes[0::4]
        g_ch = row_bytes[1::4]
        r_ch = row_bytes[2::4]
        grid.append([(lut_r[r] + lut_g[g] + lut_b[b]) // 1000
                     for b, g, r in zip(b_ch, g_ch, r_ch)])
    return grid


def feed_motion_frame(peripheral: "Peripheral", frame_payload, t_ms: int) -> List[RegionEvent]:
    """Wiring atom (Extraction / Diff kernel): consume one motion-sense
    FrameRecord.frame (duck-typed: .width/.height/.bytes_per_row/.data —
    see capture/retina_screen.py PixelPayload) and run it through the
    diff kernel. Counts toward the honest offered/watched rate so the
    cockpit's coverage number stays truthful for this stream too.

    Raw-bytes fast path (CPU diagnosis, 2026-07-19,
    eval_runs/motion_cpu_profile_20260719.md): before paying for the
    O(width*height) grayscale conversion, check whether this frame's raw
    BGRA bytes are byte-for-byte identical to the previous frame's. A
    still screen means ScreenCaptureKit is redelivering (near-)identical
    buffers at the configured fps; identical raw bytes DETERMINISTICALLY
    imply an identical grid, so the expensive conversion + per-pixel diff
    can be skipped entirely in favor of a single C-level bytes comparison
    (~15us for a 480x270 BGRA frame, vs ~25-30ms for the full path —
    profiled, not estimated). Peripheral.observe_unchanged() still runs
    the real (cheap, O(regions)) dwell-timer bookkeeping, so behavior is
    unchanged, not skipped.
    """
    peripheral.mark_offered()
    if frame_payload.data == peripheral._last_raw:
        return peripheral.observe_unchanged(t_ms)
    grid = _grayscale_array(frame_payload.width, frame_payload.height,
                            frame_payload.bytes_per_row, frame_payload.data)
    if grid is None:
        grid = grayscale_grid_from_bgra(frame_payload.width,
                                         frame_payload.height,
                                         frame_payload.bytes_per_row,
                                         frame_payload.data)
    peripheral._last_raw = frame_payload.data
    return peripheral.observe(grid, t_ms)

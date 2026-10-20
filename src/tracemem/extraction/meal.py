"""OWNS: meal preparation — turning a won gaze into a servable Meal (map:
Extraction / The feeding economy / Meal preparation).

A meal is prepared ONCE per winning gaze (auction.py's `beat` enforces the
"once"; this module owns what happens INSIDE that one call):

  1. pin the region's scene in the Retina, so it cannot be evicted out from
     under us mid-preparation (retina.py's pin/unpin API);
  2. crop to the won region plus a margin (a grid cell alone often clips
     the thing being read; the margin gives OCR/specialists context);
  3. upscale if the crop is small (a tiny won region rendered at native
     res is often sub-legible; nearest-neighbor is honest about not
     inventing detail, unlike a smoothing resample);
  4. run the blur gate (blur_gate.py) so an unreadable crop is flagged,
     never silently fed to a specialist as if it were legible;
  5. consult the novelty ledger so a meal already served for unchanged
     content is not re-served ("a meal already served to a diet is not
     re-served").

Scope note (read before assuming this closes the Novelty-ledger atom):
the claim-level Said-things memory (campaign/tree/02_extraction.json,
Extraction / Novelty ledger / Said-things memory — 5% DESIGNING as of
2026-07-19, needs Document identity, which does not exist yet either) is
NOT built here. `NoveltyLedger` below is a coarse, pixel-level "did this
crop's content change since we last served this region" check — cheap,
synchronous, and honest about being a stand-in. It answers the meal-prep
question ("is there anything new to bother serving") without pretending
to answer the harder claim-level question ("was this exact FACT already
said"). Replace it the moment the semantic ledger lands; do not extend
it to do semantic work.

Pure at the seam: this module never imports the capture layer. Frames are
consumed duck-typed against width/height/bytes_per_row/data (same
convention peripheral.py uses for capture/retina_screen.py's PixelPayload)
so extraction stays capture-agnostic.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from .blur_gate import BLUR_FLOOR, is_readable, laplacian_var
from .peripheral import GRID_X, GRID_Y, grayscale_grid_from_bgra
from .retina import Retina

Region = Tuple[int, int]
BBox = Tuple[int, int, int, int]   # (x0, y0, x1, y1) in the source frame's pixels

MARGIN_FRAC = 0.15      # crop margin beyond the won grid cell, as a fraction of
                         # the cell's own size; GUESS — no calibration probe yet,
                         # labeled rather than asserted (DOCTRINE §"constants get
                         # provenance"). Calibrate against banked gold frames.
MIN_CROP_DIM = 240      # px; below this we upscale. GUESS — same caveat as above.
MAX_UPSCALE = 6         # refuse to invent more than 6x from real pixels


@dataclass(frozen=True)
class Crop:
    """Duck-typed the same shape as capture/retina_screen.py's PixelPayload
    (width/height/bytes_per_row/data) without importing it — extraction
    stays capture-agnostic."""
    width: int
    height: int
    bytes_per_row: int
    data: bytes


@dataclass(frozen=True)
class Meal:
    """The prepared serving for a won gaze. Broadcast to every matching
    diet unchanged (auction.py does the broadcast; this object is what
    gets broadcast)."""
    region: Region
    t_ms: int                  # when this meal was prepared
    scene_t_ms: int            # timestamp of the FrameRecord it was cropped from
    bbox_px: BBox               # crop rectangle in the SOURCE frame's pixels
    crop: Crop                  # the (possibly upscaled) served pixels
    scale_factor: int           # 1 = no upscale
    blur_score: float           # laplacian variance of the served crop
    readable: bool               # blur gate verdict on the served crop
    novel: bool                  # False = unchanged since last served (ledger)
    pin_secured: bool            # True iff the Retina held a pin during crop work
    provenance: str              # gaze-id: ties this meal back to its Gaze


class NoveltyLedger:
    """Coarse, pixel-level "already served" check — see module docstring's
    scope note. Keyed by whatever scene-identity the caller passes (the
    region, for now; swap for real document/scene identity once that atom
    lands). Stores a cheap, deterministic content signature (per-row
    grayscale sums) of the last crop served for that identity."""

    def __init__(self) -> None:
        self._last_signature: Dict[Any, Tuple[int, ...]] = {}

    @staticmethod
    def _signature(gray_rows) -> Tuple[int, ...]:
        return tuple(sum(row) for row in gray_rows)

    def check_and_mark(self, scene_key: Any, gray_rows) -> bool:
        """Returns True if NOVEL (content changed, or first sighting of
        this key); always updates the ledger, win or lose."""
        sig = self._signature(gray_rows)
        prev = self._last_signature.get(scene_key)
        self._last_signature[scene_key] = sig
        return prev != sig


# -- geometry: region (grid cell) -> pixel bbox, margin included ----------

def region_bbox(region: Region, width: int, height: int,
                grid_x: int = GRID_X, grid_y: int = GRID_Y,
                margin_frac: float = MARGIN_FRAC) -> BBox:
    """Same grid convention as peripheral.py's _region_changed (bw/bh via
    floor division), so a region reported by the auction always maps to
    the same rectangle the peripheral watcher meant."""
    bw = max(1, width // grid_x)
    bh = max(1, height // grid_y)
    gx, gy = region
    x0, y0 = gx * bw, gy * bh
    x1, y1 = min(x0 + bw, width), min(y0 + bh, height)
    mx, my = int(bw * margin_frac), int(bh * margin_frac)
    x0, y0 = max(0, x0 - mx), max(0, y0 - my)
    x1, y1 = min(width, x1 + mx), min(height, y1 + my)
    return x0, y0, x1, y1


def crop_bgra(frame: Any, bbox: BBox) -> Crop:
    """frame is duck-typed: .width/.height/.bytes_per_row/.data (native
    BGRA8, row stride may exceed width*4 — honored, never assumed tight)."""
    x0, y0, x1, y1 = bbox
    crop_w, crop_h = max(0, x1 - x0), max(0, y1 - y0)
    out_stride = crop_w * 4
    rows = []
    for y in range(y0, y1):
        row_off = y * frame.bytes_per_row + x0 * 4
        rows.append(frame.data[row_off: row_off + out_stride])
    return Crop(width=crop_w, height=crop_h, bytes_per_row=out_stride,
                data=b"".join(rows))


def upscale_factor(width: int, height: int,
                    min_dim: int = MIN_CROP_DIM,
                    max_upscale: int = MAX_UPSCALE) -> int:
    smallest = min(width, height)
    if smallest <= 0 or smallest >= min_dim:
        return 1
    factor = -(-min_dim // smallest)     # ceil division, no float rounding
    return min(factor, max_upscale)


def upscale_nearest(crop: Crop, factor: int) -> Crop:
    """Nearest-neighbor pixel duplication. Deliberately not a smoothing
    resample: it never invents detail, so a downstream blur-gate verdict
    on the upscaled crop still reflects the SOURCE sharpness, not a
    resample artifact."""
    if factor <= 1:
        return crop
    w, h = crop.width, crop.height
    new_w, new_h = w * factor, h * factor
    out_stride = new_w * 4
    out_rows = []
    for y in range(h):
        row_off = y * crop.bytes_per_row
        row = crop.data[row_off: row_off + w * 4]
        big_row = bytearray()
        for px in range(w):
            pixel = row[px * 4: px * 4 + 4]
            big_row += pixel * factor
        big_row = bytes(big_row)
        out_rows.extend([big_row] * factor)
    return Crop(width=new_w, height=new_h, bytes_per_row=out_stride,
                data=b"".join(out_rows))


# -- the prepare callable Auction.beat() plugs in as `prepare` -------------

def make_meal_preparer(
    retina: Retina,
    ledger: Optional[NoveltyLedger] = None,
    display: Optional[str] = None,
    clock: Callable[[], int] = lambda: int(time.time() * 1000),
    grid: Tuple[int, int] = (GRID_X, GRID_Y),
    margin_frac: float = MARGIN_FRAC,
) -> Callable[[Region], Optional[Meal]]:
    """Builds the `prepare(region) -> Meal` closure auction.Auction takes.
    One Retina, one ledger, one display per preparer — matches one Auction
    per gaze economy instance.

    Returns None (loud absence, not a fake meal) when there is no scene to
    prepare from, or when the current scene has no pixel geometry (a raw
    opaque-bytes FrameRecord, e.g. the stand-in capture path before
    capture_geometry=True lands everywhere) — cropping needs width/height.
    """
    ledger = ledger if ledger is not None else NoveltyLedger()
    grid_x, grid_y = grid

    def prepare(region: Region) -> Optional[Meal]:
        now_ms = clock()
        scene = retina.scene_now(display)
        if scene is None:
            return None
        frame = scene.frame
        if not all(hasattr(frame, a) for a in ("width", "height", "bytes_per_row", "data")):
            return None    # opaque payload: no geometry to crop against

        # pin the exact record we are about to read, so a concurrent push
        # cannot evict it mid-crop (retina.py's pin/unpin API)
        pin = retina.pin(since_ms=scene.t_ms, now_ms=now_ms, display=display)
        try:
            bbox = region_bbox(region, frame.width, frame.height,
                                grid_x, grid_y, margin_frac)
            crop = crop_bgra(frame, bbox)
            factor = upscale_factor(crop.width, crop.height)
            crop = upscale_nearest(crop, factor)

            gray = grayscale_grid_from_bgra(crop.width, crop.height,
                                             crop.bytes_per_row, crop.data)
            score = laplacian_var(gray)
            readable = is_readable(gray, BLUR_FLOOR)
            novel = ledger.check_and_mark(region, gray)

            return Meal(
                region=region, t_ms=now_ms, scene_t_ms=scene.t_ms,
                bbox_px=bbox, crop=crop, scale_factor=factor,
                blur_score=score, readable=readable, novel=novel,
                pin_secured=pin is not None,
                provenance=f"{region}@{now_ms}",
            )
        finally:
            if pin is not None:
                retina.unpin(pin.token)

    return prepare

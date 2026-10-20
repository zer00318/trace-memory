"""OWNS: the L1 rung — a prepared Meal in, screen-anchored text runs out.

The weld between the feeding economy and the reading eye:
  Meal.crop (raw pixels, never a file) → Vision OCR → TextRuns whose
  boxes are mapped back through the meal's crop rect to ABSOLUTE source-
  frame pixels — the coordinates the Binder's world runs on.

Pixel format is inferred from bytes_per_row/width: 1 byte/px = gray8,
4 = BGRA8888 (capture's native). Anything else is refused loudly —
a guessed pixel format reads garbage and grades it VERBATIM, the worst
possible lie.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from tracemem.extraction.coord_map import CropRect, to_screen
from tracemem.extraction.ocr_vision import TextRun


@dataclass(frozen=True)
class ScreenRun:
    """A read text run in ABSOLUTE source-frame pixel coordinates."""
    text: str
    x_px: float
    y_px: float
    w_px: float
    h_px: float
    confidence: float


def recognize_pixels(width: int, height: int, bytes_per_row: int, data: bytes,
                     languages: Optional[Sequence[str]] = None,
                     fast: bool = False) -> List[TextRun]:
    """OCR raw pixels (a Meal's crop) without touching disk."""
    import Quartz
    import Vision

    bpp = bytes_per_row // max(1, width)
    if bpp == 1:
        cs = Quartz.CGColorSpaceCreateDeviceGray()
        bits, info = 8, Quartz.kCGImageAlphaNone
    elif bpp == 4:
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        bits, info = 8, (Quartz.kCGImageAlphaNoneSkipFirst |
                         Quartz.kCGBitmapByteOrder32Little)   # BGRA8888
    else:
        raise ValueError(f"unsupported pixel format: {bpp} bytes/px "
                         f"(bytes_per_row={bytes_per_row}, width={width}) — "
                         f"refusing to guess; garbage graded VERBATIM is the worst lie")
    provider = Quartz.CGDataProviderCreateWithData(None, bytes(data), len(data), None)
    image = Quartz.CGImageCreate(width, height, bits, bits * bpp, bytes_per_row,
                                 cs, info, provider, None, False,
                                 Quartz.kCGRenderingIntentDefault)
    if image is None:
        raise ValueError("CGImageCreate refused the buffer — dimensions/stride lie")

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelFast if fast
        else Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)         # verbatim law
    if languages:
        request.setRecognitionLanguages_(list(languages))
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision failed on pixel buffer: {err}")

    runs: List[TextRun] = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)
        if not cand or not len(cand):
            continue
        bb = obs.boundingBox()
        runs.append(TextRun(
            text=str(cand[0].string()),
            x=float(bb.origin.x),
            y=1.0 - float(bb.origin.y) - float(bb.size.height),
            w=float(bb.size.width), h=float(bb.size.height),
            confidence=float(cand[0].confidence())))
    return runs


def read_meal(meal) -> List[ScreenRun]:
    """The L1 rung proper: OCR the meal's crop; anchor every run on the
    SOURCE frame in absolute pixels via the meal's crop rectangle.
    Respects the gate: an unreadable or stale meal is refused upstream —
    this function trusts `readable`/`novel` were already honored and
    only asserts them (defense against a mis-wired auction)."""
    if not meal.readable:
        raise ValueError("read_meal fed an unreadable meal — the blur gate "
                         "exists upstream; a mis-wired auction must fail loudly")
    x0, y0, x1, y1 = (meal.bbox_px.x0, meal.bbox_px.y0,
                      meal.bbox_px.x1, meal.bbox_px.y1) \
        if hasattr(meal.bbox_px, "x0") else meal.bbox_px
    crop_rect = CropRect(x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0))
    runs = recognize_pixels(meal.crop.width, meal.crop.height,
                            meal.crop.bytes_per_row, meal.crop.data)
    out: List[ScreenRun] = []
    for r in runs:
        px, py, pw, ph = to_screen(r.x, r.y, r.w, r.h, crop_rect)
        out.append(ScreenRun(text=r.text, x_px=px, y_px=py, w_px=pw, h_px=ph,
                             confidence=r.confidence))
    return out

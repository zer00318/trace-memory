"""OWNS: macOS Vision OCR adapter (impl ports.OCR); bboxes NORMALIZED here (01 R2)

L1's eye — Apple Vision text recognition. Pure perception: image in →
words with boxes and confidence out. No auction knowledge, no claims,
no novelty — those live upstream. Coordinates are returned NORMALIZED
with TOP-LEFT origin (Vision speaks bottom-left; the flip happens here,
once, so nobody upstream ever meets Vision's coordinate system).
ORACLE: real banked frames read; scored against reference transcriptions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class TextRun:
    text: str
    x: float          # normalized [0,1], top-left origin
    y: float
    w: float
    h: float
    confidence: float


def recognize(image_path: str, languages: Optional[Sequence[str]] = None,
              fast: bool = False) -> List[TextRun]:
    """OCR one image file. Accurate mode by default (L1 is a deep look).
    Imports Vision lazily so non-mac test environments import this module fine."""
    import os

    import Quartz
    import Vision

    if not isinstance(image_path, (str, os.PathLike)):
        raise TypeError(f"recognize() needs a filesystem path, got "
                        f"{type(image_path).__name__} — the ObjC bridge "
                        f"segfaults on non-strings, so we refuse in Python")
    image_path = os.fspath(image_path)
    url = Quartz.CFURLCreateWithFileSystemPath(
        None, image_path, Quartz.kCFURLPOSIXPathStyle, False)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise FileNotFoundError(image_path)
    image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelFast if fast
        else Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)   # verbatim law: never "improve" text
    if languages:
        request.setRecognitionLanguages_(list(languages))

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision failed on {image_path}: {err}")

    runs: List[TextRun] = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)
        if not cand or not len(cand):
            continue
        top = cand[0]
        bb = obs.boundingBox()      # normalized, bottom-left origin
        runs.append(TextRun(
            text=str(top.string()),
            x=float(bb.origin.x),
            y=1.0 - float(bb.origin.y) - float(bb.size.height),   # the one flip
            w=float(bb.size.width),
            h=float(bb.size.height),
            confidence=float(top.confidence())))
    return runs


class VisionOCR:
    """ports.OCR adapter — kept for existing wiring; read() = recognize()."""

    def read(self, image_path: str):
        return recognize(image_path)

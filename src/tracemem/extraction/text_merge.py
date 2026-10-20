"""OWNS: cross-frame text merge — the Nutella law across time.

The same on-screen text read across many frames must become ONE claim:
the highest-confidence reading wins the text; every sighting is kept as
provenance (first/last seen, count). Mechanical by design: identity =
normalized-equal text OR (near-equal text AND overlapping place) — no
embeddings here, the semantic tier belongs to the novelty ledger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


def _iou(a: Tuple[float, float, float, float],
         b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = ((ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter)
    return inter / union if union else 0.0


@dataclass
class MergedRead:
    text: str                    # the best-confidence reading, verbatim
    bbox: Tuple[float, float, float, float]
    confidence: float
    sightings: int = 1
    first_t_ms: int = 0
    last_t_ms: int = 0
    all_readings: List[Tuple[str, float]] = field(default_factory=list)


class TextMerger:
    """Feed it reads (text, bbox-corners, confidence, t_ms) frame after
    frame; it maintains ONE MergedRead per distinct on-screen text."""

    IOU_BAR = 0.5

    def __init__(self) -> None:
        self._by_norm: Dict[str, List[MergedRead]] = {}

    def observe(self, text: str, bbox: Tuple[float, float, float, float],
                confidence: float, t_ms: int) -> MergedRead:
        key = _norm(text)
        bucket = self._by_norm.setdefault(key, [])
        for m in bucket:                       # same text; same place?
            if _iou(m.bbox, bbox) >= self.IOU_BAR:
                m.sightings += 1
                m.last_t_ms = max(m.last_t_ms, t_ms)
                m.first_t_ms = min(m.first_t_ms, t_ms)
                m.all_readings.append((text, confidence))
                if confidence > m.confidence:  # best-quality read wins, upgraded in place
                    m.text, m.confidence, m.bbox = text, confidence, bbox
                return m
        m = MergedRead(text=text, bbox=bbox, confidence=confidence,
                       first_t_ms=t_ms, last_t_ms=t_ms,
                       all_readings=[(text, confidence)])
        bucket.append(m)
        return m

    def merged(self) -> List[MergedRead]:
        return [m for bucket in self._by_norm.values() for m in bucket]


def merge_frames(frames: Sequence[Sequence]) -> List[MergedRead]:
    """Convenience: frames of (text, bbox, confidence, t_ms) tuples in."""
    tm = TextMerger()
    for frame in frames:
        for text, bbox, conf, t in frame:
            tm.observe(text, bbox, conf, t)
    return tm.merged()

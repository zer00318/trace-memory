"""OWNS: scroll detection — uniform translation of a region is SCROLLING,
not new content (the stitching pipeline's front door).

Mechanical: compare per-row signatures (grayscale row sums — the same
cheap signature the novelty ledger trusts) between two frames of a
region at candidate vertical offsets. A strong match at one nonzero
offset = scroll by that many rows. No match anywhere = real change.
Pure function; no pixels retained.
"""
from __future__ import annotations

from typing import Optional, Sequence

MATCH_BAR = 0.90        # fraction of overlapping rows that must agree
ROW_TOL = 0.02          # per-row relative tolerance (dithering, cursor blink)
MIN_OVERLAP = 8         # offsets leaving fewer overlapping rows are unreliable


def _rows_agree(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) / scale <= ROW_TOL


def detect_scroll(prev_rows: Sequence[float], curr_rows: Sequence[float],
                  max_offset: Optional[int] = None) -> Optional[int]:
    """Returns the scroll offset in rows (+down: content moved up), or
    None when the change is not a uniform translation. Offset 0 (no
    change) is reported as None too — stillness is the dwell clock's
    business, not the scroll detector's."""
    n = min(len(prev_rows), len(curr_rows))
    if n < MIN_OVERLAP:
        return None
    if max_offset is None:
        max_offset = n - MIN_OVERLAP
    best_off, best_frac = None, 0.0
    for off in range(1, max_offset + 1):
        for sign in (1, -1):
            o = off * sign
            if o > 0:      # scrolled down: prev row i+o aligns with curr row i
                pairs = zip(prev_rows[o:n], curr_rows[: n - o])
                overlap = n - o
            else:
                pairs = zip(prev_rows[: n + o], curr_rows[-o:n])
                overlap = n + o
            if overlap < MIN_OVERLAP:
                continue
            agree = sum(1 for p, c in pairs if _rows_agree(p, c))
            frac = agree / overlap
            if frac > best_frac:
                best_frac, best_off = frac, o
    return best_off if best_frac >= MATCH_BAR else None

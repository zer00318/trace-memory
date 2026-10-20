"""OWNS: Laplacian-variance readability verdict (spec 01 §3).

Below the floor the frame is unreadable and is DROPPED before any GPU is
spent — with the drop logged upstream as a gap witness, never silently.
Pure Python over gray rows: no numpy dependency at the seam; the daemon may
compute the same kernel vectorized as long as this stays the reference.
"""
from __future__ import annotations

from typing import List, Sequence

BLUR_FLOOR = 100.0  # tunable (01 R1); calibrated against gold frames


try:                                    # optional: the vectorised kernel
    import numpy as _np
except Exception:                       # noqa: BLE001
    _np = None


def _laplacian_var_np(a) -> float:
    """The same kernel over an array. Built 2026-10-09: the pure-Python loop
    ran on the capture thread for every gaze crop and was one of the two
    things holding the GIL while questions waited."""
    a = a.astype(_np.float64)
    h, w = a.shape
    if h < 3 or w < 3:
        return 0.0
    lap = (a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
           - 4.0 * a[1:-1, 1:-1])
    return float(lap.var())


def laplacian_var(gray_rows: Sequence[Sequence[float]]) -> float:
    if _np is not None and isinstance(gray_rows, _np.ndarray) \
            and gray_rows.ndim == 2:
        return _laplacian_var_np(gray_rows)
    h = len(gray_rows)
    w = len(gray_rows[0]) if h else 0
    if h < 3 or w < 3:
        return 0.0                      # too small to judge sharpness: fail closed
    vals: List[float] = []
    for y in range(1, h - 1):
        row, up, dn = gray_rows[y], gray_rows[y - 1], gray_rows[y + 1]
        for x in range(1, w - 1):
            vals.append(up[x] + dn[x] + row[x - 1] + row[x + 1] - 4.0 * row[x])
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def is_readable(gray_rows, floor: float = BLUR_FLOOR) -> bool:
    return laplacian_var(gray_rows) >= floor

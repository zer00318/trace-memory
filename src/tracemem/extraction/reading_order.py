"""OWNS: ordering text runs the way a human reads them (L1).

Column-aware: cluster runs into columns by x-overlap, read columns
left→right, within a column top→bottom, within a line left→right.
Pure function over TextRun-shaped objects (needs .x/.y/.w/.h only).
"""
from __future__ import annotations

from typing import List, Sequence

LINE_TOL = 0.6      # fraction of run height treated as same-line
COL_GAP = 0.04      # normalized x-gap that starts a new column


def order(runs: Sequence) -> List:
    if not runs:
        return []
    # columns: sweep by x, grouping runs whose x-extents chain within COL_GAP
    by_x = sorted(runs, key=lambda r: r.x)
    columns: List[List] = [[by_x[0]]]
    col_right = by_x[0].x + by_x[0].w
    for r in by_x[1:]:
        if r.x > col_right + COL_GAP:
            columns.append([r])
            col_right = r.x + r.w
        else:
            columns[-1].append(r)
            col_right = max(col_right, r.x + r.w)
    out: List = []
    for col in columns:                       # left→right columns
        col.sort(key=lambda r: r.y)           # top→bottom
        lines: List[List] = []
        for r in col:
            if lines and abs(r.y - lines[-1][0].y) <= LINE_TOL * max(r.h, lines[-1][0].h):
                lines[-1].append(r)
            else:
                lines.append([r])
        for line in lines:
            line.sort(key=lambda r: r.x)      # left→right within a line
            out.extend(line)
    return out

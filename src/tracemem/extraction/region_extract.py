"""OWNS: changed tiles → coherent regions (WHERE it changed).

Connected components (4-connectivity) over the peripheral grid's changed
tiles; each component's bounding rectangle is a Region — the thing a bid
targets and a meal crops to. Pure function, no state, no thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Set, Tuple

Tile = Tuple[int, int]          # (col, row) on the peripheral grid


@dataclass(frozen=True)
class TileRegion:
    col0: int
    row0: int
    col1: int                    # inclusive
    row1: int
    tile_count: int              # how many tiles actually changed (≠ area:
                                 # an L-shape's bbox covers tiles that didn't)


def extract(changed: Iterable[Tile]) -> List[TileRegion]:
    todo: Set[Tile] = set(changed)
    regions: List[TileRegion] = []
    while todo:
        stack = [todo.pop()]
        component = []
        while stack:
            c, r = stack.pop()
            component.append((c, r))
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in todo:
                    todo.discard(n)
                    stack.append(n)
        cols = [c for c, _ in component]
        rows = [r for _, r in component]
        regions.append(TileRegion(min(cols), min(rows), max(cols), max(rows),
                                  len(component)))
    regions.sort(key=lambda g: (g.row0, g.col0))
    return regions

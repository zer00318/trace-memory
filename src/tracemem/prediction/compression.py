"""OWNS: routine + deviation compression of the episode layer (P7 bet).

A day that the routine model predicts well collapses to a reference to the
routine; a deviating episode is kept verbatim. This is a projection, never a
deletion — raw claims and episodes are untouched; only the SUMMARY is small.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from tracemem.prediction import surprise
from tracemem.prediction.model import RoutineModel

DEVIATION_FLOOR = 0.6            # surprise above this is kept verbatim


@dataclass(frozen=True)
class Compressed:
    routine: Tuple[Tuple[int, str], ...]      # (slot, place) pairs folded into routine
    deviations: Tuple[str, ...]               # episode ids kept verbatim
    ratio: float                              # deviations / total (lower = better compression)


def compress(model: RoutineModel, episodes) -> Compressed:
    routine: List[Tuple[int, str]] = []
    deviations: List[str] = []
    eps = list(episodes)
    for ep in eps:
        s = surprise.score(ep.place_key, ep.t_range.start_ms, model)
        if s >= DEVIATION_FLOOR:
            deviations.append(ep.id)
        else:
            from tracemem.prediction.model import slot_of
            routine.append((slot_of(ep.t_range.start_ms), ep.place_key or "?"))
    total = len(eps) or 1
    return Compressed(routine=tuple(routine), deviations=tuple(deviations),
                      ratio=len(deviations) / total)

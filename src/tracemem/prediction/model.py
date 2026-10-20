"""OWNS: generative routine model of the life-stream (spec 05 P5.4).

v1 is an honest frequency model, not a neural one: it learns
P(place_key | time-slot) from episode history. That is exactly enough to
(a) score surprise as prediction error and (b) compress routine days —
the two consumers that exist. Upgrading the density model later cannot
change either consumer's contract.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Optional

MS_PER_DAY = 86_400_000
MS_PER_SLOT = 3_600_000          # 1h grain; tunable with the episode grain


def slot_of(t_ms: int) -> int:
    return (t_ms % MS_PER_DAY) // MS_PER_SLOT


def weekday_of(t_ms: int) -> int:
    """0=Monday .. 6=Sunday, UTC (same grain convention as slot_of).

    Epoch day 0 (1970-01-01) was a Thursday, so it is index 3. This is what
    lets a WEEKLY rhythm ('Mondays around 18:00') crystallize where a
    weekday-blind (slot, place) cell can only ever say 'around 18:00'.
    """
    return ((t_ms // MS_PER_DAY) + 3) % 7


class RoutineModel:
    def __init__(self) -> None:
        self._counts: Dict[int, Counter] = defaultdict(Counter)  # slot -> place counts
        self._total: Dict[int, int] = defaultdict(int)

    def fit(self, episodes) -> "RoutineModel":
        for ep in episodes:
            s = slot_of(ep.t_range.start_ms)
            self._counts[s][ep.place_key] += 1
            self._total[s] += 1
        return self

    def prob(self, place_key: Optional[str], slot: int) -> float:
        """Laplace-smoothed P(place | slot); an unseen slot is uniform-ignorant."""
        total = self._total.get(slot, 0)
        vocab = max(1, len(self._counts.get(slot, ())) + 1)
        return (self._counts[slot][place_key] + 1) / (total + vocab)

    def predict(self, context) -> Dict[Optional[str], float]:
        """context: anything with .t_ms (or an int). Returns place distribution."""
        t_ms = context if isinstance(context, int) else context.t_ms
        s = slot_of(t_ms)
        total = self._total.get(s, 0)
        if total == 0:
            return {}
        vocab = len(self._counts[s]) + 1
        return {p: (c + 1) / (total + vocab) for p, c in self._counts[s].items()}

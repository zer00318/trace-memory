"""OWNS: summon(activity) -> the ONE resident specialist (spec 01 §8, P2.3-2.5).

Pretrained-first with base fallback; hysteresis stops A->B->A thrash from
burning the session in load time: within HYSTERESIS_S of a swap the current
resident answers even for a different activity — a slightly-wrong reader now
beats the right reader after a 15s load.
"""
from __future__ import annotations

from typing import Optional

HYSTERESIS_S = 20.0


class SpecialistRegistry:
    def __init__(self, registry: dict, base_fallback, lease=None, clock=None) -> None:
        self._registry = registry            # activity -> Specialist
        self._base = base_fallback
        self._lease = lease                  # honored when loads must be turn-guarded
        self._clock = clock
        self._resident = None
        self._last_swap_ms: Optional[int] = None
        self.swap_count = 0

    def _now(self) -> int:
        return self._clock.now_ms() if self._clock else 0

    def summon(self, activity: str):
        want = self._registry.get(activity, self._base)
        if self._resident is want:
            return self._resident
        if self._resident is not None and self._clock is not None \
                and self._last_swap_ms is not None \
                and (self._now() - self._last_swap_ms) / 1000.0 < HYSTERESIS_S:
            return self._resident            # anti-thrash: hold the current reader
        if self._resident is not None:
            self._resident.evict()
        want.load()
        self._resident = want
        self._last_swap_ms = self._now()
        self.swap_count += 1
        return want

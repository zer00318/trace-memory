"""OWNS: video suppression — a playing video is ONE event, not a change-storm.

Constant-motion signature (chosen by LIVE evidence, first-beat 2026-07-19:
a playing video produced ~65 change-tiles/frame for 30s straight): a
region that changes EVERY beat for CONSTANT_BEATS is media → muted from
novelty/bidding until it holds still for STILL_BEATS (hysteresis).
Transitions are yielded exactly once each, ready to become witnesses.
Pure state machine; no pixels, no clocks of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

CONSTANT_BEATS = 10     # ~5s at 2fps: nothing but media changes that relentlessly
STILL_BEATS = 6         # ~3s of stillness ends the mute (a paused video is news again)


@dataclass(frozen=True)
class MediaEvent:
    kind: str            # "muted" | "unmuted"
    region_key: object
    t_ms: int


class MediaSuppressor:
    def __init__(self, constant_beats: int = CONSTANT_BEATS,
                 still_beats: int = STILL_BEATS) -> None:
        self._constant = constant_beats
        self._still = still_beats
        self._change_streak: Dict[object, int] = {}
        self._still_streak: Dict[object, int] = {}
        self._muted: Dict[object, bool] = {}

    def observe(self, region_key, changed: bool, t_ms: int) -> Optional[MediaEvent]:
        """One beat's verdict for one region. Returns a transition event
        (at most one) — the caller writes it as a witness, ONCE."""
        muted = self._muted.get(region_key, False)
        if changed:
            self._change_streak[region_key] = self._change_streak.get(region_key, 0) + 1
            self._still_streak[region_key] = 0
            if not muted and self._change_streak[region_key] >= self._constant:
                self._muted[region_key] = True
                return MediaEvent("muted", region_key, t_ms)
        else:
            self._change_streak[region_key] = 0
            self._still_streak[region_key] = self._still_streak.get(region_key, 0) + 1
            if muted and self._still_streak[region_key] >= self._still:
                self._muted[region_key] = False
                return MediaEvent("unmuted", region_key, t_ms)
        return None

    def is_muted(self, region_key) -> bool:
        return self._muted.get(region_key, False)

    def filter_changes(self, changes: List[Tuple[object, int]],
                       t_ms: Optional[int] = None) -> List[object]:
        """Convenience for the beat: (region_key, t_ms) changes in →
        region_keys that may still bid (unmuted) out. A tracked region
        ABSENT from this beat's changes is observed as STILL — constant
        motion means every beat, not every appearance (the first version
        muted a document touched every other beat; the test caught it)."""
        changed_keys = {key for key, _ in changes}
        t_beat = changes[0][1] if changes else int(t_ms or 0)
        for key in set(self._change_streak) | set(self._still_streak):
            if key not in changed_keys:
                self.observe(key, False, t_beat)
        out = []
        for key, t in changes:
            self.observe(key, True, t)
            if not self.is_muted(key):
                out.append(key)
        return out

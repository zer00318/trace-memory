"""OWNS: the eye stores what is NEW, and spends what it saves on detail.

FOUNDER RULING (2026-10-20): "there shouldn't be a re-read; new details must
be captured rather than the same thing." Measured 2026-10-15 on the live
store: 52% of the eye's OCR claims repeated text it had stored from the same
app within ten minutes, about half of ~120 MB/day. The napkin's extraction
box has a "Signal-Noise decipherer"; this is it, at the source, before a
claim exists. (`newness.py` was built for the semantic tier of the same
idea and never had a caller; this is the exact-text tier, which is where
the measured waste is.)

Three pieces, all pure so they test without a screen:

- SeenText: per-window memory of normalized text already captured. A run
  seen in the same window within REREAD_HORIZON_MS is a re-read and never
  becomes a claim. Every sighting refreshes it, so text that stays on
  screen is stored once, not once per frame.
- presence: with nothing new to say, the eye still owes the timeline a fact
  that the window is in front, or a 20-minute video splits at the 5-minute
  silence rule (timeline.MERGE_GAP_MS). One tiny presence claim per window
  per PRESENCE_EVERY_MS; it carries no screen text.
- detail bids: a look that found only known text bids, through the gaze
  auction's existing top-down channel, for the least recently read cell of
  the same app's screen. The saved reads go to parts not yet read.

Every constant is UNCALIBRATED: a shape chosen for how screens are used.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

REREAD_HORIZON_MS = 30 * 60 * 1000
PRESENCE_EVERY_MS = 60_000
DETAIL_BID_VALUE = 0.5            # below a live change (1.0); ages upward
MAX_PER_WINDOW = 4000
MAX_WINDOWS = 64

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)

Region = Tuple[int, int]


def normalize(text: str) -> str:
    """Case, spacing and punctuation never make text new."""
    return _NON_WORD.sub(" ", (text or "").lower()).strip()


def _window_key(claim) -> Tuple[str, Optional[str]]:
    cap = claim.coord.capture
    return (cap.app or "unknown", cap.window)


class SeenText:
    def __init__(self, horizon_ms: int = REREAD_HORIZON_MS,
                 max_per_window: int = MAX_PER_WINDOW,
                 max_windows: int = MAX_WINDOWS) -> None:
        self._horizon = horizon_ms
        self._max_per = max_per_window
        self._max_windows = max_windows
        self._windows: "OrderedDict[tuple, OrderedDict[str, int]]" = OrderedDict()

    def split(self, claims: Iterable, t_ms: int) -> Tuple[List, List]:
        """(fresh, repeated). Both kinds refresh the ledger."""
        fresh, repeated = [], []
        for claim in claims:
            key = _window_key(claim)
            seen = self._windows.get(key)
            if seen is None:
                seen = self._windows[key] = OrderedDict()
                while len(self._windows) > self._max_windows:
                    self._windows.popitem(last=False)
            self._windows.move_to_end(key)
            text = normalize(claim.text)
            last = seen.get(text)
            if not text or (last is not None and t_ms - last <= self._horizon):
                repeated.append(claim)
            else:
                fresh.append(claim)
            seen[text] = t_ms
            seen.move_to_end(text)
            while len(seen) > self._max_per:
                seen.popitem(last=False)
        return fresh, repeated


class ReadEconomy:
    """After each look: is a presence fact due, and where to look next."""

    def __init__(self, grid: Tuple[int, int],
                 presence_every_ms: int = PRESENCE_EVERY_MS) -> None:
        self._grid = grid
        self._presence_every = presence_every_ms
        self._last_said: Dict[tuple, int] = {}       # window -> last claim t
        self._read_at: Dict[Tuple[str, Region], int] = {}

    def looked(self, app: str, region: Region, t_ms: int) -> None:
        self._read_at[(app, region)] = t_ms

    def said(self, window: tuple, t_ms: int) -> None:
        self._last_said[window] = t_ms

    def presence_due(self, window: tuple, t_ms: int) -> bool:
        last = self._last_said.get(window)
        return last is None or t_ms - last >= self._presence_every

    def stale_cell(self, app: str, just_read: Region, t_ms: int,
                   horizon_ms: int = REREAD_HORIZON_MS) -> Optional[Region]:
        """The least recently read cell, only if it is due a read at all."""
        cell = self.next_detail(app, just_read)
        seen = self._read_at.get((app, cell), -1)
        return cell if seen < 0 or t_ms - seen >= horizon_ms else None

    def next_detail(self, app: str, just_read: Region) -> Region:
        """The cell of this app's screen read longest ago (never = oldest)."""
        gx, gy = self._grid
        best, best_t = None, None
        for y in range(gy):
            for x in range(gx):
                cell = (x, y)
                if cell == just_read:
                    continue
                seen = self._read_at.get((app, cell), -1)
                if best_t is None or seen < best_t:
                    best, best_t = cell, seen
        return best


# The window-title sense (capture/ax_claims) read every visible window every
# few seconds and stored each one's url, title and up to MAX_OBJECT_CLAIMS UI
# labels EVERY time: measured 2026-10-20 the hour its Accessibility grant came
# back, 28,226 claims in 30 minutes, 99.1% repeats (~88 per read). Titles and
# urls are CONTEXT the timeline carries forward, so they are stored when they
# change and re-asserted once per CONTEXT_REASSERT_MS, which keeps every
# question's range holding the context its moments need. UNCALIBRATED.
CONTEXT_REASSERT_MS = 10 * 60 * 1000
_CONTEXT_KINDS = ("url", "window_title")


class AxChanges:
    """(fresh, repeated) for accessibility readings: changes, not re-reads."""

    def __init__(self, horizon_ms: int = REREAD_HORIZON_MS,
                 reassert_ms: int = CONTEXT_REASSERT_MS,
                 max_contexts: int = 2000) -> None:
        self._objects = SeenText(horizon_ms)
        self._reassert = reassert_ms
        self._max = max_contexts
        self._context: "OrderedDict[tuple, Tuple[str, int]]" = OrderedDict()

    def split(self, claims: Iterable, t_ms: int) -> Tuple[List, List]:
        fresh, repeated, objects = [], [], []
        for claim in claims:
            kind = (claim.structured or {}).get("ax")
            if kind not in _CONTEXT_KINDS:
                objects.append(claim)
                continue
            key = _window_key(claim) + (kind,)
            text = normalize(claim.text)
            prev = self._context.get(key)
            if prev is None or prev[0] != text or t_ms - prev[1] >= self._reassert:
                fresh.append(claim)
                self._context[key] = (text, t_ms)
                self._context.move_to_end(key)
                while len(self._context) > self._max:
                    self._context.popitem(last=False)
            else:
                repeated.append(claim)
        more, again = self._objects.split(objects, t_ms)
        return fresh + more, repeated + again

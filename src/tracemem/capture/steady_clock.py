"""OWNS: a stamp that never goes backwards, and says when the wall clock did.

`Stamping / Monotonic clock law` and `Time itself / Clock jumps`. The daemon
stamped every claim with `int(time.time() * 1000)` — the wall clock, raw.

WHAT THAT COSTS. An NTP correction, a manual time change, a laptop waking in
a new timezone with a bad RTC: the wall clock steps, sometimes backwards by
seconds. Every claim written after a backward step carries a timestamp
EARLIER than claims already stored. Nothing in the system notices, and three
things quietly break at once:

  · the timeline reorders — a person's afternoon appears before their
    morning, and no query can tell that from what really happened;
  · episode boundaries are cut at the wrong instants, since the binder
    segments on time;
  · a range query silently omits claims that fall outside a range they
    should have been inside.

None of that raises. It looks like a memory that is simply wrong about a day.

THE RULE: TIME MOVES FORWARD OR IT DOES NOT MOVE. Elapsed time comes from
`time.monotonic`, which no clock correction can touch, and the wall clock is
consulted only to ANCHOR that to a real date. A wall step is detected, the
anchor is re-set, and — this is the part that matters — the step is
REPORTED, so the caller can write a witness rather than letting a jump pass
as ordinary seconds.

WHY NOT JUST USE MONOTONIC. It has no relation to any date. A memory whose
claims are stamped in seconds-since-boot cannot answer "what did I do on
Tuesday", which is most of what it is for.

A FORWARD STEP IS ALSO A JUMP. Waking from sleep looks like one and is
harmless; a clock corrected forwards by an hour is not. Both are reported,
and the caller decides — this module refuses to guess which happened, because
guessing wrong in the harmless direction is how the harmful one gets missed.
"""
from __future__ import annotations

import time as _time
from typing import Callable, List, Optional, Tuple

# How far the wall clock may drift from the anchored estimate before it is
# called a jump rather than ordinary imprecision. Generous: scheduler delays,
# a busy machine and coarse timers all move these apart by tens of
# milliseconds, and crying jump at that would make the witness worthless.
JUMP_MS = 2_000
MODEL_ID = "steady-wall-v1"


class SteadyClock:
    """`now_ms()` that never returns a smaller number than it just did."""

    model_id = MODEL_ID

    def __init__(self, wall: Optional[Callable[[], float]] = None,
                 mono: Optional[Callable[[], float]] = None,
                 jump_ms: int = JUMP_MS) -> None:
        self._wall = wall or _time.time
        self._mono = mono or _time.monotonic
        self._jump_ms = jump_ms
        self._wall_anchor = int(self._wall() * 1000)
        self._mono_anchor = self._mono()
        self._last = self._wall_anchor
        # (t_ms_when_noticed, delta_ms). Drained by the caller so the jump
        # can be witnessed. Bounded: a machine with a broken RTC could step
        # every second for a week, and a list nobody drains is a leak.
        self._jumps: List[Tuple[int, int]] = []
        self.jumps_seen = 0

    def _estimate(self) -> int:
        return self._wall_anchor + int(
            (self._mono() - self._mono_anchor) * 1000)

    def now_ms(self) -> int:
        est = self._estimate()
        wall = int(self._wall() * 1000)
        delta = wall - est
        if abs(delta) >= self._jump_ms:
            # RE-ANCHOR TO THE NEW TRUTH. The wall clock is now the better
            # estimate of the actual date — it is what every other program on
            # this machine believes, and a memory that insisted on its own
            # timeline would disagree with every file mtime and calendar
            # entry it ever reads.
            self._wall_anchor = wall
            self._mono_anchor = self._mono()
            self.jumps_seen += 1
            self._jumps.append((max(self._last, wall), delta))
            if len(self._jumps) > 256:
                del self._jumps[:-256]
            est = wall
        # NEVER BACKWARDS. Re-anchoring fixes the DATE; this fixes the
        # ORDER. A claim written after another must never carry an earlier
        # stamp, because everything downstream — episodes, ranges, the
        # timeline a person reads — assumes that and cannot check it.
        if est < self._last:
            return self._last
        self._last = est
        return est

    def __call__(self) -> int:
        return self.now_ms()

    def drain_jumps(self) -> List[Tuple[int, int]]:
        """Jumps noticed since the last call. Emptied, so a caller that
        witnesses them cannot witness the same one twice."""
        out, self._jumps = self._jumps, []
        return out

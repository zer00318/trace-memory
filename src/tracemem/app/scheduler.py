"""OWNS: the two-level GPU arbiter (spec 08 §1-3, O1-O3/O5).

The design finding: residency (which weights are loaded — changes in seconds,
wants hysteresis) and turns (who computes next — changes in milliseconds,
wants queue-jump) are DIFFERENT scarce things; one lock either thrashes
weights or starves queries. Cooperative single-process v1: contention raises
or flags, it never blocks — the caller (night loop) polls preempted() and
yields, which is exactly the contract run_night was written against.
Every transition is logged with its cause.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

HARD_EVICT_S = 60.0
HYSTERESIS_MS = 30_000          # a fresh swap is protected from background flip-flop
QUERY_PRIORITY = 10             # user turns queue-jump; background is 0

# ROLES THAT MUST NEVER HOLD THE GPU WHILE THE EYE IS OPEN.
#
# Heavy background GPU work beside live capture makes capture GO DARK with no
# error — the most expensive failure this product has had. O3 encoded that as
# a refusal, but as a literal `role == "DREAM"` comparison, which protects
# exactly one caller by name. The second background consumer of the GPU (the
# macro VLM eye) would have sailed straight past it and been refused by
# nothing, because it is spelled differently.
#
# A set is not much better in principle — a third caller can still forget to
# join it — but it turns "did you spell it DREAM?" into a named list that
# shows up in a grep for the constant, and the tests below pin the membership
# rather than the string.
GPU_HEAVY_ROLES = frozenset({"DREAM", "MACRO"})

# How many arbiter events are kept. Enough to reconstruct what happened around
# an incident; bounded so a daemon that runs for a year does not accumulate
# one entry per turn forever. See _log().
LOG_TAIL = 2_000


class SpotlightBusy(Exception):
    pass


class Spotlight:
    def __init__(self, swap_table: Dict[str, float], clock) -> None:
        self._swap_table = swap_table          # model -> load seconds (reported)
        self._clock = clock
        self.resident: Optional[str] = None
        self._holder: Optional[Tuple[str, int]] = None      # (role, priority)
        self._holder_since_ms = 0
        self._last_swap_ms = -10 ** 12
        self._preempt = False
        self.capture_active = False
        self.log: List[Tuple[int, str, str]] = []           # (t_ms, event, cause)

    def _log(self, event: str, cause: str) -> None:
        self.log.append((self._clock.now_ms(), event, cause))
        # BOUNDED (2026-07-29, P2). This list is the arbiter's whole audit
        # trail and it had no ceiling. That was harmless while nothing ran on
        # a schedule; with a continuous night taking a turn every 30s it is a
        # slow leak in a process designed to run for years — and the leak
        # would appear as unexplained RSS growth long after the cause, which
        # is the invisible-failure shape this codebase spends the most effort
        # avoiding. The tail is what anyone debugging actually reads.
        if len(self.log) > LOG_TAIL:
            del self.log[:-LOG_TAIL]

    # -- external signals --

    def set_capture(self, active: bool) -> None:
        """Signal whether the eye is open. IDEMPOTENT.

        The daemon calls this every beat so the guard reflects reality rather
        than whatever was true at boot (before 2026-07-29 nothing outside
        tests called it at all, so `capture_active` was permanently False and
        the DREAM refusal below could never fire). A per-beat caller must not
        write a log line per beat, so an unchanged state is a no-op — the log
        records TRANSITIONS, which is the only thing anyone reads it for.
        """
        if active == self.capture_active:
            return
        self.capture_active = active
        self._log("capture", "on" if active else "off")

    def signal_query(self) -> None:
        """A user turn is waiting: cooperative preempt flag (O2)."""
        if self._holder and self._holder[1] < QUERY_PRIORITY:
            self._preempt = True
            self._log("preempt-signal", "query waiting, holder=%s" % self._holder[0])

    # -- the night loop's polling contract --

    def preempted(self) -> bool:
        if self._preempt:
            return True
        if self._holder and self._holder[1] < QUERY_PRIORITY:
            held_s = (self._clock.now_ms() - self._holder_since_ms) / 1000.0
            if held_s > HARD_EVICT_S:
                self._log("hard-evict-due", "held %.0fs > %.0fs" % (held_s, HARD_EVICT_S))
                return True                     # escape hatch: a stuck dream must yield
        return False

    def is_uncontended(self) -> bool:
        return self._holder is None and not self.capture_active

    # -- turns --

    @contextmanager
    def acquire(self, model: str, role: str = "?", priority: int = 0):
        t = self._clock.now_ms()
        if self._holder is not None:
            raise SpotlightBusy("turn held by %s" % self._holder[0])
        if role in GPU_HEAVY_ROLES and self.capture_active:
            raise SpotlightBusy("%s refuses the GPU during live capture — "
                                "capture goes dark silently otherwise (O3)"
                                % role)
        if self.resident is not None and self.resident != model:
            fresh = t - self._last_swap_ms < HYSTERESIS_MS
            if fresh and priority < QUERY_PRIORITY:
                raise SpotlightBusy("residency hysteresis: %s loaded %.0fs ago"
                                    % (self.resident, (t - self._last_swap_ms) / 1000.0))
            self._log("evict", "%s -> %s for %s" % (self.resident, model, role))
            self.resident = None
        if self.resident is None:
            self.resident = model               # ONE model resident, ever (O1)
            self._last_swap_ms = t
            self._log("load", "%s (%.1fs) for %s"
                      % (model, self._swap_table.get(model, 0.0), role))
        self._holder = (role, priority)
        self._holder_since_ms = t
        self._log("turn-start", role)
        try:
            yield
        finally:
            self._holder = None
            self._preempt = False
            self._log("turn-end", role)

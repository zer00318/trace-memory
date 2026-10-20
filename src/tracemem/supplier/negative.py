"""OWNS: the honest "no" (map: Supplier / Negative; and the Calibrated
I-don't-know it feeds).

THE ATOM'S POINT, and it is the sharpest edge in the whole product: zero
hits is NOT an answer. "Did I ever talk to Sam about the lease?" answered
with a flat "no" is a claim about the WORLD, made from evidence that only
covers what the system happened to be watching. If the eyes were shut for
40% of the asked period, the honest answer is "not while I was watching,
and I was watching 60% of that time" — which is a different sentence, and
the only one the system has standing to say.

A flat "no" here is the most dangerous output in the system, because it is
the one a person will act on hardest ("good, I never sent it") and the one
that looks least like a guess. It has no hedge in it, no citation to check,
and no surface that would ever reveal it was wrong. So the code path that
could produce a bare "no" does not exist: absence answers are constructed
here, always carrying their coverage, and there is nothing to forget to add.

THE GAP MATH IS THE DELIVERABLE (the brief's own words). It comes from
app/coverage.py, which derives watched time from WITNESSES alone — never
from claim density, which would call a quiet afternoon a blind spot.

SPEC: 05 (supplier), 08 (honest holes)
ORACLE: tests/unit/test_negative.py — plant nothing, ask "did I ever" → the
        answer carries a coverage %, and a bare "no" is unreachable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from tracemem.app.coverage import Coverage, compute

# Below this coverage, an absence answer is worth so little that saying
# "never" at all misleads — the honest response leads with the blindness.
# UNCALIBRATED: 0.5 is the point where the system saw less than half of what
# it is being asked about. No probe has run; the founder-grading loop owns
# moving it.
WEAK_COVERAGE = 0.5


@dataclass(frozen=True)
class AbsenceAnswer:
    found: int                      # how many hits (0 = a real absence)
    coverage: Optional[float]       # fraction watched, None = nothing to watch
    gaps: Tuple                     # the itemised blind intervals
    t0_ms: int
    t1_ms: int

    @property
    def coverage_pct(self) -> Optional[float]:
        return None if self.coverage is None else round(100.0 * self.coverage, 1)

    @property
    def weak(self) -> bool:
        return self.coverage is not None and self.coverage < WEAK_COVERAGE

    def render(self) -> str:
        """The sentence. There is no branch here that returns a bare 'no'.

        Three shapes, by what the system actually knows:
          - nothing to watch (the whole range was declared off)
          - weak coverage: lead with the blindness, not the absence
          - normal: "not while I was watching (X%)", gaps itemised
        """
        if self.found:
            return f"found {self.found}"

        if self.coverage is None:
            return ("I have nothing to say about that period — capture was "
                    "off for all of it, at your request.")

        pct = self.coverage_pct
        gap_note = ""
        if self.gaps:
            shown = "; ".join(str(g) for g in self.gaps[:3])
            more = f" (+{len(self.gaps) - 3} more)" if len(self.gaps) > 3 else ""
            gap_note = f" Blind spots: {shown}{more}."

        if self.weak:
            return (f"I only watched {pct}% of that period, so I cannot tell "
                    f"you whether it happened — I saw nothing, but I was "
                    f"looking away for most of it.{gap_note}")

        return (f"Not while I was watching — and I was watching {pct}% of "
                f"that period.{gap_note}")


def answer_absence(claim_store, t0_ms: int, t1_ms: int,
                   hits: Sequence = (), witnesses: Optional[Sequence] = None
                   ) -> AbsenceAnswer:
    """Build the absence answer for a range. `hits` is whatever retrieval
    found; a non-empty hits list is not an absence at all, and is passed
    through so callers have ONE path rather than an if that could forget."""
    if witnesses is None:
        # Witness claims may START before the window (an overnight gap is
        # stamped the previous evening) — same lookback reasoning as the
        # binder's. Without it the morning's blindness is invisible.
        witnesses = claim_store.read_range(t0_ms - _LOOKBACK_MS, t1_ms)
    cov: Coverage = compute(witnesses, t0_ms, t1_ms)
    return AbsenceAnswer(found=len(hits), coverage=cov.fraction,
                         gaps=cov.gaps, t0_ms=t0_ms, t1_ms=t1_ms)


# A blind interval reaching into the window can only be seen if we look back
# far enough to find the claim that opened it. UNCALIBRATED; matches the
# binder's GAP_LOOKBACK_MS reasoning (longest plausible single absence).
_LOOKBACK_MS = 36 * 60 * 60 * 1000

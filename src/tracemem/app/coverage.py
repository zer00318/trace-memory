"""OWNS: the coverage audit — what fraction of a day was actually watched
(map: Proof / Coverage audit).

DERIVED FROM WITNESSES ALONE. Nothing here is estimated, sampled, or
inferred from how many claims happened to land. That constraint is the
whole atom: a coverage number computed from claim density would say a quiet
afternoon of reading was a blind spot, and would say a noisy broken loop was
excellent coverage. Only the witness stream knows the difference between
"nothing happened" and "I could not see", and those are opposite answers to
every absence question.

    coverage = watched_ms / (day_span_ms - declared_off_ms)

DECLARED-OFF TIME IS REMOVED FROM THE DENOMINATOR, not counted as a miss.
The sacred OFF is the founder's own choice; charging the system for the
hours it was told not to look would make the honest act of pausing look
like a failure, which is precisely the incentive that gets a privacy
feature quietly abandoned. A founder pause is not a gap in the product's
duty — it is the product obeying.

Every gap is itemised, not just totalled. "You were 88% covered" is not
useful to a person asking what happened at 2pm; "not watching 13:58–14:31,
because the lid was shut" is. The list is what the Negative question class
cites, so it carries each gap's CAUSE from the witness that recorded it.

SPEC: 08 (honest holes), 12 (proof)
ORACLE: tests/unit/test_coverage.py — a hand-computed fixture day, checked
        by hand in the test's own comments, plus the invariants (coverage
        never exceeds 1.0; an all-gap day is 0.0; a fully-off day is not a
        division by zero).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from tracemem.app.witness import WitnessKind

# Witness kinds that mean "the eyes were shut", each with whether the
# founder ASKED for it (declared-off) or it merely happened to us (a gap).
BLIND_KINDS = {
    WitnessKind.GAP.value: False,
    WitnessKind.KILL_DETECTED.value: False,
    WitnessKind.CLOSED_EYES.value: False,
    WitnessKind.AWAY.value: False,
    WitnessKind.CRASH.value: False,
    WitnessKind.PERMISSION_LOSS.value: False,
    WitnessKind.EXCLUDED.value: True,        # blocklist / secure input: asked for
    WitnessKind.FOUNDER_PAUSE.value: True,   # the sacred OFF
}


@dataclass(frozen=True)
class Gap:
    t1_ms: int
    t2_ms: int
    kind: str
    cause: str
    declared_off: bool

    @property
    def duration_ms(self) -> int:
        return max(0, self.t2_ms - self.t1_ms)

    def __str__(self) -> str:
        return (f"not watching {self.t1_ms}–{self.t2_ms} "
                f"({self.kind}{': ' + self.cause if self.cause else ''})")


@dataclass(frozen=True)
class Coverage:
    day_start_ms: int
    day_end_ms: int
    watched_ms: int
    declared_off_ms: int
    gaps: Tuple[Gap, ...]

    @property
    def span_ms(self) -> int:
        return max(0, self.day_end_ms - self.day_start_ms)

    @property
    def accountable_ms(self) -> int:
        """The time the system was actually responsible for watching."""
        return max(0, self.span_ms - self.declared_off_ms)

    @property
    def fraction(self) -> Optional[float]:
        """None when there is nothing to be accountable for — a day spent
        entirely paused has no coverage percentage, and inventing 0% or
        100% would both be lies about it."""
        if self.accountable_ms <= 0:
            return None
        return self.watched_ms / self.accountable_ms

    @property
    def percent(self) -> Optional[float]:
        """Never rounds UP to 100 while a hole exists (2026-07-29).

        Found by reading a real digest, which printed:

            I was watching 100% of this period. Blind for 4 interval(s).

        Both halves came from this object, and together they are a
        contradiction a person would rightly stop trusting. `round(99.96, 1)`
        is 100.0, and over a long window a few real blind minutes round away
        entirely — so the number said "I saw everything" about a period the
        very next clause admitted it had not seen.

        This is the smallest possible version of the failure the whole product
        exists to refuse, and it is the most dangerous size: too small to
        notice, in the one number a person uses to decide how much to trust
        an absence answer. 99.9 is the honest ceiling for an imperfect day.
        """
        f = self.fraction
        if f is None:
            return None
        pct = round(100.0 * f, 1)
        if pct >= 100.0 and self.watched_ms < self.accountable_ms:
            return 99.9
        return pct

    @property
    def percent_text(self) -> Optional[str]:
        """The number AS A READER SEES IT — and the only sanctioned way to
        render it.

        THE GUARD ABOVE WAS UNDONE ONE LAYER UP (found 2026-07-29 by reading
        the founder's live digest, the second time this same sentence has
        been caught lying). `percent` correctly refused to say 100 and
        returned 99.9 — and then `digest.py` printed it with `%.0f`, which
        rounds 99.9 straight back to "100". The report read, again:

            I was watching 100% of this period. Blind for 3 interval(s).

        A guard at the source cannot survive a formatter downstream that is
        free to round. So the formatting moves in HERE, beside the rule it
        has to honour, and the caller is given a string rather than a number
        it can re-round. Fix the class, not the sentence: any future surface
        — a web face, a menubar, an export — gets the honest text for free.
        """
        pct = self.percent
        if pct is None:
            return None
        # One decimal only when it is load-bearing, i.e. when rounding to a
        # whole number would claim a completeness the day did not have.
        if pct >= 99.9 and self.watched_ms < self.accountable_ms:
            return "99.9%"
        return "%.0f%%" % pct

    def missed_ms(self) -> int:
        return self.accountable_ms - self.watched_ms

    def as_cockpit_row(self) -> dict:
        return {"coverage_pct": self.percent,
                "watched_ms": self.watched_ms,
                "declared_off_ms": self.declared_off_ms,
                "gap_count": len(self.gaps),
                "gaps": [str(g) for g in self.gaps]}


def _clip(t1: int, t2: int, lo: int, hi: int) -> Tuple[int, int]:
    return (max(t1, lo), min(t2, hi))


def _merge(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Overlapping blind intervals are ONE blind interval. Without this a
    lid closed while already away subtracts the same hour twice and
    coverage goes negative — silently, since it is only ever divided."""
    out: List[Tuple[int, int]] = []
    for t1, t2 in sorted(spans):
        if out and t1 <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], t2))
        else:
            out.append((t1, t2))
    return out


def gaps_from_witnesses(claims: Sequence, day_start_ms: int,
                        day_end_ms: int) -> List[Gap]:
    """Every blind interval overlapping the day, clipped to it."""
    found: List[Gap] = []
    for c in claims:
        s = getattr(c, "structured", None) or {}
        kind = s.get("witness")
        if kind not in BLIND_KINDS or "t2_ms" not in s:
            continue
        t1, t2 = _clip(c.coord.t_ms, int(s["t2_ms"]), day_start_ms, day_end_ms)
        if t2 <= t1:
            continue                       # entirely outside this day
        found.append(Gap(t1_ms=t1, t2_ms=t2, kind=kind,
                         cause=str(s.get("cause", "")),
                         declared_off=BLIND_KINDS[kind]))
    return sorted(found, key=lambda g: g.t1_ms)


def compute(claims: Sequence, day_start_ms: int, day_end_ms: int) -> Coverage:
    """The audit. Witnesses in, coverage out — nothing hand-estimated."""
    gaps = gaps_from_witnesses(claims, day_start_ms, day_end_ms)

    off = _merge([(g.t1_ms, g.t2_ms) for g in gaps if g.declared_off])
    off_ms = sum(t2 - t1 for t1, t2 in off)

    # Blind time that counts against us = every blind interval MINUS the
    # declared-off ones. Merged first, so overlaps never subtract twice.
    blind = _merge([(g.t1_ms, g.t2_ms) for g in gaps])
    blind_ms = sum(t2 - t1 for t1, t2 in blind)

    span = max(0, day_end_ms - day_start_ms)
    watched = max(0, span - blind_ms)
    return Coverage(day_start_ms=day_start_ms, day_end_ms=day_end_ms,
                    watched_ms=watched, declared_off_ms=off_ms,
                    gaps=tuple(gaps))

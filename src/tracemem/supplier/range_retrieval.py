"""OWNS: range in → claims out, with the blindness attached (map: Supplier /
Range retrieval, Aggregate).

Wires the time-phrase parser to the store's time index. The whole value is
in what travels alongside the ids: the coverage of the asked range and the
gaps inside it, so that every downstream sentence can tell these three
apart —

    "nothing happened"          a claim about the world
    "I found nothing"           a claim about the search
    "I wasn't watching"         a claim about the system

They are different answers, and only the last two are ever ours to make.
The class this module refuses to allow is the first one: a range fully
inside a capture gap returns NOT_WATCHING, never EMPTY, because zero hits
from a period the eyes were shut is not evidence of anything.

WHY THE VERDICT IS AN ENUM AND NOT A COMMENT: the caller that formats the
answer must branch on this, and a caller reading `len(ids) == 0` has already
lost the distinction. Making it a field means the honest phrasing is the
path of least resistance rather than a discipline someone has to remember.

AGGREGATE COUNTS DEDUPE BY MOMENT, not by claim. "How many times did I look
at the lease?" answered by counting claims would report one long reading
session as forty times — a number that is technically derived from evidence
and completely false as an answer. Moments are the unit a person means.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Sequence, Tuple

from tracemem.app.coverage import Coverage, compute
from tracemem.supplier.time_phrases import Range


class Verdict(Enum):
    FOUND = "found"
    EMPTY = "empty"                  # watched, and genuinely nothing there
    NOT_WATCHING = "not-watching"    # the range is (almost) all gap
    PARTIAL = "partial"              # found some, but blind for much of it


# Above this share of the asked range being unwatched, a zero result stops
# being "nothing happened" and becomes "I wasn't there". UNCALIBRATED
# (2026-07-22): declared, not probed. The founder-grading loop owns moving
# it — the same status as supplier/negative.WEAK_COVERAGE, and deliberately
# the same number so the two cannot disagree about the same judgement.
MOSTLY_BLIND = 0.5


@dataclass(frozen=True)
class RangeResult:
    range: Range
    claim_ids: Tuple[str, ...]
    coverage: Coverage
    verdict: Verdict

    @property
    def gaps(self):
        return self.coverage.gaps

    @property
    def coverage_percent(self) -> Optional[float]:
        return self.coverage.percent

    def caveat(self) -> str:
        """The sentence a formatter should attach. Never empty for anything
        except a fully-watched range — silence would read as certainty."""
        pct = self.coverage_percent
        if self.verdict is Verdict.NOT_WATCHING:
            return ("I wasn't watching for most of %s (saw %s%% of it), so I "
                    "can't tell you what happened then"
                    % (self.range.phrase,
                       "an unknown share of" if pct is None else pct))
        if pct is None:
            return ("that whole period was declared off — nothing was "
                    "watched, by your own instruction")
        if pct >= 99.5:
            return ""
        return "I was watching %s%% of %s" % (pct, self.range.phrase)


def retrieve(claim_store, rng: Range, witnesses: Sequence,
             mostly_blind: float = MOSTLY_BLIND) -> RangeResult:
    """Claims in the range, plus what the range's coverage actually was.

    `claim_store` needs only `ids_in_range`; the ids path deliberately does
    not decrypt (map: Supplier / Time index), because counting and coverage
    math never need the text.
    """
    ids = tuple(claim_store.ids_in_range(rng.t0_ms, rng.t1_ms))
    coverage = compute(list(witnesses), rng.t0_ms, rng.t1_ms)

    fraction = coverage.fraction
    blind = fraction is not None and fraction < mostly_blind

    if blind and not ids:
        verdict = Verdict.NOT_WATCHING
    elif not ids:
        verdict = Verdict.EMPTY
    elif blind:
        verdict = Verdict.PARTIAL
    else:
        verdict = Verdict.FOUND

    return RangeResult(range=rng, claim_ids=ids, coverage=coverage,
                       verdict=verdict)


@dataclass(frozen=True)
class Count:
    n: int
    range: Range
    coverage: Coverage
    caveat: str

    def render(self) -> str:
        """A count is NEVER rendered bare (the brief's own words)."""
        head = "%d time%s that I saw" % (self.n, "" if self.n == 1 else "s")
        return head if not self.caveat else "%s — %s" % (head, self.caveat)


def count_moments(result: RangeResult,
                  moment_of: Callable[[str], Optional[str]]) -> Count:
    """Count DISTINCT MOMENTS among the hits, with the coverage caveat.

    `moment_of` maps a claim id to its moment id; claims with no moment yet
    count as their own moment rather than being dropped, because dropping
    them would undercount by exactly the claims the binder has not reached.
    """
    seen = []
    for claim_id in result.claim_ids:
        key = moment_of(claim_id) or ("claim:" + claim_id)
        if key not in seen:
            seen.append(key)

    caveat = result.caveat()
    if result.verdict is Verdict.NOT_WATCHING:
        caveat = result.caveat()
    return Count(n=len(seen), range=result.range,
                 coverage=result.coverage, caveat=caveat)

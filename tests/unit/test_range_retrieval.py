"""ORACLE for Range retrieval + Aggregate (06_supplier).

PROOFS:
  Range retrieval "Test: range query returns claims + the gap list; a range
                   fully inside a gap returns 'not watching', not 'nothing
                   happened'."
  Aggregate       "Fixture week: known plant count returned with the honest
                   caveat attached." — and "Never a bare number."

The three sentences this module exists to keep apart:

    "nothing happened"   a claim about the world   — never ours to make
    "I found nothing"    a claim about the search
    "I wasn't watching"  a claim about the system

The tests below are mostly about the boundary between the last two, because
that is where a zero silently turns into a falsehood.
"""
import pytest

from tracemem.app.witness import WitnessKind, witness
from tracemem.supplier.range_retrieval import (
    MOSTLY_BLIND, Count, RangeResult, Verdict, count_moments, retrieve,
)
from tracemem.supplier.time_phrases import Range
from tracemem.text.claim_log import ClaimLog
from tests.helpers import make_claim

HOUR = 3_600_000
T0 = 1_000_000_000
DAY = Range(t0_ms=T0, t1_ms=T0 + 10 * HOUR, phrase="yesterday",
            from_lived_rhythm=True)


def store(tmp_path, n=0, start=T0 + HOUR, step=HOUR):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    for i in range(n):
        log.append(make_claim(text="thing %d" % i, t_ms=start + i * step,
                              frame="f%d" % i))
    return log


def gap(t1, t2, kind=WitnessKind.KILL_DETECTED, cause="no-clean-stop"):
    return witness(kind, t_ms=t1, span_t2_ms=t2, detail="gap",
                   structured_extra={"cause": cause})


def off(t1, t2):
    """A founder-declared pause: blind, but not our fault."""
    return witness(WitnessKind.FOUNDER_PAUSE, t_ms=t1, span_t2_ms=t2,
                   detail="paused")


# -- THE ORACLE ------------------------------------------------------------
def test_a_range_returns_its_claims_and_its_gap_list(tmp_path):
    log = store(tmp_path, n=4)
    result = retrieve(log, DAY, [gap(T0 + 6 * HOUR, T0 + 7 * HOUR)])

    assert len(result.claim_ids) == 4
    assert len(result.gaps) == 1
    assert result.verdict is Verdict.FOUND
    assert result.coverage_percent == 90.0


def test_a_range_fully_inside_a_gap_says_not_watching(tmp_path):
    """THE oracle. Zero hits from a period the eyes were shut is not
    evidence of anything, and must never render as 'nothing happened'."""
    log = store(tmp_path, n=0)
    result = retrieve(log, DAY, [gap(T0, T0 + 10 * HOUR)])

    assert result.verdict is Verdict.NOT_WATCHING
    assert result.claim_ids == ()
    assert "wasn't watching" in result.caveat()


def test_zero_hits_while_fully_watching_is_empty_not_not_watching(tmp_path):
    """The other side of the same line: if the eyes were open the whole
    time, 'I found nothing' is an honest thing to say."""
    log = store(tmp_path, n=0)
    result = retrieve(log, DAY, [])
    assert result.verdict is Verdict.EMPTY
    assert result.coverage_percent == 100.0


def test_finding_something_while_mostly_blind_is_partial(tmp_path):
    """Hits do not cancel blindness. 'I saw it twice' from a range we
    watched a third of is still a number that needs its caveat."""
    log = store(tmp_path, n=2)
    result = retrieve(log, DAY, [gap(T0 + 3 * HOUR, T0 + 10 * HOUR)])
    assert result.verdict is Verdict.PARTIAL
    assert result.claim_ids != ()
    assert result.caveat()


def test_the_blindness_threshold_is_honoured_at_the_boundary(tmp_path):
    """A documented threshold that is not enforced is decoration."""
    log = store(tmp_path, n=0)
    span = 10 * HOUR

    just_watched = retrieve(
        log, DAY, [gap(T0, T0 + int(span * (1 - MOSTLY_BLIND)) - 60_000)])
    assert just_watched.verdict is Verdict.EMPTY

    just_blind = retrieve(
        log, DAY, [gap(T0, T0 + int(span * (1 - MOSTLY_BLIND)) + 60_000)])
    assert just_blind.verdict is Verdict.NOT_WATCHING


def test_a_declared_pause_is_not_counted_against_coverage(tmp_path):
    """Declared-off time is not a failure to watch — it is the user's own
    instruction being obeyed, and blaming ourselves for it would understate
    coverage forever."""
    log = store(tmp_path, n=1)
    result = retrieve(log, DAY, [off(T0, T0 + 5 * HOUR)])
    assert result.coverage.declared_off_ms == 5 * HOUR
    assert result.verdict is not Verdict.NOT_WATCHING


def test_a_wholly_paused_range_has_no_percentage_and_says_so(tmp_path):
    """Coverage is None when there was nothing to be accountable for.
    Rendering 0% or 100% would both be lies about that period."""
    log = store(tmp_path, n=0)
    result = retrieve(log, DAY, [off(T0, T0 + 10 * HOUR)])
    assert result.coverage_percent is None
    assert "declared off" in result.caveat()


def test_a_fully_watched_range_gets_no_caveat(tmp_path):
    """Silence has to mean something. If a caveat appeared even at 100%,
    readers would learn to ignore caveats."""
    log = store(tmp_path, n=2)
    assert retrieve(log, DAY, []).caveat() == ""


def test_claims_outside_the_range_are_not_returned(tmp_path):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    log.append(make_claim(text="before", t_ms=T0 - HOUR, frame="a"))
    log.append(make_claim(text="inside", t_ms=T0 + HOUR, frame="b"))
    log.append(make_claim(text="after", t_ms=T0 + 99 * HOUR, frame="c"))
    assert len(retrieve(log, DAY, []).claim_ids) == 1


# -- Aggregate -------------------------------------------------------------
def test_a_count_dedupes_by_moment_not_by_claim(tmp_path):
    """One long reading session is ONE time, not forty. Counting claims
    would give a number technically derived from evidence and completely
    false as an answer."""
    log = store(tmp_path, n=6, step=60_000)
    result = retrieve(log, DAY, [])
    ids = list(result.claim_ids)
    moments = {cid: ("m-1" if i < 4 else "m-2") for i, cid in enumerate(ids)}

    count = count_moments(result, moments.get)
    assert count.n == 2


def test_claims_without_a_moment_still_count(tmp_path):
    """Dropping them would undercount by exactly the claims the binder has
    not reached yet — a number that silently improves as a background job
    catches up."""
    log = store(tmp_path, n=3, step=60_000)
    result = retrieve(log, DAY, [])
    count = count_moments(result, lambda _cid: None)
    assert count.n == 3


def test_a_count_is_never_rendered_bare(tmp_path):
    """The brief's own words. A bare '7' invites a confidence the evidence
    does not support."""
    log = store(tmp_path, n=3, step=60_000)
    result = retrieve(log, DAY, [gap(T0 + 5 * HOUR, T0 + 9 * HOUR)])
    rendered = count_moments(result, lambda _c: None).render()

    assert "that I saw" in rendered
    assert "watching" in rendered
    assert rendered != "3"


def test_a_count_of_zero_from_a_blind_range_leads_with_the_blindness(tmp_path):
    log = store(tmp_path, n=0)
    result = retrieve(log, DAY, [gap(T0, T0 + 10 * HOUR)])
    rendered = count_moments(result, lambda _c: None).render()
    assert "0 times that I saw" in rendered
    assert "wasn't watching" in rendered


def test_singular_reads_naturally(tmp_path):
    log = store(tmp_path, n=1)
    result = retrieve(log, DAY, [])
    assert count_moments(result, lambda _c: None).render() == "1 time that I saw"

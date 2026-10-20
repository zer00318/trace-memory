"""ORACLE for the Coverage audit (12_proof).

The atom's PROOF asks for a real day's coverage spot-checked BY HAND. The
hand-computation is written out in the fixture below so a reader can
re-derive it without running anything — that is the point of a spot check.
"""
from tracemem.app.coverage import (Coverage, Gap, compute,
                                   gaps_from_witnesses)
from tracemem.app.witness import WitnessKind, witness

H = 3_600_000
MIN = 60_000
DAY_START = 8 * H          # 08:00
DAY_END = 20 * H           # 20:00 — a 12-hour accountable day


def test_a_perfect_day_is_100_percent():
    c = compute([], DAY_START, DAY_END)
    assert c.percent == 100.0 and c.gaps == ()


def test_the_hand_computed_day():
    """SPOT CHECK, computed by hand here:

      day span        08:00 → 20:00            = 12h = 43,200,000 ms
      lunch away      12:00 → 12:45            =  45min  (counts against us)
      lid shut        15:00 → 15:30            =  30min  (counts against us)
      founder pause   17:00 → 18:00            =  60min  (DECLARED OFF)

      blind total     45 + 30 + 60             = 135 min = 8,100,000 ms
      watched         43,200,000 - 8,100,000   = 35,100,000 ms
      accountable     43,200,000 - 3,600,000   = 39,600,000 ms
      coverage        35,100,000 / 39,600,000  = 0.88636... → 88.6%
    """
    claims = [
        witness(WitnessKind.AWAY, t_ms=12 * H, span_t2_ms=12 * H + 45 * MIN),
        witness(WitnessKind.CLOSED_EYES, t_ms=15 * H, span_t2_ms=15 * H + 30 * MIN),
        witness(WitnessKind.FOUNDER_PAUSE, t_ms=17 * H, span_t2_ms=18 * H),
    ]
    c = compute(claims, DAY_START, DAY_END)
    assert c.watched_ms == 35_100_000
    assert c.declared_off_ms == 3_600_000
    assert c.accountable_ms == 39_600_000
    assert c.percent == 88.6
    assert len(c.gaps) == 3


def test_declared_off_time_leaves_the_denominator():
    """Charging the system for hours it was TOLD not to look would make the
    honest act of pausing look like a failure — the incentive that gets a
    privacy feature quietly abandoned."""
    paused = [witness(WitnessKind.FOUNDER_PAUSE, t_ms=10 * H, span_t2_ms=12 * H)]
    c = compute(paused, DAY_START, DAY_END)
    assert c.percent == 100.0, "a pause was counted as a miss"


def test_a_gap_is_charged_but_a_pause_is_not():
    gap = compute([witness(WitnessKind.GAP, t_ms=10 * H, span_t2_ms=12 * H)],
                  DAY_START, DAY_END)
    off = compute([witness(WitnessKind.FOUNDER_PAUSE, t_ms=10 * H,
                           span_t2_ms=12 * H)], DAY_START, DAY_END)
    assert gap.percent < 100.0 and off.percent == 100.0


def test_overlapping_blind_intervals_are_not_double_subtracted():
    """A lid shut while already away is ONE blind hour. Double-subtracting
    drives coverage negative, silently — it is only ever divided."""
    claims = [
        witness(WitnessKind.AWAY, t_ms=12 * H, span_t2_ms=14 * H),
        witness(WitnessKind.CLOSED_EYES, t_ms=12 * H + 30 * MIN, span_t2_ms=13 * H),
    ]
    c = compute(claims, DAY_START, DAY_END)
    assert c.watched_ms == 43_200_000 - 2 * H
    assert 0.0 <= c.fraction <= 1.0


def test_coverage_never_exceeds_one():
    c = compute([witness(WitnessKind.AWAY, t_ms=0, span_t2_ms=100 * H)],
                DAY_START, DAY_END)
    assert 0.0 <= c.fraction <= 1.0


def test_a_fully_blind_day_is_zero_not_an_error():
    c = compute([witness(WitnessKind.KILL_DETECTED, t_ms=DAY_START,
                         span_t2_ms=DAY_END)], DAY_START, DAY_END)
    assert c.percent == 0.0


def test_a_fully_paused_day_has_no_percentage_at_all():
    """Inventing 0% or 100% would both be lies about a day nobody asked us
    to watch."""
    c = compute([witness(WitnessKind.FOUNDER_PAUSE, t_ms=DAY_START,
                         span_t2_ms=DAY_END)], DAY_START, DAY_END)
    assert c.fraction is None and c.percent is None


def test_gaps_outside_the_day_are_ignored():
    yesterday = witness(WitnessKind.GAP, t_ms=1 * H, span_t2_ms=2 * H)
    assert gaps_from_witnesses([yesterday], DAY_START, DAY_END) == []


def test_a_gap_straddling_midnight_is_clipped_to_this_day():
    overnight = witness(WitnessKind.CLOSED_EYES, t_ms=2 * H, span_t2_ms=9 * H)
    gaps = gaps_from_witnesses([overnight], DAY_START, DAY_END)
    assert len(gaps) == 1
    assert gaps[0].t1_ms == DAY_START and gaps[0].t2_ms == 9 * H


def test_non_witness_claims_are_not_gaps():
    """Coverage is derived from witnesses ALONE — never from claim density,
    which would call a quiet afternoon of reading a blind spot."""
    from tests.helpers import make_claim
    assert gaps_from_witnesses([make_claim("read a page", t_ms=10 * H)],
                               DAY_START, DAY_END) == []


def test_point_witnesses_are_not_intervals():
    """A START witness has no t2 and blinds nothing."""
    assert gaps_from_witnesses([witness(WitnessKind.START, t_ms=9 * H)],
                               DAY_START, DAY_END) == []


def test_each_gap_carries_its_cause_for_the_negative_class():
    """'You were 88% covered' does not answer 'what happened at 2pm'."""
    from tracemem.app.gap_detector import detect_gap
    boot_gap = detect_gap(last_claim_t_ms=13 * H, last_witness_kind=None,
                          now_ms=14 * H)
    gaps = gaps_from_witnesses([boot_gap], DAY_START, DAY_END)
    assert gaps[0].cause == "no-clean-stop"
    assert "not watching" in str(gaps[0])


def test_the_cockpit_row_is_renderable():
    c = compute([witness(WitnessKind.AWAY, t_ms=12 * H, span_t2_ms=13 * H)],
                DAY_START, DAY_END)
    row = c.as_cockpit_row()
    assert row["coverage_pct"] and row["gap_count"] == 1
    assert isinstance(row["gaps"][0], str)


def test_missed_time_and_watched_time_account_for_the_whole_day():
    c = compute([witness(WitnessKind.AWAY, t_ms=12 * H, span_t2_ms=13 * H),
                 witness(WitnessKind.FOUNDER_PAUSE, t_ms=17 * H, span_t2_ms=18 * H)],
                DAY_START, DAY_END)
    assert c.watched_ms + c.missed_ms() == c.accountable_ms


def test_coverage_never_says_100_percent_while_a_hole_exists():
    """FOUND BY READING A REAL DIGEST (2026-07-29), which printed:

        I was watching 100% of this period. Blind for 4 interval(s).

    Both halves came from one Coverage object. round(99.96, 1) is 100.0, so
    over a long window a few real blind minutes rounded away entirely — the
    number said "I saw everything" about a period the next clause admitted it
    had not seen. The smallest possible version of the failure this product
    exists to refuse, in the one number a person uses to decide how much to
    trust an absence answer.
    """
    from tracemem.app.coverage import compute
    from tracemem.app.witness import WitnessKind, witness

    day0, day1 = 0, 200 * 3_600_000                  # a 200-hour window
    tiny = witness(WitnessKind.CLOSED_EYES, t_ms=1_000,
                   span_t2_ms=1_000 + 60_000)        # one blind minute
    cov = compute([tiny], day0, day1)

    assert cov.gaps                                  # there IS a hole
    assert cov.percent < 100.0                       # ...so it cannot say 100
    assert cov.percent == 99.9


def test_a_genuinely_perfect_day_still_reports_100():
    """The guard must not make honesty impossible: with no blind interval at
    all, 100% is the true answer and must survive."""
    from tracemem.app.coverage import compute
    cov = compute([], 0, 3_600_000)
    assert cov.percent == 100.0


# -- the rendered number, not just the computed one -------------------------

def test_the_rendered_percentage_never_says_100_while_a_hole_exists():
    """THE ORACLE, and the second time this exact sentence has been caught.

    `percent` already refused to round up to 100. Then the digest printed it
    with `%.0f`, which rounded 99.9 back to "100", and the founder's live
    report said:

        I was watching 100% of this period. Blind for 3 interval(s).

    A guard at the source is worth nothing if a formatter downstream may
    round. The text is now produced beside the rule.
    """
    c = Coverage(day_start_ms=0, day_end_ms=86_400_000,
                 watched_ms=86_365_000,        # 35s blind in a day
                 declared_off_ms=0, gaps=(Gap(1000, 36_000, kind="blind", cause="kill-detected",
                           declared_off=False),))

    assert c.percent == 99.9
    assert c.percent_text == "99.9%"
    assert "100" not in c.percent_text


def test_a_genuinely_complete_day_still_reads_as_100():
    """The guard must not tax an honest day: refusing to ever say 100 would
    be its own small lie, in the opposite direction."""
    c = Coverage(day_start_ms=0, day_end_ms=86_400_000,
                 watched_ms=86_400_000, declared_off_ms=0, gaps=())

    assert c.percent_text == "100%"


def test_a_fully_paused_day_has_no_percentage_to_render():
    c = Coverage(day_start_ms=0, day_end_ms=86_400_000,
                 watched_ms=0, declared_off_ms=86_400_000, gaps=())

    assert c.percent_text is None


def test_the_digest_renders_coverage_through_the_sanctioned_text():
    """The regression guard proper: assert on the STRING the founder reads,
    because every previous version of this bug passed a test on the number."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src/tracemem/app/digest.py"
    body = src.read_text()
    assert "cov.percent_text" in body
    # No format spec may be applied to the raw percentage in the digest.
    assert not re.search(r"%\.?\d*f%%\"?\s*%\s*cov\.percent\b", body), (
        "the digest must not re-format cov.percent — that is exactly how "
        "99.9 became '100' next to 'Blind for 3 interval(s)'")

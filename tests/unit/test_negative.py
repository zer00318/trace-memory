"""ORACLE for Negative + Time index (06_supplier).

Negative PROOF: "plant nothing, ask 'did I ever' → answer contains coverage
%, never a flat 'no'."
Time index PROOF: "Range scan on fixture week returns correct ids in <10ms."
"""
import re
import time

from tracemem.app.witness import WitnessKind, witness
from tracemem.supplier.negative import (
    WEAK_COVERAGE, AbsenceAnswer, answer_absence,
)
from tracemem.text.claim_log import ClaimLog
from tests.helpers import make_claim

H = 3_600_000
MIN = 60_000
DAY_START, DAY_END = 8 * H, 20 * H


def store(tmp_path, claims=()):
    log = ClaimLog(str(tmp_path / "neg.sqlite3"))
    for c in claims:
        log.append(c)
    return log


# -- the atom's PROOF ------------------------------------------------------
def test_planting_nothing_answers_with_coverage_never_a_flat_no(tmp_path):
    log = store(tmp_path, [
        witness(WitnessKind.AWAY, t_ms=12 * H, span_t2_ms=13 * H),
    ])
    ans = answer_absence(log, DAY_START, DAY_END)
    text = ans.render()

    assert ans.found == 0
    assert ans.coverage_pct is not None
    assert re.search(r"\d+(\.\d+)?%", text), f"no coverage % in: {text!r}"
    assert text.strip().lower() != "no"
    assert "while I was watching" in text


def test_a_bare_no_is_unreachable_from_any_input(tmp_path):
    """Not 'we remembered to add the caveat' — there is no branch that can
    emit a bare no. Exercised across the full range of coverage."""
    coverages = [i / 12 for i in range(13)] + [None]
    for cov in coverages:
        ans = AbsenceAnswer(found=0, coverage=cov, gaps=(),
                            t0_ms=DAY_START, t1_ms=DAY_END)
        text = ans.render().strip().lower()
        assert text not in ("no", "no.", "never", "never.")
        assert len(text) > 30, f"answer too bare at coverage={cov}: {text!r}"
        # every non-null coverage must SAY its number
        if cov is not None:
            assert "%" in text, f"coverage {cov} not disclosed: {text!r}"


def test_full_coverage_still_says_how_much_it_watched(tmp_path):
    log = store(tmp_path)
    ans = answer_absence(log, DAY_START, DAY_END)
    assert ans.coverage_pct == 100.0
    assert "100.0%" in ans.render()


def test_weak_coverage_leads_with_the_blindness(tmp_path):
    """Below half, 'never' misleads more than it informs — so the sentence
    changes shape rather than appending a caveat nobody reads."""
    log = store(tmp_path, [
        witness(WitnessKind.KILL_DETECTED, t_ms=DAY_START,
                span_t2_ms=DAY_START + 9 * H),
    ])
    ans = answer_absence(log, DAY_START, DAY_END)
    assert ans.weak is True
    text = ans.render()
    assert "cannot tell you" in text
    assert "Not while I was watching" not in text


def test_a_fully_paused_period_says_so_rather_than_claiming_absence(tmp_path):
    """Coverage is None: inventing 0% or 100% would both be lies about a
    period nobody asked us to watch."""
    log = store(tmp_path, [
        witness(WitnessKind.FOUNDER_PAUSE, t_ms=DAY_START, span_t2_ms=DAY_END),
    ])
    ans = answer_absence(log, DAY_START, DAY_END)
    assert ans.coverage is None
    assert "at your request" in ans.render()


def test_the_gaps_are_itemised_not_just_totalled(tmp_path):
    """'88% covered' does not answer 'what happened at 2pm'."""
    log = store(tmp_path, [
        witness(WitnessKind.AWAY, t_ms=12 * H, span_t2_ms=13 * H),
        witness(WitnessKind.CLOSED_EYES, t_ms=15 * H, span_t2_ms=16 * H),
    ])
    ans = answer_absence(log, DAY_START, DAY_END)
    assert len(ans.gaps) == 2
    assert "Blind spots:" in ans.render()


def test_many_gaps_are_summarised_not_dumped(tmp_path):
    ws = [witness(WitnessKind.AWAY, t_ms=(9 + i) * H,
                  span_t2_ms=(9 + i) * H + 10 * MIN) for i in range(6)]
    ans = answer_absence(store(tmp_path, ws), DAY_START, DAY_END)
    assert "+3 more" in ans.render()


def test_an_overnight_gap_reaching_into_the_window_is_seen(tmp_path):
    """The witness is stamped when the absence STARTED — the previous
    evening. Without a lookback the morning's blindness is invisible and
    coverage silently reads 100%."""
    log = store(tmp_path, [
        witness(WitnessKind.CLOSED_EYES, t_ms=-2 * H, span_t2_ms=DAY_START + 2 * H),
    ])
    ans = answer_absence(log, DAY_START, DAY_END)
    assert ans.coverage_pct is not None and ans.coverage_pct < 100.0


def test_hits_are_not_an_absence(tmp_path):
    ans = answer_absence(store(tmp_path), DAY_START, DAY_END,
                         hits=["a claim"])
    assert ans.found == 1 and ans.render().startswith("found")


def test_the_weak_threshold_is_labelled_uncalibrated():
    from pathlib import Path
    import tracemem.supplier.negative as mod
    assert "UNCALIBRATED" in Path(mod.__file__).read_text()
    assert WEAK_COVERAGE == 0.5


# -- Time index PROOF ------------------------------------------------------
def test_range_scan_returns_correct_ids(tmp_path):
    log = ClaimLog(str(tmp_path / "idx.sqlite3"))
    planted = []
    for i in range(200):
        c = make_claim(f"claim {i}", t_ms=i * MIN, frame=f"f{i}")
        log.append(c)
        planted.append((i * MIN, c.id))

    lo, hi = 50 * MIN, 100 * MIN
    expected = {cid for t, cid in planted if lo <= t <= hi}
    assert set(log.ids_in_range(lo, hi)) == expected
    assert log.count_in_range(lo, hi) == len(expected)


def test_range_scan_on_a_fixture_week_is_under_10ms(tmp_path):
    """The atom's PROOF, measured — not asserted."""
    log = ClaimLog(str(tmp_path / "week.sqlite3"))
    # A week at one claim per minute: 10,080 claims.
    for i in range(10_080):
        log.append(make_claim(f"c{i}", t_ms=i * MIN, frame=f"f{i}"))

    best = min(_time_scan(log, 2 * 24 * 60 * MIN, 4 * 24 * 60 * MIN)
               for _ in range(5))
    print(f"\nTIME INDEX: best range scan over a 10,080-claim week = "
          f"{best * 1000:.2f}ms")
    assert best < 0.010, f"range scan took {best * 1000:.2f}ms, budget 10ms"


def _time_scan(log, lo, hi):
    t0 = time.perf_counter()
    log.ids_in_range(lo, hi)
    return time.perf_counter() - t0


def test_the_id_scan_does_not_decrypt(tmp_path):
    """The whole reason it is a separate door from read_range: at life scale
    the difference between an id scan and a full materialise is the
    difference between a scan and a stall."""
    log = ClaimLog(str(tmp_path / "cheap.sqlite3"))
    for i in range(2000):
        log.append(make_claim(f"c{i}", t_ms=i * MIN, frame=f"f{i}"))

    t0 = time.perf_counter()
    log.ids_in_range(0, 2000 * MIN)
    ids_t = time.perf_counter() - t0

    t0 = time.perf_counter()
    log.read_range(0, 2000 * MIN)
    full_t = time.perf_counter() - t0

    assert ids_t < full_t, (f"id scan ({ids_t*1000:.1f}ms) was not cheaper "
                            f"than full read ({full_t*1000:.1f}ms)")


def test_an_empty_range_returns_nothing_not_everything(tmp_path):
    log = ClaimLog(str(tmp_path / "empty.sqlite3"))
    log.append(make_claim("x", t_ms=5 * MIN))
    assert log.ids_in_range(100 * MIN, 200 * MIN) == []

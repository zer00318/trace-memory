"""ORACLE for Frontier spend control (02_extraction) + Frontier cost ledger
(13_workshop).

PROOFS:
  spend control "One spend ledger for ALL frontier use; per-run budget flag
                 required, running total logged, hard monthly cap that
                 REFUSES further calls."
  cost ledger   "Cockpit shows cumulative $ matching the ledger file."

The tests that carry the weight are the refusal ones. A budget that only
reports is a budget discovered on a statement — and these calls are made by
automated sessions that do not feel money, on a self-funded project.
"""
import json
import time

import pytest

from tools.spend_ledger import (
    MONTHLY_CAP_USD, RATES_AS_OF, SpendCapReached, check_before_spending,
    estimate_usd, load, month_total, publish, record, summary, total,
)

MONTH_MS = 31 * 24 * 3600 * 1000


def ledger(tmp_path):
    return str(tmp_path / "spend.jsonl")


# -- append-only, one file -------------------------------------------------
def test_a_missing_ledger_is_empty_not_an_error(tmp_path):
    assert load(ledger(tmp_path)) == []
    assert total(path=ledger(tmp_path)) == 0.0


def test_every_call_writes_one_line_with_its_why(tmp_path):
    path = ledger(tmp_path)
    record("ceiling", "claude-opus-4-8", 0.42, 1000, 200,
           why="frame 12 ceiling", path=path)
    entries = load(path)
    assert len(entries) == 1
    assert entries[0]["tool"] == "ceiling"
    assert entries[0]["usd"] == 0.42
    assert entries[0]["why"] == "frame 12 ceiling"
    assert entries[0]["tokens_in"] == 1000


def test_lines_accumulate_rather_than_replace(tmp_path):
    path = ledger(tmp_path)
    for i in range(5):
        record("t%d" % i, "m", 1.0, path=path)
    assert len(load(path)) == 5
    assert total(path=path) == 5.0


def test_a_negative_charge_is_refused(tmp_path):
    """A refund is its own line, not an edit to an old one. Allowing
    negatives here would make the ledger a place where totals can be
    quietly walked back."""
    with pytest.raises(ValueError):
        record("t", "m", -1.0, path=ledger(tmp_path))


# -- THE REFUSAL -----------------------------------------------------------
def test_the_cap_refuses_before_the_call_not_after(tmp_path):
    path = ledger(tmp_path)
    record("ceiling", "m", MONTHLY_CAP_USD, path=path)
    with pytest.raises(SpendCapReached) as exc:
        check_before_spending(0.01, path=path)
    assert "cap reached" in str(exc.value)


def test_the_check_uses_the_projected_total_not_the_current_one(tmp_path):
    """A check that only looks at what has ALREADY been spent lets the one
    call that blows the budget through — and that is usually the expensive
    one."""
    path = ledger(tmp_path)
    record("ceiling", "m", MONTHLY_CAP_USD - 1.0, path=path)
    check_before_spending(0.5, path=path)            # fits
    with pytest.raises(SpendCapReached):
        check_before_spending(5.0, path=path)        # would cross


def test_under_the_cap_the_check_returns_what_is_spent(tmp_path):
    path = ledger(tmp_path)
    record("ceiling", "m", 3.0, path=path)
    assert check_before_spending(1.0, path=path) == 3.0


def test_the_refusal_says_how_to_proceed(tmp_path):
    """An error that blocks without naming the deliberate way forward gets
    worked around rather than decided on."""
    path = ledger(tmp_path)
    record("t", "m", MONTHLY_CAP_USD, path=path)
    with pytest.raises(SpendCapReached) as exc:
        check_before_spending(1.0, path=path)
    assert "MONTHLY_CAP_USD" in str(exc.value)
    assert "founder decision" in str(exc.value)


def test_the_cap_is_monthly_not_lifetime(tmp_path):
    """Last month's spending must not permanently disable the tool."""
    path = ledger(tmp_path)
    old = int(time.time() * 1000) - 2 * MONTH_MS
    record("t", "m", MONTHLY_CAP_USD * 3, t_ms=old, path=path)
    check_before_spending(1.0, path=path)            # does not raise
    assert total(path=path) > MONTHLY_CAP_USD
    assert month_total(path=path) == 0.0


# -- estimates are labeled, never invented ---------------------------------
def test_an_unknown_model_gets_no_price_rather_than_a_guess():
    """A fabricated cost in a spend ledger is worse than a missing one: it
    looks like a measurement."""
    assert estimate_usd("some-new-model", 1000, 1000) is None


def test_a_known_model_estimates_from_stamped_rates():
    got = estimate_usd("claude-opus-4-8", 1_000_000, 1_000_000)
    assert got == pytest.approx(90.0)


def test_the_rates_carry_a_date():
    """Rates rot. A table without a date cannot be seen to be stale."""
    assert RATES_AS_OF
    assert len(RATES_AS_OF) == 10


# -- the display half ------------------------------------------------------
def test_the_summary_of_an_empty_ledger_says_so_rather_than_zero(tmp_path):
    got = summary(ledger(tmp_path))
    assert got["frontier_spend_calls"] == 0
    assert "no frontier calls" in got["frontier_spend_status"]


def test_the_cockpit_numbers_match_the_ledger_file(tmp_path):
    """THE ORACLE for the display atom: 'cockpit shows cumulative $ matching
    the ledger file'. The cockpit renders status.json's numbers block, so
    the check is that the published figure equals the file's own sum."""
    path = ledger(tmp_path)
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"updated": "x", "numbers": {"tests_green": 941},
                                  "now": []}))

    record("ceiling", "claude-opus-4-8", 1.25, path=path)
    record("stand-in", "claude-sonnet-5", 0.75, path=path)

    numbers = publish(path=path, status_path=str(status))
    from_file = sum(e["usd"] for e in load(path))

    assert numbers["frontier_spend_usd_total"] == pytest.approx(from_file)
    assert numbers["frontier_spend_usd_total"] == 2.0
    assert numbers["frontier_spend_calls"] == 2

    on_disk = json.loads(status.read_text())["numbers"]
    assert on_disk["frontier_spend_usd_total"] == 2.0
    assert on_disk["tests_green"] == 941          # nothing else disturbed


def test_the_summary_reports_distance_to_the_cap(tmp_path):
    """A running total without the cap beside it does not tell anyone
    whether to worry."""
    path = ledger(tmp_path)
    record("t", "m", MONTHLY_CAP_USD / 2, path=path)
    got = summary(path)
    assert got["frontier_spend_cap_usd"] == MONTHLY_CAP_USD
    assert got["frontier_spend_pct_of_cap"] == pytest.approx(50.0)
    assert got["frontier_spend_status"] == "under cap"


def test_at_the_cap_the_status_says_at_cap(tmp_path):
    path = ledger(tmp_path)
    record("t", "m", MONTHLY_CAP_USD, path=path)
    assert summary(path)["frontier_spend_status"] == "at cap"


def test_the_tool_runs(tmp_path):
    from tools.spend_ledger import main
    path = ledger(tmp_path)
    record("t", "m", 1.0, path=path)
    assert main(["--ledger", path]) == 0

"""ORACLE for Self-metrics (08_running).

PROOF: "After a day: self-metrics series exists; founder can see what TRACE
cost." The day-long half belongs to the 20-hour bench (11_machine); what is
provable now is that the sampler reads the REAL process tree, that the
number it judges against the budget is the right kind of number, and that
the series it hands the founder is honest about what it does not know.

The sharpest test here is `test_the_budget_is_judged_on_a_rate_not_an
_average`: `ps` %CPU is an average since launch, so a daemon that burned a
core for one hour and idled for twenty still reports high. Judging the
budget on that column would produce alerts about the past and silence about
the present.
"""
import os

import pytest

from tracemem.app.self_metrics import (
    CPU_BUDGET_PCT, MAX_SAMPLES, SUSTAINED_SAMPLES, Sample, SelfMetrics,
    _parse_cputime, read_tree,
)
from tracemem.app.witness import WitnessKind


class Scripted:
    """A process tree whose cumulative CPU time the test drives directly."""

    def __init__(self):
        self.cpu_seconds = 0.0
        self.rss_bytes = 64 * 1024 * 1024

    def __call__(self, _pids):
        return Sample(0, self.rss_bytes, self.cpu_seconds, 0.0, 1)


def metrics(scripted, **kw):
    return SelfMetrics(pids_fn=lambda: [1], reader=scripted, **kw)


# -- the real read ---------------------------------------------------------
def test_it_reads_this_actual_process():
    """Not a fake: `ps` against our own pid. A wrong column order or a units
    mistake shows up here."""
    s = read_tree([os.getpid()])
    assert s.n_procs == 1
    assert s.rss_bytes > 1024 * 1024          # any live Python is over 1 MiB
    assert s.cpu_seconds >= 0.0


def test_a_dead_pid_does_not_take_the_sampler_down():
    """A child that exits between listing and sampling is normal. A metrics
    sampler that raised on it would kill the daemon it measures."""
    s = read_tree([999_999])
    assert s.n_procs == 0
    assert s.rss_bytes == 0


def test_an_empty_pid_list_is_not_an_error():
    assert read_tree([]).n_procs == 0


def test_the_tree_sums_rather_than_reporting_one_process():
    """The daemon has children. Reporting only the parent would understate
    the bill, which is the direction a self-measurement must never err in."""
    one = read_tree([os.getpid()])
    two = read_tree([os.getpid(), os.getppid()])
    assert two.n_procs >= one.n_procs
    assert two.rss_bytes >= one.rss_bytes


# -- the ps TIME format ----------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("0:00.00", 0.0),
    ("1:30.00", 90.0),
    ("10:00.50", 600.5),
    ("1:00:00", 3600.0),
    ("2-03:00:00", 2 * 86400 + 3 * 3600),
    ("", 0.0),
])
def test_cputime_parses_every_form_ps_emits(text, expected):
    """The hh:mm:ss form appears the moment the daemon has been up an hour —
    which is exactly when self-metrics start mattering. A parser that only
    handled mm:ss would silently under-report by a factor of sixty."""
    assert _parse_cputime(text) == pytest.approx(expected)


# -- the budget, judged correctly -----------------------------------------
def test_the_first_sample_cannot_judge_anything():
    """One sample is a reading, not a rate."""
    scripted = Scripted()
    m = metrics(scripted)
    assert m.sample(0) is None


def test_the_budget_is_judged_on_a_rate_not_an_average():
    """THE distinction. Between two samples 60s apart, 30s of CPU is 50% of a
    core — over budget — regardless of what the process averaged since it
    launched."""
    scripted = Scripted()
    m = metrics(scripted, sustained=1)
    m.sample(0)
    scripted.cpu_seconds = 30.0
    w = m.sample(60_000)
    assert w is not None
    assert w.structured["cpu_pct"] == pytest.approx(50.0, abs=0.1)


def test_a_quiet_daemon_says_nothing():
    scripted = Scripted()
    m = metrics(scripted)
    for i in range(20):
        scripted.cpu_seconds += 1.0            # 1s CPU per 60s = 1.7%
        assert m.sample(i * 60_000) is None


def test_a_spike_is_not_a_habit():
    """One over-budget minute must not alert. The user is not owed a warning
    every time a night job runs."""
    scripted = Scripted()
    m = metrics(scripted)
    m.sample(0)
    scripted.cpu_seconds += 40.0               # one loud minute
    assert m.sample(60_000) is None
    for i in range(2, 10):                     # then quiet
        scripted.cpu_seconds += 1.0
        assert m.sample(i * 60_000) is None


def test_sustained_cost_is_witnessed_once_per_run():
    """Once per RUN, not once per sample — the same law as the permission
    nudge. A metric that alerts every minute is a metric that gets muted."""
    scripted = Scripted()
    m = metrics(scripted)
    m.sample(0)

    fired = []
    for i in range(1, 15):
        scripted.cpu_seconds += 30.0           # 50% of a core, every minute
        w = m.sample(i * 60_000)
        if w is not None:
            fired.append(w)

    assert len(fired) == 1
    assert fired[0].structured["witness"] == WitnessKind.OVERBUDGET.value
    assert fired[0].structured["samples"] == SUSTAINED_SAMPLES
    assert fired[0].scope == "memory"


def test_coming_back_under_budget_re_arms_the_alert():
    """A second overrun after a genuine recovery is a second real event."""
    scripted = Scripted()
    m = metrics(scripted, sustained=2)
    m.sample(0)

    def burn(start, n, seconds):
        out = []
        for i in range(n):
            scripted.cpu_seconds += seconds
            w = m.sample((start + i) * 60_000)
            if w:
                out.append(w)
        return out

    assert len(burn(1, 3, 30.0)) == 1          # first overrun
    burn(10, 3, 0.5)                           # recover
    assert len(burn(20, 3, 30.0)) == 1         # second overrun alerts again


def test_a_shrinking_tree_is_not_reported_as_negative_cost():
    """When a child dies, cumulative CPU time goes backwards. That is not a
    measurement of anything and must not be reported as one."""
    scripted = Scripted()
    m = metrics(scripted, sustained=1)
    m.sample(0)
    scripted.cpu_seconds = -5.0
    assert m.sample(60_000) is None


def test_two_samples_at_the_same_instant_produce_no_rate():
    scripted = Scripted()
    m = metrics(scripted, sustained=1)
    m.sample(1000)
    scripted.cpu_seconds += 100.0
    assert m.sample(1000) is None


# -- what the founder sees -------------------------------------------------
def test_the_summary_is_empty_rather_than_zero_when_it_knows_nothing():
    """Zeros would read as 'TRACE cost nothing', which is a claim. An empty
    summary is the truth: not enough samples to say."""
    m = metrics(Scripted())
    assert m.summary() == {}
    m.sample(0)
    assert m.summary() == {}


def test_the_summary_reports_average_peak_and_the_budget_it_is_judged_by():
    scripted = Scripted()
    m = metrics(scripted)
    m.sample(0)
    for i, burn in enumerate([1.0, 30.0, 1.0], start=1):
        scripted.cpu_seconds += burn
        m.sample(i * 60_000)

    s = m.summary()
    assert s["samples"] == 4
    assert s["cpu_pct_peak"] == pytest.approx(50.0, abs=0.1)
    assert 0 < s["cpu_pct_avg"] < s["cpu_pct_peak"]
    assert s["budget_pct"] == CPU_BUDGET_PCT
    assert s["rss_bytes_peak"] > 0


def test_the_series_is_what_a_bench_report_would_render():
    scripted = Scripted()
    m = metrics(scripted)
    for i in range(3):
        scripted.cpu_seconds += 1.0
        m.sample(i * 60_000)
    series = m.series()
    assert len(series) == 3
    assert set(series[0]) == {"t_ms", "rss_bytes", "cpu_seconds", "n_procs"}


def test_the_series_is_bounded():
    """An always-on daemon must not grow a list forever."""
    scripted = Scripted()
    m = metrics(scripted)
    for i in range(MAX_SAMPLES + 50):
        scripted.cpu_seconds += 0.1
        m.sample(i * 60_000)
    assert len(m.samples) == MAX_SAMPLES


def test_the_budget_constant_is_a_tenth_of_a_core():
    """Pinned so a future edit is a decision, not a drift."""
    assert CPU_BUDGET_PCT == 10.0

"""OWNS: what TRACE costs the machine it runs on (map: Running / Self-metrics).

The product asks for an always-on process on a laptop. The only honest
version of that bargain is that the user can SEE the bill: not a claim that
it is light, a series they can look at. This module samples the daemon's own
process tree and keeps the numbers.

WHY `ps` AND NOT A LIBRARY: psutil is not in this venv, and adding a
dependency to read two integers is a poor trade. The sample runs once a
minute, so a fork per sample is 1440 forks a day — irrelevant. Note the
contrast with app/memory_pressure.py, which is polled far more often and
therefore goes through ctypes rather than a subprocess. Same reasoning,
opposite conclusion, because the cadence is different.

WHAT %CPU FROM `ps` ACTUALLY MEANS, since it is easy to report wrongly: it
is the process's CPU time over its LIFETIME divided by its elapsed
lifetime — an average since launch, not an instantaneous reading. For a
long-running daemon that makes it a lagging indicator: a process that burned
a core for its first hour and idled for the next twenty still reports a
misleadingly high number, and one that starts burning now will take hours to
show it. So the budget check below is computed from the DELTA between
consecutive samples of CPU TIME, not from %CPU. The %CPU column is kept for
display only, and labeled as the since-launch average it is.

THE BUDGET IS DECLARED, NOT MEASURED (2026-07-22). The 20-hour bench
(11_machine) is the atom that will produce a real number. Until it runs,
CPU_BUDGET_PCT is a stake in the ground, marked as one.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim

# PROVISIONAL (2026-07-22) — declared, replaced by the 20-hour bench.
# Anchored to the promise rather than to a measurement: an always-on daemon
# that costs more than a tenth of one core is a daemon a laptop user will
# eventually notice and kill, and a memory that gets killed remembers
# nothing. Expressed per-core-percent, so 10.0 means a tenth of one core.
CPU_BUDGET_PCT = 10.0

# A spike is not a problem; a habit is. Five consecutive minute-samples over
# budget is five minutes of sustained cost, which is long enough to be real
# and short enough that the user is probably still at the machine.
SUSTAINED_SAMPLES = 5

# Cheap ceiling on retained samples: a day at one per minute, plus slack.
MAX_SAMPLES = 1500


@dataclass(frozen=True)
class Sample:
    t_ms: int
    rss_bytes: int
    cpu_seconds: float      # cumulative CPU time of the whole tree
    cpu_pct_since_launch: float
    n_procs: int


def _parse_cputime(text: str) -> float:
    """`ps -o time=` gives [[dd-]hh:]mm:ss. Parsed here rather than trusted
    to a single format, because the two-field form appears the moment a
    daemon has been up for an hour and that is precisely when this starts
    mattering."""
    text = text.strip()
    if not text:
        return 0.0
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [float(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hours, minutes, seconds = parts
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def read_tree(pids: Sequence[int]) -> Sample:
    """Sample the given processes. Raises nothing for dead pids — a child
    that exited between the caller's list and this read is normal, and a
    metrics sampler that crashes on it would take the daemon with it."""
    if not pids:
        return Sample(0, 0, 0.0, 0.0, 0)
    out = subprocess.run(
        ["ps", "-o", "rss=,%cpu=,time=", "-p", ",".join(str(p) for p in pids)],
        capture_output=True, text=True)

    rss_kb = 0
    cpu_pct = 0.0
    cpu_seconds = 0.0
    n = 0
    for line in out.stdout.strip().splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3:
            continue
        rss_kb += int(fields[0])
        cpu_pct += float(fields[1])
        cpu_seconds += _parse_cputime(fields[2])
        n += 1
    return Sample(0, rss_kb * 1024, cpu_seconds, cpu_pct, n)


class SelfMetrics:
    """Keeps the series and decides when the cost is worth witnessing."""

    def __init__(self, pids_fn: Callable[[], Sequence[int]],
                 reader: Optional[Callable[[Sequence[int]], Sample]] = None,
                 budget_pct: float = CPU_BUDGET_PCT,
                 sustained: int = SUSTAINED_SAMPLES) -> None:
        self._pids_fn = pids_fn
        self._read = reader or read_tree
        self.budget_pct = budget_pct
        self.sustained = sustained
        self.samples: List[Sample] = []
        self._over_run = 0
        self._witnessed_this_run = False

    def sample(self, now_ms: int) -> Optional[Claim]:
        """Take one sample. Returns an OVERBUDGET witness the first time a
        run of over-budget samples reaches the sustained length — once per
        run, not once per sample, for the same reason the permission nudge
        fires once."""
        raw = self._read(self._pids_fn())
        s = Sample(now_ms, raw.rss_bytes, raw.cpu_seconds,
                   raw.cpu_pct_since_launch, raw.n_procs)

        rate = self._rate_since_previous(s)
        self.samples.append(s)
        if len(self.samples) > MAX_SAMPLES:
            del self.samples[:len(self.samples) - MAX_SAMPLES]

        if rate is None:
            return None                     # first sample has no delta to read

        if rate > self.budget_pct:
            self._over_run += 1
            if self._over_run >= self.sustained and not self._witnessed_this_run:
                self._witnessed_this_run = True
                return witness(
                    WitnessKind.OVERBUDGET, t_ms=now_ms,
                    detail=("self cost %.1f%% of a core sustained over %d "
                            "samples (budget %.1f%%); RSS %.0f MiB"
                            % (rate, self._over_run, self.budget_pct,
                               s.rss_bytes / (1024 * 1024))),
                    structured_extra={"cpu_pct": round(rate, 2),
                                      "budget_pct": self.budget_pct,
                                      "samples": self._over_run,
                                      "rss_bytes": s.rss_bytes})
        else:
            self._over_run = 0
            self._witnessed_this_run = False
        return None

    def _rate_since_previous(self, s: Sample) -> Optional[float]:
        """CPU percent between the last two samples, from CPU-TIME delta.

        This is the number %CPU cannot give: an instantaneous-ish rate rather
        than an average since launch.
        """
        if not self.samples:
            return None
        prev = self.samples[-1]
        elapsed_s = (s.t_ms - prev.t_ms) / 1000.0
        if elapsed_s <= 0:
            return None
        cpu_delta = s.cpu_seconds - prev.cpu_seconds
        if cpu_delta < 0:
            # The tree changed shape (a child died): cumulative time went
            # backwards. Not a measurement, so it is not reported as one.
            return None
        return 100.0 * cpu_delta / elapsed_s

    # -- what the cockpit and the founder read -----------------------------
    def series(self) -> List[dict]:
        return [{"t_ms": s.t_ms, "rss_bytes": s.rss_bytes,
                 "cpu_seconds": s.cpu_seconds, "n_procs": s.n_procs}
                for s in self.samples]

    def summary(self) -> dict:
        """The one-glance version. Empty dict when there is nothing to say —
        never zeros, which would read as 'it cost nothing'."""
        if len(self.samples) < 2:
            return {}
        rates = []
        for prev, cur in zip(self.samples, self.samples[1:]):
            elapsed = (cur.t_ms - prev.t_ms) / 1000.0
            delta = cur.cpu_seconds - prev.cpu_seconds
            if elapsed > 0 and delta >= 0:
                rates.append(100.0 * delta / elapsed)
        if not rates:
            return {}
        return {
            "samples": len(self.samples),
            "cpu_pct_avg": round(sum(rates) / len(rates), 2),
            "cpu_pct_peak": round(max(rates), 2),
            "rss_bytes_peak": max(s.rss_bytes for s in self.samples),
            "budget_pct": self.budget_pct,
            "over_budget_now": self._over_run > 0,
        }

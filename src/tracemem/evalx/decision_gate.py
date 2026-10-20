"""OWNS: the pre-registered verdict (spec X §5, F4; loop exit per ruling A4).

The thresholds were committed before any data existed. Moving them after
seeing results requires a written, dated justification in the report — this
module deliberately has no way to pass different thresholds in.
"""
from __future__ import annotations

from dataclasses import dataclass

EMERGENT_HIGH = 0.6     # baseline already surfaces the unasked -> shed wins
EMERGENT_LOW = 0.3      # baseline blind to the unasked
FACTUAL_BAR = 0.6       # below this, capture/extraction is the bottleneck
MAX_UPSTREAM_LOOPS = 2  # ruling A4: then the verdict is CAPTURE_BOUND


@dataclass(frozen=True)
class Verdict:
    code: str            # SHED_SUFFICES | CATHEDRAL_JUSTIFIED | FIX_UPSTREAM | CAPTURE_BOUND
    reason: str


def verdict(report: dict, prior_upstream_loops: int = 0) -> Verdict:
    e = float(report["emergent_correct"])
    f = float(report["factual_correct"])
    if e >= EMERGENT_HIGH:
        return Verdict("SHED_SUFFICES",
                       "baseline emergent %.2f >= %.2f: the napkin's heart is "
                       "refuted by evidence; build better RAG, not 04" % (e, EMERGENT_HIGH))
    if e < EMERGENT_LOW and f >= FACTUAL_BAR:
        return Verdict("CATHEDRAL_JUSTIFIED",
                       "competent rememberer (factual %.2f) still blind to the "
                       "unasked (emergent %.2f): failures.md is the 04 "
                       "requirements doc" % (f, e))
    if prior_upstream_loops >= MAX_UPSTREAM_LOOPS:
        return Verdict("CAPTURE_BOUND",
                       "factual %.2f after %d upstream repair loops: bottleneck "
                       "is sensing, not memory; cathedral decision deferred, "
                       "dated (A4)" % (f, prior_upstream_loops))
    return Verdict("FIX_UPSTREAM",
                   "muddy middle (emergent %.2f, factual %.2f): the bug is born "
                   "at write time — fix 01/07 and re-run (loop %d of %d)"
                   % (e, f, prior_upstream_loops + 1, MAX_UPSTREAM_LOOPS))

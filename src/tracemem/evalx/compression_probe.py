"""OWNS: the novelty curve — does a life plateau? (spec X §4, P7 risk #1).

Per period p: novelty(p) = fraction of period-p claims whose nearest-neighbor
similarity against PRIOR periods is below theta_novel. A falling curve is the
compact-model premise holding; flat/rising after the first periods means the
"life compresses" bet is in trouble regardless of RAG-vs-cathedral.
Reports; never gates (the decision_gate decides).

TWO THINGS MADE THIS UNRUNNABLE ON A REAL LIFE, and both are fixed here
(2026-08-28) rather than worked around by never running it.

1. IT RE-EMBEDDED THE WHOLE STORE. `novelty_curve` called `embedder.embed`
   per claim, after `baseline_rag` had already embedded the same claims for
   the same run. The caller now hands its vectors in.

2. IT COMPARED EVERYTHING TO EVERYTHING. `max(_cos(v, u) for u in prior)` over
   a growing prior is O(n²): on the founder's 633,805 claims that is about
   7.7e13 multiply-adds in interpreted Python, which is not slow, it is
   never. So the comparison is BOUNDED and the bound is declared in the
   result rather than hidden: at most `max_per_period` claims are scored per
   period, against a reservoir of at most `prior_cap` earlier vectors,
   evenly spaced so the sample spans the whole past instead of its beginning.

   This makes the curve an ESTIMATE. It always was one — theta_novel is
   uncalibrated and a day is an arbitrary period — and the module's own rule
   is that it reports and never gates. A bounded estimate that runs beats an
   exact number nobody can compute.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

MS_DAY = 86_400_000

# How many of a period's claims are scored, and how much of the past they are
# scored against. Both UNCALIBRATED: they are the shapes of "enough to see a
# trend" and "enough of the past to be representative", not measured values.
MAX_PER_PERIOD = 400
PRIOR_CAP = 4000


def _cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _spread(items: Sequence, cap: int) -> List:
    """At most `cap` items, evenly spaced — a sample of the WHOLE range.

    Taking the first `cap` would compare a period against only the oldest
    part of the past, which reads as novelty that is really just distance in
    time.
    """
    items = list(items)
    if cap <= 0 or len(items) <= cap:
        return items
    step = len(items) / float(cap)
    return [items[int(i * step)] for i in range(cap)]


def _max_sim(vecs, prior):
    """For each vector, its best similarity against the prior set."""
    try:
        import numpy as np
        a = np.asarray(vecs, dtype=np.float32)
        b = np.asarray(prior, dtype=np.float32)
        return (a @ b.T).max(axis=1)
    except Exception:                                         # noqa: BLE001
        return [max(_cos(v, u) for u in prior) for v in vecs]


def novelty_curve(claim_store, embedder, theta_novel: float = 0.35,
                  period_ms: int = MS_DAY,
                  claims: Optional[Sequence] = None,
                  vectors: Optional[Sequence] = None,
                  max_per_period: int = MAX_PER_PERIOD,
                  prior_cap: int = PRIOR_CAP) -> List[Tuple[int, float]]:
    """(period, novel fraction) per period, oldest first.

    Pass `claims` and `vectors` together to reuse an embedding pass that has
    already happened; without them this embeds what the store hands over,
    which is the original behaviour and is fine on a fixture.
    """
    if claims is None:
        claims = list(claim_store.all_claims())
        vectors = None
    if vectors is None:
        many = getattr(embedder, "embed_many", None)
        vectors = (many([c.text for c in claims]) if many is not None
                   else [embedder.embed(c.text) for c in claims])

    by_period: Dict[int, List] = defaultdict(list)
    for c, v in zip(claims, vectors):
        by_period[c.coord.t_ms // period_ms].append(v)

    curve: List[Tuple[int, float]] = []
    prior: List = []
    for p in sorted(by_period):
        vecs = by_period[p]
        scored = _spread(vecs, max_per_period)
        if prior and scored:
            against = _spread(prior, prior_cap)
            sims = _max_sim(scored, against)
            novel = sum(1 for s in sims if s < theta_novel)
            curve.append((p, novel / len(scored)))
        else:
            curve.append((p, 1.0))          # everything is novel to an empty memory
        prior.extend(vecs)
    return curve


def plateaus(curve, after: int = 2, floor: float = 0.25) -> bool:
    """True iff novelty settles under `floor` once the first `after` periods
    are past — the operational reading of 'routines dominate'."""
    tail = [rate for i, (_p, rate) in enumerate(curve) if i >= after]
    return bool(tail) and (sum(tail) / len(tail)) < floor

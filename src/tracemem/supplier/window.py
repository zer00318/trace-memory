"""SUPPLYING A STRETCH OF TIME, rather than a needle in one.

The supplier had exactly one retrieval shape: embed the question, cosine it
against node summaries, spread two hops, pack node renderings. That shape
answers "who is Priya" and cannot answer "what was I doing on August 3",
because the node graph holds no time — and the measured consequence was an
answer of `not in memory` about a day containing 29,250 claims, built from a
context whose top entries were ENTITY `Aug`, ENTITY `Day`, ENTITY `Year`.

A question that names a time is a different question, and the store already
indexes for it (`ix_claim_time`, and `read_range` returns 29,250 claims in
2.1 seconds). So this path never touches the graph:

    question names a window → read the claims in it → moments → pack

WHAT MAKES IT HONEST. `range_retrieval` (built 2026-07, never wired) already
computes coverage from the gap witnesses and phrases the caveat: "I was
watching 82% of yesterday". That distinction is the whole product promise
here — "nothing happened" and "I wasn't watching" are different answers, and
only one of them is ever true. An empty window returns the second, with the
witnessed reason, and never the first.

BUDGET. Moments are packed longest-first because a person asking what they
did means the hours, not the thirty seconds — then re-sorted into time order
before rendering, because a timeline out of order reads as noise and invites
the model to invent an ordering.
"""
from __future__ import annotations

import threading

from typing import Optional, Sequence

from tracemem.domain.grading import grade_for
from tracemem.supplier import timeline
from tracemem.supplier.packer import Context, CtxBlock
from tracemem.supplier.range_retrieval import Verdict, retrieve

# Reserve for the coverage caveat and the summary line, so a full budget can
# never squeeze out the sentence that says how much of the window was seen.
CAVEAT_RESERVE_TOKENS = 120


# ONE FOLD PER STORE, FOR THE MOST RECENT PERIOD START (2026-10-15).
# "which apps did I use most today?" took 66s, 61s of it decrypting all ~185k
# claims of the day, on every ask. The timeline is a fold over time-ordered
# claims (timeline.TimelineFold), so the fold for [t0, T] fed the claims in
# (T, t1] IS the timeline for [t0, t1]. A count over the plaintext time index
# proves [t0, T] is unchanged; a late claim inside it (macro eye, phone sync)
# changes the count and the period is rebuilt from scratch.
_FOLD_LOCK = threading.Lock()
_FOLDS: dict = {}


def _timeline_for(claim_log, rng):
    """(moments, claims decrypted, "full"|"incremental")."""
    scope = getattr(claim_log, "_cache_scope", None)
    count = getattr(claim_log, "count_range", None)
    # File-backed stores only: an in-memory store's scope is id(self), which
    # Python reuses after collection, and a dead store's fold must never be
    # served to a new one.
    if scope is None or count is None or not isinstance(scope[0], str):
        claims = claim_log.read_range(rng.t0_ms, rng.t1_ms)
        return timeline.build(claims), len(claims), "full"
    with _FOLD_LOCK:
        entry = _FOLDS.get(scope)
        if (entry is not None and entry["t0"] == rng.t0_ms
                and entry["through"] <= rng.t1_ms
                and count(rng.t0_ms, entry["through"]) == entry["n"]):
            fold = entry["fold"]
            new = claim_log.read_range(entry["through"] + 1, rng.t1_ms)
            fold.feed(new)
            through = max([entry["through"]] + [c.coord.t_ms for c in new])
            entry.update(through=through, n=count(rng.t0_ms, through))
            return fold.result(), len(new), "incremental"
        claims = claim_log.read_range(rng.t0_ms, rng.t1_ms)
        fold = timeline.TimelineFold()
        fold.feed(claims)
        through = max([rng.t0_ms - 1] + [c.coord.t_ms for c in claims])
        _FOLDS[scope] = {"t0": rng.t0_ms, "through": through, "fold": fold,
                         "n": count(rng.t0_ms, through)}
        return fold.result(), len(claims), "full"


def _witnesses(claim_log, rng):
    by_bidder = getattr(claim_log, "read_range_by_bidder", None)
    if by_bidder is not None:
        return [c for c in by_bidder(rng.t0_ms, rng.t1_ms, "system:witness")
                if (c.structured or {}).get("witness")]
    return [c for c in claim_log.read_range(rng.t0_ms, rng.t1_ms)
            if (c.structured or {}).get("witness")]


def supply_window(claim_log, rng, budget_tokens: int, tokenizer=None,
                  witnesses: Optional[Sequence] = None,
                  moments_out: Optional[dict] = None) -> Context:
    """Claims in `rng` → a packed timeline Context.

    `witnesses` are the gap witnesses used for coverage. They are read from
    the same range when not supplied; a caller that already has them (the
    digest does) passes them in rather than paying the read twice.
    """
    import time as _t
    tokenizer = tokenizer or (lambda s: max(1, len(s) // 4))
    _w0, _c0 = _t.monotonic(), _t.thread_time()
    moments, read, how = _timeline_for(claim_log, rng)
    _w1, _c1 = _t.monotonic(), _t.thread_time()
    if witnesses is None:
        witnesses = _witnesses(claim_log, rng)

    result = retrieve(claim_log, rng, witnesses)
    caveat = result.caveat()
    _w2, _c2 = _t.monotonic(), _t.thread_time()
    if moments_out is not None:
        # Where a slow period answer spent itself, wall vs this thread's CPU:
        # "today" took 77s live against ~3s on a synthetic store of the same
        # size (2026-10-09), and the difference had no name.
        moments_out["timing"] = (
            "%s: read %d claims + timeline %.1fs (cpu %.1fs), coverage %.1fs"
            % (how, read, _w1 - _w0, _c1 - _c0, _w2 - _w1))
        # The caller may need the moments themselves (a counted summary is
        # built from them) without reading the range a second time.
        moments_out["moments"] = moments
        moments_out["caveat"] = caveat
    if not moments:
        # NOT the same as "nothing happened" — see the module docstring.
        text = ("I have no perception at all for %s. %s"
                % (rng.phrase, caveat or "No gap witness explains it, "
                   "which means the eye was not running."))
        return Context(
            (CtxBlock("coverage", text, grade_for("witness"), (), rng.t0_ms),),
            tokenizer(text))

    blocks = []
    spent = 0
    if caveat:
        head = "COVERAGE: %s." % caveat
        blocks.append(CtxBlock("coverage", head, grade_for("witness"), (), rng.t0_ms))
        spent += tokenizer(head)

    room = budget_tokens - CAVEAT_RESERVE_TOKENS
    packed = []
    for m in sorted(moments, key=lambda m: m.duration_ms, reverse=True):
        # CAPPED BY COUNT AS WELL AS BY BUDGET. Twenty short moments fit the
        # token budget easily and still ruin the answer: the model walks all
        # twenty and produces a transcript instead of a summary. Measured on
        # a live three-minute window that became a twenty-moment recital.
        # The long moments are what the question is about; the rest were
        # passed through. What is dropped is counted and said.
        if len(packed) >= timeline.MAX_RENDERED_MOMENTS:
            break
        text = timeline.render([m])
        cost = tokenizer(text)
        if spent + cost > room:
            continue
        packed.append((m, text))
        spent += cost

    for m, text in sorted(packed, key=lambda mt: mt[0].t0_ms):
        blocks.append(CtxBlock(
            kind="moment",
            text=text,
            # A coverage line is derived from the GAP WITNESSES, so it is
            # graded as a witness — that is literally where it comes from.
            # The moment's own strongest witness. Never a literal: the
            # grade table is the single place that decides what a perceiver's
            # word is worth (tests/unit/test_grading.py enforces it).
            grade=m.grade if m.grade is not None else grade_for("ocr"),
            # The SAME strings the render puts in brackets — see
            # timeline.CITE_LEN. A mismatch here disables the never-lie guard.
            source_ids=tuple(timeline.cite_id(c) for c in m.claim_ids[:3]),
            t_ms=m.t0_ms))

    dropped = len(moments) - len(packed)
    if dropped > 0:
        # NO SILENT CAPS. A timeline that quietly omits a third of the day
        # reads as a complete day, and the model will summarise it as one.
        note = ("NOTE: %d shorter moment(s) are not shown — this period had "
                "more short stretches than fit. What is shown is the longest."
                % dropped)
        blocks.append(CtxBlock("coverage", note, grade_for("witness"), (), rng.t1_ms))
        spent += tokenizer(note)

    return Context(tuple(blocks), spent)


def window_verdict(claim_log, rng, witnesses=()) -> Verdict:
    return retrieve(claim_log, rng, witnesses).verdict

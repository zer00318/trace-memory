"""'Today' must not decrypt the whole day on every ask (2026-10-15: 66s).

The timeline is a fold; the incremental path must be the same timeline as a
rebuild, decrypt only what is new, and rebuild when the past changed.
"""
from __future__ import annotations

from tests.helpers import make_claim
from tracemem.supplier import timeline
from tracemem.supplier import window as window_mod
from tracemem.text.claim_log import ClaimLog

MIN = 60_000


def _day(n, t0=0, apps=("Brave", "Mail", "Code")):
    return [make_claim("line %d of something worth reading on screen" % i,
                       t_ms=t0 + i * 7_000, frame="f%d" % i,
                       app=apps[(i // 9) % len(apps)], bidder="peripheral:change")
            for i in range(n)]


def _shape(moments):
    return [(m.t0_ms, m.t1_ms, m.app, m.title, m.n_claims, tuple(m.samples),
             tuple(m.claim_ids)) for m in moments]


def test_a_fold_fed_in_parts_is_the_timeline_built_at_once():
    claims = _day(200)
    fold = timeline.TimelineFold()
    for k in range(0, 200, 37):
        fold.feed(claims[k:k + 37])
    assert _shape(fold.result()) == _shape(timeline.build(claims))


class _Rng:
    def __init__(self, t0, t1):
        self.t0_ms, self.t1_ms, self.phrase = t0, t1, "today"


def _counting(log):
    opened = []
    real = log._try_open

    def counted(*a):
        opened.append(a[0])
        return real(*a)

    log._try_open = counted
    return opened


def test_asking_again_decrypts_only_what_arrived_since(tmp_path):
    import tracemem.text.claim_log as cl
    window_mod._FOLDS.clear()
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    first = _day(120)
    for c in first:
        log.append(c)
    window_mod._timeline_for(log, _Rng(0, 10 * 60 * MIN))
    later = _day(30, t0=first[-1].coord.t_ms + 7_000)
    for c in later:
        log.append(c)
    cl._WIRE_CACHE.clear()
    opened = _counting(log)
    moments, read, how = window_mod._timeline_for(log, _Rng(0, 10 * 60 * MIN))
    assert how == "incremental" and read == 30 and len(opened) == 30
    assert _shape(moments) == _shape(timeline.build(first + later))


def test_a_late_claim_inside_the_cached_past_forces_a_true_rebuild(tmp_path):
    window_mod._FOLDS.clear()
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    first = _day(120)
    for c in first:
        log.append(c)
    window_mod._timeline_for(log, _Rng(0, 10 * 60 * MIN))
    late = make_claim("understood late by the macro eye about the kiln",
                      t_ms=first[50].coord.t_ms + 1, frame="late",
                      app="Preview", bidder="vlm_macro")
    log.append(late)
    moments, _read, how = window_mod._timeline_for(log, _Rng(0, 10 * 60 * MIN))
    assert how == "full"
    assert _shape(moments) == _shape(timeline.build(first + [late]))

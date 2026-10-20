"""ORACLE for Relation confidence (04_binder, ruling round four).

PROOF: contradictory evidence flips a relation; the old belief survives in
the trail; no silent overwrite (trail length grows).
"""
from tracemem.binder.relation import (
    AMBIGUOUS_BAND, Evidence, Relation, RelationSet, observe, why,
)


def ev(delta, t_ms=1000, source="eye", detail=""):
    return Evidence(t_ms=t_ms, source=source, delta=delta, detail=detail)


def worn(conf=0.5):
    return Relation("headphones", "worn-by", "founder", conf)


# -- the atom's PROOF ------------------------------------------------------
def test_contradictory_evidence_flips_and_leaves_a_trail():
    r = worn(0.5)
    r = observe(r, ev(+0.3, t_ms=1000, source="eye", detail="on the head"))
    assert r.believed is True and len(r.trail) == 1

    r = observe(r, ev(-0.5, t_ms=2000, source="eye", detail="on the desk"))
    assert r.believed is False
    assert len(r.trail) == 2, "the flip did not grow the trail"

    # The old belief SURVIVES, with the evidence that caused it.
    first = r.trail[0]
    assert first.now_believed is True and first.cause.detail == "on the head"


def test_no_silent_overwrite_the_old_object_is_untouched():
    """A caller holding yesterday's belief still holds yesterday's belief."""
    before = observe(worn(0.5), ev(+0.3))
    after = observe(before, ev(-0.5))
    assert before.confidence == 0.8 and before.believed is True
    assert after.confidence != before.confidence
    assert len(before.trail) == 1 and len(after.trail) == 2


def test_evidence_always_accumulates_even_without_a_flip():
    r = worn(0.5)
    for i in range(5):
        r = observe(r, ev(+0.02, t_ms=i))
    assert len(r.evidence) == 5


def test_the_trail_records_minds_changed_not_every_observation():
    """A trail that logged everything would be unreadable, and the flip
    would be lost among the noise."""
    r = worn(0.5)
    for i in range(10):
        r = observe(r, ev(+0.01, t_ms=i))
    assert len(r.evidence) == 10 and len(r.trail) == 1   # only the crossing


def test_confidence_is_clamped_not_wrapped():
    r = worn(0.9)
    r = observe(r, ev(+5.0))
    assert r.confidence == 1.0
    r = observe(r, ev(-99.0))
    assert r.confidence == 0.0


def test_belief_line_is_strict():
    """Exactly 0.5 is not a belief — 'more likely than not' means more."""
    assert Relation("a", "p", "b", 0.5).believed is False
    assert Relation("a", "p", "b", 0.5001).believed is True


def test_repeated_flips_each_leave_a_mark():
    r = worn(0.5)
    for i, d in enumerate([+0.3, -0.5, +0.5, -0.5]):
        r = observe(r, ev(d, t_ms=i))
    assert len(r.trail) == 4
    assert [t.now_believed for t in r.trail] == [True, False, True, False]


# -- "why do you think that" ----------------------------------------------
def test_why_answers_in_plain_words():
    r = observe(worn(0.5), ev(+0.3, t_ms=1000, source="eye",
                              detail="over both ears"))
    r = observe(r, ev(-0.4, t_ms=2000, source="eye", detail="on the desk"))
    lines = why(r)
    assert len(lines) == 2
    assert "came to believe" in lines[0] and "over both ears" in lines[0]
    assert "stopped believing" in lines[1]


def test_why_is_honest_when_it_never_changed_its_mind():
    assert "never changed its mind" in why(worn(0.9))[0]


# -- the ambiguous band the Binder bids on --------------------------------
def test_ambiguous_band_is_what_a_look_would_resolve():
    lo, hi = AMBIGUOUS_BAND
    assert Relation("a", "p", "b", (lo + hi) / 2).ambiguous is True
    assert Relation("a", "p", "b", 0.95).ambiguous is False
    assert Relation("a", "p", "b", 0.05).ambiguous is False


# -- the set readers actually use -----------------------------------------
def test_readers_see_only_the_current_best():
    rs = RelationSet()
    rs.observe("headphones", "worn-by", "founder", ev(+0.3))
    rs.observe("cup", "held-by", "founder", ev(-0.3))
    believed = rs.believed()
    assert len(believed) == 1 and believed[0].subject == "headphones"


def test_the_set_keeps_the_trail_across_updates():
    rs = RelationSet()
    rs.observe("h", "worn-by", "f", ev(+0.3, t_ms=1))
    rs.observe("h", "worn-by", "f", ev(-0.5, t_ms=2))
    assert len(rs.current("h", "worn-by", "f").trail) == 2


def test_ambiguous_relations_are_findable_for_bidding():
    rs = RelationSet()
    rs.observe("h", "worn-by", "f", ev(+0.05))       # 0.55: ambiguous
    rs.observe("c", "held-by", "f", ev(+0.45))       # 0.95: settled
    amb = rs.ambiguous()
    assert len(amb) == 1 and amb[0].subject == "h"


def test_distinct_predicates_do_not_collide():
    rs = RelationSet()
    rs.observe("h", "worn-by", "f", ev(+0.3))
    rs.observe("h", "owned-by", "f", ev(-0.3))
    assert rs.current("h", "worn-by", "f").believed is True
    assert rs.current("h", "owned-by", "f").believed is False


def test_str_says_when_it_is_doubted():
    assert "doubted" in str(Relation("h", "worn-by", "f", 0.2))
    assert "doubted" not in str(Relation("h", "worn-by", "f", 0.9))

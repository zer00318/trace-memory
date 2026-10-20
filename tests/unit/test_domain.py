"""Spec 00 oracles — the frozen vocabulary."""
import dataclasses

import pytest

from tests.helpers import make_claim
from tracemem.domain import (
    Attribute, Confidence, Episode, Grade, Node, NodeKind, SpanCoord,
    TimeRange, activation_now, boost, degrade, spread_deltas,
)


# ---- grade (00 §2) ----
def test_grade_ordering_total():
    assert Grade.VERBATIM > Grade.SPECIALIST > Grade.GIST > Grade.DERIVED > Grade.FLOOR

def test_degrade_one_step():
    assert degrade([Grade.VERBATIM, Grade.VERBATIM]) == Grade.SPECIALIST

def test_degrade_causal_costs_extra():
    assert degrade([Grade.DERIVED, Grade.DERIVED], causal=True) == Grade.FLOOR

def test_degrade_never_below_floor_and_never_above_min_parent():
    assert degrade([Grade.FLOOR]) == Grade.FLOOR
    for parents in ([Grade.VERBATIM, Grade.GIST], [Grade.SPECIALIST]):
        assert degrade(parents) < min(parents)

def test_degrade_monotonic_over_depth_chain():
    g = Grade.VERBATIM
    seen = [g]
    for _ in range(5):
        g = degrade([g])
        seen.append(g)
    assert seen == sorted(seen, reverse=True)


# ---- confidence (00 §3) ----
def test_confidence_combines_multiplicatively_down():
    c = Confidence(0.9).combine(Confidence(0.9))
    assert abs(c.value - 0.81) < 1e-9

def test_confidence_range_enforced():
    with pytest.raises(ValueError):
        Confidence(1.5)


# ---- coordinate stack (00 §1) ----
def test_full_stack_builds_and_is_frozen():
    c = make_claim()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.coord.span = None  # type: ignore[misc]

def test_bbox_must_be_normalized_and_ordered():
    with pytest.raises(ValueError):
        SpanCoord(bbox=(0.5, 0.5, 0.1, 0.9))
    with pytest.raises(ValueError):
        SpanCoord(bbox=(0.0, 0.0, 1.5, 1.0))


# ---- claim (00 §5) ----
def test_claim_is_immutable():
    c = make_claim()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.text = "mutated"  # type: ignore[misc]

def test_claim_id_deterministic_from_content():
    assert make_claim().id == make_claim().id
    assert make_claim().id != make_claim(text="other").id
    assert make_claim().content_hash() == make_claim().content_hash()


# ---- episode (00 §6) ----
def test_episode_grows_never_rewrites():
    e1 = Episode(id="e", t_range=TimeRange(0, 10), place_key="p", claim_ids=("a", "b"))
    e2 = e1.with_claims(["c", "a"])
    assert set(e1.claim_ids) <= set(e2.claim_ids)
    assert e1.claim_ids == ("a", "b")            # original untouched
    assert e2.claim_ids == ("a", "b", "c")       # dedup + order-stable


# ---- node (00 §7) ----
def test_emergent_node_requires_parents():
    with pytest.raises(ValueError):
        Node(id="n", kind=NodeKind.PATTERN)

def test_entity_node_forbids_parents():
    with pytest.raises(ValueError):
        Node(id="n", kind=NodeKind.ENTITY, parent_ids=("x",))

def test_attribute_requires_source_episode():
    with pytest.raises(ValueError):
        Attribute(key="k", value="v", grade=Grade.GIST,
                  confidence=Confidence(0.5), source_episode_ids=(), t_ms=0)


# ---- activation math (04 §2) ----
HL = 7 * 24 * 3600 * 1000

def test_decay_half_life():
    assert abs(activation_now(1.0, 0, HL) - 0.5) < 1e-9
    assert abs(activation_now(1.0, 0, 2 * HL) - 0.25) < 1e-9

def test_surprise_doubles_boost():
    calm = boost(0.0, 0, 0, "sighting", surprise=0.0)
    wow = boost(0.0, 0, 0, "sighting", surprise=1.0)
    assert abs(wow / calm - 2.0) < 1e-9

def test_boost_caps_at_one():
    assert boost(0.95, 0, 0, "sighting", surprise=1.0) == 1.0

def test_spread_is_one_hop_and_attenuated():
    deltas = spread_deltas(0.5, [("a", 2.0), ("b", 1.0)])
    assert set(deltas) == {"a", "b"}
    assert abs(deltas["a"] - 0.5 * 0.4 * 1.0) < 1e-9   # w/max_w = 1
    assert abs(deltas["b"] - 0.5 * 0.4 * 0.5) < 1e-9
    assert spread_deltas(0.5, []) == {}

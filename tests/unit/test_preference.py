"""Understand the person through their VOLITIONAL actions (analysis gap H).

Repeated CHOSEN engagement becomes a corrigible 'prefers X' hypothesis; repeated
IMPOSED engagement (the inbox guard) does not; no volition signal stays silent.
"""
from __future__ import annotations

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Node, NodeKind, Provenance, SpanCoord, WorldCoord, node_id,
)
from tracemem.domain.node import Attribute
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.preference import preference_pass


def _claim(text, frame, initiated):
    coord = CoordinateStack(
        world=WorldCoord(place_key="wifi:home"),
        capture=CaptureCoord.lived(frame_id=frame, device_id="mac-1"),
        span=SpanCoord(bbox=None), t_ms=1000)
    return Claim(
        text=text, coord=coord,
        provenance=Provenance("ocr", Modality.SCREEN),
        grade=Grade.VERBATIM, confidence=Confidence(0.9),
        structured={"initiated": initiated},
        bidder="t", bid_id="t:%s" % frame, bid_predicted_value=0.0,
        actual_cost=0.0, scope="frame")


def _target(node_id_str, label, episode_ids):
    return Node(id=node_id_str, kind=NodeKind.OBJECT, activation=0.5,
                last_active_t_ms=1000,
                attributes=[Attribute(key="object", value=label,
                                      grade=Grade.VERBATIM,
                                      confidence=Confidence(0.9),
                                      source_episode_ids=tuple(episode_ids),
                                      t_ms=1000)])


def test_repeated_chosen_engagement_becomes_a_preference():
    g = MemoryGraphStore()
    g.upsert_node(_target("obj:croissant", "croissant", ["e1", "e2", "e3"]))
    cbe = {ep: [_claim("croissant", ep, initiated=True)]
           for ep in ("e1", "e2", "e3")}
    minted = preference_pass(g, cbe, t_ms=2000)

    assert minted == [node_id("prefers", "croissant")]
    pref = g.get_node(minted[0])
    assert pref.kind is NodeKind.HYPOTHESIS           # a corrigible belief, not a fact
    assert "prefer croissant" in pref.attributes[0].value
    assert pref.attributes[0].grade < Grade.VERBATIM  # derived → one grade down
    assert set(pref.attributes[0].source_episode_ids) == {"e1", "e2", "e3"}  # cited
    assert pref.activation >= 0.6                     # born warm


def test_imposed_recurrence_is_not_a_preference():
    """Opening work mail every day is obligation, not love."""
    g = MemoryGraphStore()
    g.upsert_node(_target("obj:inbox", "inbox", ["e1", "e2", "e3", "e4", "e5"]))
    cbe = {ep: [_claim("inbox", ep, initiated=False)]
           for ep in ("e1", "e2", "e3", "e4", "e5")}
    assert preference_pass(g, cbe, t_ms=2000) == []


def test_no_volition_signal_stays_silent():
    g = MemoryGraphStore()
    g.upsert_node(_target("obj:thing", "thing", ["e1", "e2", "e3"]))
    coord = CoordinateStack(world=WorldCoord(), capture=CaptureCoord.lived("f", "d"),
                            span=SpanCoord(), t_ms=1)
    bare = {ep: [Claim(text="thing", coord=coord,
                       provenance=Provenance("ocr", Modality.SCREEN),
                       grade=Grade.VERBATIM, confidence=Confidence(0.9),
                       bidder="t", bid_id="t:%s" % ep, bid_predicted_value=0.0,
                       actual_cost=0.0, scope="frame")]           # no structured
            for ep in ("e1", "e2", "e3")}
    assert preference_pass(g, bare, t_ms=2000) == []


def test_a_single_choice_is_not_yet_a_preference():
    g = MemoryGraphStore()
    g.upsert_node(_target("obj:tea", "tea", ["e1", "e2", "e3"]))
    cbe = {"e1": [_claim("tea", "e1", initiated=True)],
           "e2": [_claim("tea", "e2", initiated=False)],
           "e3": [_claim("tea", "e3", initiated=False)]}          # 1 chosen, 2 imposed
    assert preference_pass(g, cbe, t_ms=2000) == []               # below MIN_CHOICES + ratio

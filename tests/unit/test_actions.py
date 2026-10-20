"""Verbs from change cadence — the actions producer (SCHEMA §1.9).

Closes the loop the preference derivation opened: cadence → verb → volition →
'prefers X'. Lexicon-free, conservative about calling an act chosen.
"""
from __future__ import annotations

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Node, NodeKind, Provenance, SpanCoord, WorldCoord, node_id,
)
from tracemem.domain.node import Attribute
from tracemem.extraction.actions import (
    infer_verbs, stamp_frame_volition, volition_from_verb, with_volition,
)
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.preference import preference_pass


def _c(text, t_ms, app, frame):
    coord = CoordinateStack(
        world=WorldCoord(place_key="wifi:home"),
        capture=CaptureCoord.lived(frame_id=frame, device_id="mac-1", app=app),
        span=SpanCoord(bbox=None), t_ms=t_ms)
    return Claim(text=text, coord=coord,
                 provenance=Provenance("ocr", Modality.SCREEN),
                 grade=Grade.VERBATIM, confidence=Confidence(0.9),
                 bidder="t", bid_id="t:%s" % frame, bid_predicted_value=0.0,
                 actual_cost=0.0, scope="frame")


def test_incremental_growth_is_writing_and_is_chosen():
    seq = [_c("draft", 1, "Notes", "f1"),
           _c("draft roadmap", 2, "Notes", "f2"),
           _c("draft roadmap milestones", 3, "Notes", "f3")]
    verbs = infer_verbs(seq)
    assert verbs[seq[1].id] == "writing"
    assert verbs[seq[2].id] == "writing"
    assert volition_from_verb("writing") is True


def test_wholesale_change_is_browsing_static_is_reading():
    browse = [_c("chess.com home page", 1, "Safari", "f1"),
              _c("wikipedia article about bread history", 2, "Safari", "f2")]
    assert infer_verbs(browse)[browse[1].id] == "browsing"

    still = [_c("a long article about cities", 1, "Reader", "f1"),
             _c("a long article about cities", 2, "Reader", "f2")]
    assert infer_verbs(still)[still[1].id] == "reading"
    assert volition_from_verb("reading") is None        # passive → unknown, not chosen


def test_with_volition_stamps_only_chosen_acts():
    seq = [_c("draft", 1, "Notes", "f1"),
           _c("draft plan", 2, "Notes", "f2")]          # writing
    stamped = with_volition(seq)
    assert stamped[1].structured["initiated"] is True
    assert stamped[0].structured is None                # first claim: reading → unstamped


def test_stamp_frame_volition_marks_a_whole_frame_from_the_cadence():
    """The pipeline seam: a frame whose content grew from the last is a chosen
    (writing) act; all its claims get stamped, a still frame's do not."""
    frame_claims = [_c("croissant recipe", 2, "Notes", "f2"),
                    _c("butter", 2, "Notes", "f2b")]
    grew = stamp_frame_volition(frame_claims, "croissant", "croissant recipe butter")
    assert all(c.structured["initiated"] is True for c in grew)

    still = stamp_frame_volition(frame_claims, "same text", "same text")
    assert all(c.structured is None for c in still)      # reading → unstamped


def test_cadence_feeds_preference_end_to_end():
    """The whole point: an actively-written-about target becomes a preference,
    with volition supplied by cadence rather than hand-set."""
    seq = [_c("croissant", 1, "Notes", "f1"),
           _c("croissant recipe", 2, "Notes", "f2"),
           _c("croissant recipe butter", 3, "Notes", "f3")]     # writing, writing
    stamped = with_volition(seq)

    g = MemoryGraphStore()
    g.upsert_node(Node(id="obj:croissant", kind=NodeKind.OBJECT, activation=0.5,
                       last_active_t_ms=3,
                       attributes=[Attribute(key="object", value="croissant",
                                             grade=Grade.VERBATIM,
                                             confidence=Confidence(0.9),
                                             source_episode_ids=("e1", "e2", "e3"),
                                             t_ms=3)]))
    # each writing claim is its own episode's evidence
    cbe = {"e1": [stamped[0]], "e2": [stamped[1]], "e3": [stamped[2]]}
    minted = preference_pass(g, cbe, t_ms=100, min_choices=2)
    assert minted == [node_id("prefers", "croissant")]

"""Deep OWNED mining (analysis gap E / commitment 2).

A file's body is read only where a node is HUNGRY, via the source pointer the
broad pass recorded — the facts a headline skipped, fetched from a source that
(unlike a discarded pixel) still exists.
"""
from __future__ import annotations

import os

from tracemem.domain import Confidence, Grade, Node, NodeKind, ProvenanceClass
from tracemem.domain.node import Attribute
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.owned.deep import deep_mine_owned, deep_read
from tracemem.owned.reader import _owned_claim

_BODY = "# Roadmap\n\nmet with Priya about the budget\ncall the bakery Monday\n-\n"


def _fixture(tmp_path):
    p = os.path.join(str(tmp_path), "notes.md")
    with open(p, "w") as f:
        f.write(_BODY)
    return os.path.abspath(p)


def _obj(label, episode, extra_attrs=()):
    attrs = [Attribute(key="object", value=label, grade=Grade.VERBATIM,
                       confidence=Confidence(0.9), source_episode_ids=(episode,),
                       t_ms=1000)]
    attrs += list(extra_attrs)
    return Node(id="obj:%s" % label, kind=NodeKind.OBJECT, attributes=attrs)


def _attr(key, value, episode="e1"):
    return Attribute(key=key, value=value, grade=Grade.VERBATIM,
                     confidence=Confidence(0.9), source_episode_ids=(episode,),
                     t_ms=1000)


def test_deep_read_extracts_the_body_the_headline_skipped(tmp_path):
    claims = deep_read(_fixture(tmp_path), device_id="mac-1")
    texts = {c.text for c in claims}
    assert "met with Priya about the budget" in texts
    assert "call the bakery Monday" in texts
    assert "-" not in texts                              # too short → ignored
    for c in claims:
        assert c.provenance.klass is ProvenanceClass.OWNED
        assert c.coord.capture.source and c.coord.capture.frame_id is None


def test_deep_mine_reads_the_source_of_a_hungry_node(tmp_path):
    src = _fixture(tmp_path)
    g = MemoryGraphStore()
    g.upsert_node(_obj("jar", "e1"))                     # missing color+located_at → hungry
    cbe = {"e1": [_owned_claim("Roadmap", src, "mac-1", 1000)]}

    new = deep_mine_owned(g, cbe, device_id="mac-1")
    assert any("Priya" in c.text for c in new)           # the body fact broad never saw
    assert all(c.provenance.klass is ProvenanceClass.OWNED for c in new)


def test_a_satisfied_node_does_not_trigger_a_deep_read(tmp_path):
    src = _fixture(tmp_path)
    g = MemoryGraphStore()
    # this object knows its color AND where it lives → agenda empty → not hungry
    g.upsert_node(_obj("jar", "e1", extra_attrs=[_attr("color", "red"),
                                                 _attr("located_at", "desk")]))
    cbe = {"e1": [_owned_claim("Roadmap", src, "mac-1", 1000)]}
    assert deep_mine_owned(g, cbe, device_id="mac-1") == []


def test_each_source_is_read_once_per_pass(tmp_path):
    src = _fixture(tmp_path)
    g = MemoryGraphStore()
    g.upsert_node(_obj("jar", "e1"))
    g.upsert_node(_obj("box", "e1"))                     # two hungry nodes, same source
    cbe = {"e1": [_owned_claim("Roadmap", src, "mac-1", 1000)]}

    new = deep_mine_owned(g, cbe, device_id="mac-1")
    # the body has 2 meaningful lines; read ONCE despite two hungry nodes
    priya = [c for c in new if "Priya" in c.text]
    assert len(priya) == 1

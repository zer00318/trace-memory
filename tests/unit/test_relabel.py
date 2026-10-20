"""Beliefs re-said in English — the first real re-marination.

F-RAW is the reason raw claims are kept for ever: "a node re-reading its own
old claims with grammars it did not have at the time". This is the first pass
that actually does it.

Until 2026-08-26 every recurrence belief rendered as
`recurring: key=value observed in N distinct moments`. Measured on the real
graph the morning after that was fixed: 190 beliefs, 112 sayable — so 78 read
like database rows and would for ever, because a new template only applies to
beliefs derived after it exists.
"""
from __future__ import annotations

from tracemem.domain import Confidence, Grade
from tracemem.domain.grading import retracted
from tracemem.domain.node import Attribute, Node, NodeKind
from tracemem.nodes.marination.relabel import parse_old, relabel_pass

NOW = 1_700_000_000_000


def _attr(value):
    return Attribute(key="claim", value=value, grade=Grade.DERIVED,
                     confidence=Confidence(0.7),
                     source_episode_ids=("ep1",), t_ms=NOW)


def _belief(nid, *values):
    return Node(id=nid, kind=NodeKind.PATTERN, derivation_depth=1,
                parent_ids=("p1",), attributes=[_attr(v) for v in values])


class Store:
    def __init__(self, nodes):
        self.nodes = {n.id: n for n in nodes}
        self.retired_ids = []

    def upsert_node(self, n):
        self.nodes[n.id] = n

    def retire_meta(self, node_id, t_ms):
        self.retired_ids.append(node_id)


# -- reading the old format -----------------------------------------------

def test_it_reads_the_old_format_back():
    assert parse_old("recurring: site=lichess.org observed in 9 distinct "
                     "moments") == ("site", "lichess.org", 9)


def test_it_refuses_anything_that_is_not_exactly_the_old_format():
    """A belief that merely CONTAINS something like this — a founder
    correction quoting one, say — must not be rewritten."""
    assert parse_old("you keep going back to lichess.org") is None
    assert parse_old("he said recurring: site=x observed in 9 distinct "
                     "moments yesterday") is None
    assert parse_old("") is None
    assert parse_old(None) is None


# -- re-saying -------------------------------------------------------------

def test_a_sayable_belief_is_re_said():
    """Same evidence, same citations, same node — only the sentence changes,
    because only the sentence was ever wrong."""
    store = Store([_belief("b1", "recurring: site=lichess.org observed in 9 "
                                 "distinct moments")])
    out = relabel_pass(store, NOW)
    assert out["resaid"] == ["you keep going back to lichess.org — in 9 moments"]
    assert store.nodes["b1"].attributes[0].value.startswith("you keep going")


def test_the_evidence_is_untouched():
    store = Store([_belief("b1", "recurring: app=Brave observed in 4 distinct "
                                 "moments")])
    before = store.nodes["b1"].attributes[0].source_episode_ids
    relabel_pass(store, NOW)
    assert store.nodes["b1"].attributes[0].source_episode_ids == before


def test_an_already_english_belief_is_left_alone():
    store = Store([_belief("b1", "you keep going back to lichess.org")])
    out = relabel_pass(store, NOW)
    assert out["resaid"] == [] and store.retired_ids == []


# -- retiring what cannot be said -----------------------------------------

def test_an_unsayable_belief_is_retired_not_deleted():
    """`page=/watch` is a URL path; there is no phrasing that makes it a fact
    about a life, and the current detector would not mint it at all. It goes
    to the moat like any belief that died, so "what changed my mind" can
    still show it."""
    store = Store([_belief("b1", "recurring: page=/watch observed in 230 "
                                 "distinct moments")])
    out = relabel_pass(store, NOW)
    assert store.retired_ids == ["b1"]
    assert out["retired"] == ["b1"]
    assert "b1" in store.nodes, "it was deleted rather than retired"


def test_a_belief_with_one_sayable_line_survives():
    """Retiring a node because ONE of its statements is unsayable would throw
    away the ones that are fine."""
    store = Store([_belief("b1",
                           "recurring: page=/watch observed in 9 distinct moments",
                           "recurring: site=lichess.org observed in 9 distinct moments")])
    relabel_pass(store, NOW)
    assert store.retired_ids == []
    values = [a.value for a in store.nodes["b1"].attributes]
    assert any(v.startswith("you keep going back") for v in values)


# -- it never invents ------------------------------------------------------

def test_an_unparseable_belief_is_left_exactly_as_it_is():
    """Leaving one ugly sentence is a smaller harm than rewriting a person's
    memory from a guess."""
    odd = "recurring: something the regex has never seen"
    store = Store([_belief("b1", odd)])
    relabel_pass(store, NOW)
    assert store.nodes["b1"].attributes[0].value == odd
    assert store.retired_ids == []


def test_a_retracted_line_is_not_resurrected():
    store = Store([_belief("b1", "recurring: site=x.com observed in 3 distinct "
                                 "moments")])
    store.nodes["b1"].attributes[0].grade = retracted()
    out = relabel_pass(store, NOW)
    assert out["resaid"] == []


def test_entity_nodes_are_not_touched():
    """This is about BELIEFS. An entity's own name is not a derived claim."""
    n = Node(id="e1", kind=NodeKind.ENTITY,
             attributes=[_attr("recurring: site=x.com observed in 3 distinct "
                               "moments")])
    store = Store([n])
    out = relabel_pass(store, NOW)
    assert out["scanned"] == 0 and out["resaid"] == []


def test_a_broken_store_does_not_take_the_night_down():
    class Angry:
        @property
        def nodes(self):
            raise RuntimeError("graph on fire")

    assert relabel_pass(Angry(), NOW)["scanned"] == 0

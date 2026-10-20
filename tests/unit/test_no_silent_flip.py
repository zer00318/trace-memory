"""A belief that changes leaves a trail, and no API erases it.

`The living cell / Belief revision / No-silent-flip guard`, its oracle: a
flip grows the trail, and no API path erases it.

This is the never-lie promise pointed at the machine's own past rather than
at the founder's. A memory that quietly replaced what it used to think would
be indistinguishable, from the outside, from one that had always thought the
new thing — and "it has always said that" is the most dangerous sentence a
memory can make true after the fact.
"""
from __future__ import annotations

import ast
import pathlib

from tracemem.domain import Confidence, Grade
from tracemem.domain.grading import retracted
from tracemem.domain.node import Node, NodeKind
from tracemem.nodes.accrual import accrue, label_of

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _node():
    return Node(id="n1", kind=NodeKind.ENTITY)


def test_a_flip_grows_the_trail():
    """The old value is still there afterwards. That is the whole property."""
    n = _node()
    accrue(n, "employer", "Acme", Grade.VERBATIM, Confidence(0.9), "ep1", 1000)
    accrue(n, "employer", "Globex", Grade.VERBATIM, Confidence(0.9), "ep2",
           2000)

    values = [a.value for a in n.attributes if a.key == "employer"]
    assert values == ["Acme", "Globex"], \
        "the old belief was overwritten instead of joined"


def test_the_newest_best_evidence_speaks_for_the_node():
    """Keeping the trail must not mean answering with the stale value."""
    n = _node()
    accrue(n, "name", "Sam", Grade.GIST, Confidence(0.5), "ep1", 1000)
    accrue(n, "name", "Samantha", Grade.VERBATIM, Confidence(0.9), "ep2", 2000)
    assert label_of(n) == "Samantha"
    assert len(n.attributes) == 2, "the trail was trimmed"


def test_re_seeing_the_same_thing_is_not_a_flip():
    """Otherwise every repeated observation would read as a change of mind
    and the trail would say nothing."""
    n = _node()
    accrue(n, "name", "Sam", Grade.VERBATIM, Confidence(0.9), "ep1", 1000)
    accrue(n, "name", "Sam", Grade.VERBATIM, Confidence(0.9), "ep2", 2000)
    assert len(n.attributes) == 1
    assert n.attributes[0].source_episode_ids == ("ep1", "ep2"), \
        "the second sighting was not recorded at all"


def test_a_retraction_demotes_and_never_deletes():
    """The one place that used to erase. See nodes/birth/person_judge."""
    n = _node()
    accrue(n, "seen_as", "Hana", Grade.VERBATIM, Confidence(0.9), "ep1", 1)
    accrue(n, "seen_as", "Trash", Grade.VERBATIM, Confidence(0.9), "ep1", 1)
    for a in n.attributes:
        if a.value == "Trash":
            a.grade = retracted()
            a.confidence = Confidence(0.0)

    assert len(n.attributes) == 2, "a retraction removed evidence"
    assert label_of(n) == "Hana", "a retracted value spoke for the node"


def test_no_module_erases_a_nodes_attributes():
    """THE GREP HALF OF THE ORACLE: no API path erases the trail.

    `person_judge.enforce` used to rebuild the attribute list without the
    rejected entries. It was the only such path in the system, and what it
    dropped was a judgement about a person's NAME — so if the judgement was
    wrong, the founder had no way to see it had ever been made.
    """
    offenders = []
    for f in (ROOT / "src").rglob("*.py"):
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # `x.attributes = ...` — rebinding the whole list is how a trail
            # disappears without anything looking like a delete.
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "attributes":
                        offenders.append("%s:%d" % (f.relative_to(ROOT),
                                                    node.lineno))
            # `.attributes.remove(...)` / `.pop(...)` / `.clear()`
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("remove", "pop", "clear")
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "attributes"):
                offenders.append("%s:%d" % (f.relative_to(ROOT), node.lineno))
    assert offenders == [], \
        "these erase a node's trail rather than demoting: %r" % offenders
